import asyncio
import re
import secrets
from contextlib import asynccontextmanager

from alpaca.common.enums import Sort
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.config import reload_settings, settings
from app.models import (
    AppSettingsResponse,
    AppSettingsUpdate,
    AutoScannerRequest,
    BacktestRequest,
    BankrollReturnRequest,
    BankrollStatus,
    BankrollWithdrawRequest,
    BotStatus,
    DashboardAuthUpdate,
    ScannerRequest,
    TradeRequest,
)
from app.paths import resource_path
from app.services import bankroll, trade_log, trade_sync
from app.services.backtest import run_backtest
from app.services.basic_auth import BasicAuthMiddleware
from app.services.bot import bot
from app.services.env_file import InvalidEnvValue, mask_secret, read_env, write_env
from app.services.fmp import FmpClient
from app.services.scanner import MarketScanner


def _sync_orders_headless() -> None:
    """Server-side fill/exit reconciliation - the dashboard's own periodic sync only
    happens while a browser has the page open, and the bankroll gate plus walk-away
    rules read directly from what this records. Skipping when the broker isn't
    configured (or a sync hiccups) is fine; the next cycle retries."""
    try:
        trade_sync.sync_orders(AlpacaBroker())
    except Exception:
        pass


async def _automation_loop() -> None:
    """Runs for the life of the server. Order syncing and manage_open_positions()
    always run - fills/exits must reconcile and open positions stay protected whether
    or not new-entry auto-trading is switched on - while auto_cycle() (new entries) is
    a no-op unless auto-trading is enabled."""
    while True:
        try:
            await asyncio.to_thread(_sync_orders_headless)
            await asyncio.to_thread(bot.manage_open_positions)
            if bot.status.auto_trading_enabled:
                await asyncio.to_thread(bot.auto_cycle)
        except Exception:
            pass  # a single bad cycle should never kill the loop
        await asyncio.sleep(settings.automation_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_automation_loop())
    yield
    task.cancel()


app = FastAPI(title="Rolling Papers Bot", version="0.1.0", lifespan=lifespan)
app.add_middleware(BasicAuthMiddleware)
app.mount("/static", StaticFiles(directory=resource_path("static")), name="static")
scanner = MarketScanner()
MAX_BARS_LIMIT = 5000

# Longer-range chart presets: (bar timeframe, lookback in calendar days). YTD is handled separately.
RANGE_PRESETS: dict[str, tuple[TimeFrame, int]] = {
    "1M": (TimeFrame.Day, 45),
    "6M": (TimeFrame.Day, 200),
    "1Y": (TimeFrame.Day, 400),
    "5Y": (TimeFrame(1, TimeFrameUnit.Week), 5 * 365 + 30),
    "10Y": (TimeFrame(1, TimeFrameUnit.Week), 10 * 365 + 30),
    "ALL": (TimeFrame(1, TimeFrameUnit.Month), 25 * 365),
}


def _enum_str(value) -> str:
    """Alpaca SDK enums (order status, order type) stringify via .value; plain strings pass through."""
    return str(value.value if hasattr(value, "value") else value)


# Cache-busts /static/* references in index.html so a CDN/edge cache (e.g. a
# Cloudflare Tunnel, which caches .css/.js by file extension regardless of
# origin headers) can't keep serving a stale asset after a deploy — the query
# string changes every process start, which every deploy path already forces
# via `systemctl restart`.
_STATIC_VERSION = str(int(datetime.now(UTC).timestamp()))
_STATIC_HREF_PATTERN = re.compile(r'"(/static/[^"]+)"')


def _render_index_html() -> str:
    html = resource_path("static/index.html").read_text(encoding="utf-8")
    return _STATIC_HREF_PATTERN.sub(lambda m: f'"{m.group(1)}?v={_STATIC_VERSION}"', html)


_INDEX_HTML = _render_index_html()

_API_KEY_LABEL_PREFIX = re.compile(r"(?i)^(api[_ -]?key|secret([_ -]?key)?)\s*[:=]\s*")


def _clean_api_key(value: str) -> str:
    """Strip paste artifacts like a leading 'apikey:' label or surrounding quotes — people often
    copy the whole 'label: value' line straight from a provider's dashboard instead of just the key."""
    value = value.strip().strip("'\"")
    value = _API_KEY_LABEL_PREFIX.sub("", value)
    return value.strip().strip("'\"")


def resolve_period(period: str, now: datetime) -> tuple[TimeFrame, datetime]:
    period = period.upper()
    if period == "YTD":
        return TimeFrame.Day, datetime(now.year, 1, 1, tzinfo=UTC)
    if period not in RANGE_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown period '{period}'. Use one of: YTD, {', '.join(RANGE_PRESETS)}.")
    timeframe, lookback_days = RANGE_PRESETS[period]
    return timeframe, now - timedelta(days=lookback_days)


