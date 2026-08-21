import asyncio
import re
from contextlib import asynccontextmanager

from alpaca.common.enums import Sort
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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
    BotStatus,
    ScannerRequest,
    TradeRequest,
)
from app.paths import resource_path
from app.services import trade_log
from app.services.backtest import run_backtest
from app.services.basic_auth import BasicAuthMiddleware
from app.services.bot import bot
from app.services.env_file import mask_secret, read_env, write_env
from app.services.fmp import FmpClient
from app.services.scanner import MarketScanner


async def _automation_loop() -> None:
    """Runs for the life of the server; each pass is a no-op unless auto-trading is on."""
    while True:
        try:
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
    return FileResponse(resource_path("static/index.html"))


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

    write_env(updates)
    reload_settings()
    return get_settings()


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

    for order_id in trade_log.pending_order_ids():
        try:
            order = broker.get_order(order_id)
        except Exception:
            continue
        trade_log.update_fill(
            order_id=order_id,
            status=_enum_str(order.status),
            filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price is not None else None,
            filled_qty=float(order.filled_qty) if order.filled_qty is not None else None,
            filled_at=order.filled_at.isoformat() if order.filled_at is not None else None,
        )

    for trade in trade_log.trades_awaiting_exit():
        try:
            order = broker.get_order(trade["order_id"])
        except Exception:
            continue
        filled_leg = next(
            (leg for leg in (order.legs or []) if _enum_str(leg.status) == "filled"),
            None,
        )
        if filled_leg is None or filled_leg.filled_avg_price is None:
            continue
        exit_reason = "target" if _enum_str(filled_leg.order_type) == "limit" else "stop"
        exit_price = float(filled_leg.filled_avg_price)
        exit_qty = float(filled_leg.filled_qty) if filled_leg.filled_qty is not None else trade["qty"]
        entry_price = trade["filled_avg_price"] or 0.0
        pnl = (exit_price - entry_price) * exit_qty if trade["side"] == "buy" else (entry_price - exit_price) * exit_qty
        trade_log.record_exit(
            order_id=trade["order_id"],
            exit_order_id=str(filled_leg.id),
            exit_price=exit_price,
            exit_qty=exit_qty,
            exit_at=filled_leg.filled_at.isoformat() if filled_leg.filled_at is not None else None,
            exit_reason=exit_reason,
            realized_pnl=round(pnl, 2),
        )

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