@app.get("/")
def index():
    return HTMLResponse(_INDEX_HTML)


@app.get("/api/status", response_model=BotStatus)
def status():
    return bot.refresh_status()


@app.get("/api/settings", response_model=AppSettingsResponse)
def get_settings():
    values = read_env()
    return AppSettingsResponse(
        alpaca_api_key=mask_secret(values.get("ALPACA_API_KEY", "")),
        alpaca_secret_key=mask_secret(values.get("ALPACA_SECRET_KEY", "")),
        alpaca_paper=values.get("ALPACA_PAPER", "true").lower() == "true",
        fmp_api_key=mask_secret(values.get("FMP_API_KEY", "")),
        allow_live_trading=values.get("ALLOW_LIVE_TRADING", "false").lower() == "true",
        # Never the password - write-only, only ever set via
        # update_dashboard_auth(), never echoed back once configured.
        dashboard_username=values.get("DASHBOARD_USERNAME", ""),
    )


@app.post("/api/settings", response_model=AppSettingsResponse)
def save_settings(request: AppSettingsUpdate):
    updates = {
        "ALPACA_PAPER": str(request.alpaca_paper).lower(),
        "ALLOW_LIVE_TRADING": str(request.allow_live_trading).lower(),
    }
    if request.alpaca_api_key and "..." not in request.alpaca_api_key:
        updates["ALPACA_API_KEY"] = _clean_api_key(request.alpaca_api_key)
    if request.alpaca_secret_key and "..." not in request.alpaca_secret_key:
        updates["ALPACA_SECRET_KEY"] = _clean_api_key(request.alpaca_secret_key)
    if request.fmp_api_key and "..." not in request.fmp_api_key:
        updates["FMP_API_KEY"] = _clean_api_key(request.fmp_api_key)

    try:
        write_env(updates)
    except InvalidEnvValue as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reload_settings()
    return get_settings()


@app.post("/api/settings/dashboard-auth", response_model=AppSettingsResponse)
def update_dashboard_auth(request: DashboardAuthUpdate):
    # BasicAuthMiddleware already requires valid credentials to reach this
    # route at all once auth is on - this check is defense in depth on top
    # of that (e.g. a browser tab left logged in), and the only way to set
    # up auth for the first time when it's currently off.
    if settings.dashboard_username:
        if not secrets.compare_digest(request.current_password, settings.dashboard_password):
            raise HTTPException(status_code=401, detail="Current password is incorrect.")

    new_username = request.new_username.strip()
    if not new_username:
        raise HTTPException(status_code=400, detail="Username can't be empty.")
    if len(request.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")

    try:
        write_env({"DASHBOARD_USERNAME": new_username, "DASHBOARD_PASSWORD": request.new_password})
    except InvalidEnvValue as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reload_settings()
    return get_settings()


def _bankroll_status() -> BankrollStatus:
    balance = bankroll.current_bankroll()
    savings_balance = None
    savings_unavailable_reason = None
    try:
        account_equity = float(AlpacaBroker().account().equity)
    except BrokerUnavailable as exc:
        savings_unavailable_reason = str(exc)
    except Exception as exc:
        savings_unavailable_reason = f"Could not fetch Alpaca account: {exc}"
    else:
        savings_balance = round(account_equity - balance, 2)

    return BankrollStatus(
        bankroll_balance=balance,
        deployed_capital=bankroll.deployed_capital(),
        available_to_trade=bankroll.available_to_trade(),
        realized_pnl=round(bankroll.realized_pnl(), 2),
        savings_balance=savings_balance,
        savings_unavailable_reason=savings_unavailable_reason,
        transactions=bankroll.transactions(limit=20),
    )


@app.get("/api/bankroll", response_model=BankrollStatus)
def get_bankroll():
    return _bankroll_status()


@app.post("/api/bankroll/withdraw", response_model=BankrollStatus)
def withdraw_bankroll(request: BankrollWithdrawRequest):
    try:
        account_equity = float(AlpacaBroker().account().equity)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca account: {exc}") from exc

    try:
        bankroll.record_withdrawal(request.amount, account_equity, note=request.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _bankroll_status()


@app.post("/api/bankroll/return", response_model=BankrollStatus)
def return_bankroll(request: BankrollReturnRequest):
    try:
        bankroll.record_return_to_savings(request.amount, note=request.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _bankroll_status()


@app.get("/api/settings/test")
def test_settings():
    try:
        account = AlpacaBroker().account()
    except BrokerUnavailable as exc:
        alpaca_result = {"configured": False, "ok": False, "detail": str(exc)}
    except Exception as exc:
        alpaca_result = {"configured": True, "ok": False, "detail": f"Alpaca rejected the request: {exc}"}
    else:
        alpaca_result = {"configured": True, "ok": True, "detail": f"Connected — account status: {_enum_str(account.status)}"}

    fmp = FmpClient()
    if not fmp.configured:
        fmp_result = {"configured": False, "ok": False, "detail": "No FMP key configured (optional — float data falls back to the local list)"}
    else:
        try:
            fmp.shares_float("AAPL", use_cache=False)
        except Exception as exc:
            fmp_result = {"configured": True, "ok": False, "detail": str(exc)}
        else:
            fmp_result = {"configured": True, "ok": True, "detail": "Connected"}

    return {"alpaca": alpaca_result, "fmp": fmp_result}


@app.post("/api/start", response_model=BotStatus)
def start():
    return bot.start()


@app.post("/api/stop", response_model=BotStatus)
def stop():
    return bot.stop()


@app.post("/api/tick", response_model=BotStatus)
def tick():
    return bot.tick()


@app.post("/api/automation/start", response_model=BotStatus)
def start_automation():
    return bot.start_auto_trading()


@app.post("/api/automation/stop", response_model=BotStatus)
def stop_automation():
    return bot.stop_auto_trading()


@app.post("/api/scanner")
def scan_market(request: ScannerRequest):
    try:
        return scanner.scan(request.symbols)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not scan market: {exc}") from exc


@app.post("/api/scanner/auto")
def scan_market_universe(request: AutoScannerRequest):
    try:
        return scanner.scan_universe(limit=request.limit, max_symbols=request.max_symbols)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not scan market universe: {exc}") from exc


@app.get("/api/account")
def account():
    try:
        acct = AlpacaBroker().account()
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca account: {exc}") from exc
    return {
        "id": acct.id,
        "status": acct.status,
        "currency": acct.currency,
        "buying_power": acct.buying_power,
        "equity": acct.equity,
        "paper": bot.status.paper,
    }


@app.get("/api/positions")
def positions():
    try:
        return {"positions": AlpacaBroker().positions_as_dicts()}
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca positions: {exc}") from exc


@app.get("/api/portfolio/history")
def portfolio_history(period: str = "1D", timeframe: str = "5Min"):
    try:
        return AlpacaBroker().portfolio_history(period=period, timeframe=timeframe)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca portfolio history: {exc}") from exc


@app.get("/api/trades/history")
def trade_history(limit: int = 50):
    return {"trades": trade_log.list_trades(limit=min(max(limit, 1), 500))}


@app.post("/api/trades/history/sync")
def sync_trade_history():
    try:
        broker = AlpacaBroker()
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trade_sync.sync_orders(broker)
    return {"trades": trade_log.list_trades(limit=50)}


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    try:
        return AlpacaBroker().latest_quote(symbol)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca quote for {symbol.upper()}: {exc}") from exc


@app.get("/api/bars/{symbol}")
def bars(symbol: str, limit: int = 120, trading_date: date | None = None, days: int | None = None, period: str | None = None):
    try:
        sort = Sort.ASC
        timeframe = TimeFrame.Minute
        if trading_date:
            market_tz = ZoneInfo("America/New_York")
            start = datetime.combine(trading_date, time(9, 30), tzinfo=market_tz).astimezone(UTC)
            end = datetime.combine(trading_date, time(16, 0), tzinfo=market_tz).astimezone(UTC)
            limit = min(max(limit, 1), 390)
        elif period:
            end = datetime.now(UTC)
            timeframe, start = resolve_period(period, end)
            limit = MAX_BARS_LIMIT
        elif days:
            days = min(max(days, 1), 30)
            end = datetime.now(UTC)
            start = end - timedelta(days=days + 5)
            limit = min(max(limit, 1), days * 390, MAX_BARS_LIMIT)
            sort = Sort.DESC
        else:
            end = datetime.now(UTC)
            start = end - timedelta(days=7)
        return {
            "symbol": symbol.upper(),
            "bars": AlpacaBroker().historical_bars(symbol, start=start, end=end, limit=limit, sort=sort, timeframe=timeframe),
        }
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca bars for {symbol.upper()}: {exc}") from exc


@app.post("/api/trade")
def trade(request: TradeRequest):
    try:
        return bot.submit_trade(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/backtest")
def backtest(request: BacktestRequest):
    try:
        return run_backtest(
            request.symbol,
            request.start,
            request.end,
            starting_capital=request.starting_capital,
            position_value=request.position_value,
        )
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Backtest failed: {exc}") from exc
