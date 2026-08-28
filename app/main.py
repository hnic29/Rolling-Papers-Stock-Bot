import asyncio
import re
from contextlib import asynccontextmanager

from alpaca.common.enums import Sort
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.config import settings
from app.models import (
    AppSettingsResponse,
    AppSettingsUpdate,
    AutoScannerRequest,
    BacktestRequest,
    BankrollReturnRequest,
    BankrollStatus,
    BankrollWithdrawRequest,
    BootstrapRequest,
    BotStatus,
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    ResetPasswordRequest,
    ScannerRequest,
    TradeRequest,
    UserListItem,
    UserPublic,
)
from app.paths import resource_path
from app.services import bankroll, bot_registry, credentials, notify, trade_log, trade_sync, users
from app.services.backtest import run_daily_backtest
from app.services.env_file import mask_secret
from app.services.finnhub_live import stream_trades
from app.services.fmp import FmpClient
from app.services.scanner import MarketScanner
from app.services.session_auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    SessionAuthMiddleware,
    create_session_token,
    resolve_optional_user_id,
    verify_session_token,
)


def _sync_orders_headless(user_id: int, ntfy_topic: str) -> None:
    """Server-side fill/exit reconciliation for one user - the dashboard's own
    periodic sync only happens while a browser has the page open, and the bankroll
    gate plus walk-away rules read directly from what this records. Skipping when the
    broker isn't configured (or a sync hiccups) is fine; the next cycle retries."""
    try:
        trade_sync.sync_orders(AlpacaBroker.for_user(user_id), user_id=user_id, ntfy_topic=ntfy_topic)
    except Exception:
        pass


async def _automation_loop() -> None:
    """Runs for the life of the server, once per registered user each cycle
    (sequentially - simpler than one task per user, and naturally throttles how many
    Alpaca API calls fire at once for a handful of family/friends). Order syncing and
    manage_open_positions() always run for every user - fills/exits must reconcile and
    open positions stay protected whether or not new-entry auto-trading is switched on
    - while auto_cycle() (new entries) is a no-op unless that user has it enabled. One
    user's cycle failing never blocks another's - each gets its own try/except."""
    while True:
        for user in users.list_users():
            user_id = user["id"]
            try:
                user_settings = credentials.get_credentials(user_id)
                bot = bot_registry.get_bot(user_id)
                await asyncio.to_thread(_sync_orders_headless, user_id, user_settings["ntfy_topic"])
                await asyncio.to_thread(bot.check_market_open_close_notifications)
                await asyncio.to_thread(bot.manage_open_positions)
                if bot.status.auto_trading_enabled:
                    await asyncio.to_thread(bot.auto_cycle)
            except Exception:
                pass  # a single bad cycle for one user should never kill the loop or block anyone else
        await asyncio.sleep(settings.automation_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    users.migrate_legacy_dashboard_credentials()
    credentials.migrate_legacy_settings(1)
    task = asyncio.create_task(_automation_loop())
    yield
    task.cancel()


app = FastAPI(title="Rolling Papers Bot", version="0.1.0", lifespan=lifespan)
app.add_middleware(SessionAuthMiddleware)
app.mount("/static", StaticFiles(directory=resource_path("static")), name="static")
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


@app.get("/api/status")
def status(request: Request):
    """Left public (see SessionAuthMiddleware's _PUBLIC_PATHS) so an external monitor
    can ping this with no session at all - it just gets a bare "ok" then. The logged-in
    dashboard hits this exact same path with its session cookie already attached and
    gets its own bot's real status back."""
    user_id = resolve_optional_user_id(request)
    if user_id is None:
        return {"status": "ok"}
    return bot_registry.get_bot(user_id).refresh_status()


@app.get("/api/settings", response_model=AppSettingsResponse)
def get_settings(request: Request):
    creds = credentials.get_credentials(request.state.user_id)
    return AppSettingsResponse(
        alpaca_api_key=mask_secret(creds["alpaca_api_key"]),
        alpaca_secret_key=mask_secret(creds["alpaca_secret_key"]),
        alpaca_paper=creds["alpaca_paper"],
        fmp_api_key=mask_secret(creds["fmp_api_key"]),
        finnhub_api_key=mask_secret(creds["finnhub_api_key"]),
        allow_live_trading=creds["allow_live_trading"],
        ntfy_topic=creds["ntfy_topic"],
    )


@app.post("/api/settings", response_model=AppSettingsResponse)
def save_settings(body: AppSettingsUpdate, request: Request):
    arming_live = not body.alpaca_paper and body.allow_live_trading
    if arming_live and not body.confirm_live_trading:
        raise HTTPException(
            status_code=400,
            detail=(
                "This would enable LIVE trading with real money — the bot could place real "
                "orders within a couple of minutes. Confirm explicitly to proceed."
            ),
        )

    user_id = request.state.user_id
    previous_topic = credentials.get_credentials(user_id)["ntfy_topic"]
    updates = {
        "alpaca_paper": body.alpaca_paper,
        "allow_live_trading": body.allow_live_trading,
        "ntfy_topic": body.ntfy_topic.strip(),
    }
    if body.alpaca_api_key and "..." not in body.alpaca_api_key:
        updates["alpaca_api_key"] = _clean_api_key(body.alpaca_api_key)
    if body.alpaca_secret_key and "..." not in body.alpaca_secret_key:
        updates["alpaca_secret_key"] = _clean_api_key(body.alpaca_secret_key)
    if body.fmp_api_key and "..." not in body.fmp_api_key:
        updates["fmp_api_key"] = _clean_api_key(body.fmp_api_key)
    if body.finnhub_api_key and "..." not in body.finnhub_api_key:
        updates["finnhub_api_key"] = _clean_api_key(body.finnhub_api_key)

    credentials.save_credentials(user_id, **updates)

    # Immediate feedback on the phone that the topic actually works - the alternative
    # is discovering a typo'd topic days later by missing a real trade alert.
    if updates["ntfy_topic"] and updates["ntfy_topic"] != previous_topic:
        notify.send(
            updates["ntfy_topic"],
            "Notifications connected",
            "Rolling Papers Bot will push trade opens, exits with P&L, and walk-aways here.",
            tags="white_check_mark",
        )

    return get_settings(request)


def _set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(user_id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )


@app.get("/api/auth/status")
def auth_status():
    """Public: tells the dashboard whether to show the first-run "create an admin
    account" screen or a normal login screen."""
    return {"needs_bootstrap": users.count_users() == 0}


@app.post("/api/bootstrap", response_model=UserPublic)
def bootstrap(request: BootstrapRequest, response: Response):
    if users.count_users() > 0:
        raise HTTPException(status_code=403, detail="Setup has already been completed — log in instead.")
    username = request.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username can't be empty.")
    if len(request.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    user = users.create_user(username, request.password, is_admin=True)
    _set_session_cookie(response, user["id"])
    return UserPublic(id=user["id"], username=user["username"], is_admin=True)


@app.post("/api/login", response_model=UserPublic)
def login(request: LoginRequest, response: Response):
    user = users.verify_password(request.username.strip(), request.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    _set_session_cookie(response, user["id"])
    return UserPublic(id=user["id"], username=user["username"], is_admin=bool(user["is_admin"]))


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"message": "Logged out."}


@app.get("/api/me", response_model=UserPublic)
def me(request: Request):
    return UserPublic(id=request.state.user_id, username=request.state.username, is_admin=request.state.is_admin)


@app.post("/api/me/password")
def change_my_password(request: Request, body: ChangePasswordRequest):
    user = users.get_user_by_id(request.state.user_id)
    if users.verify_password(user["username"], body.current_password) is None:
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")
    users.set_password(request.state.user_id, body.new_password)
    return {"message": "Password updated."}


def _require_admin(request: Request) -> None:
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")


def _user_list_item(user: dict) -> UserListItem:
    return UserListItem(id=user["id"], username=user["username"], is_admin=bool(user["is_admin"]), created_at=user["created_at"])


@app.get("/api/users", response_model=list[UserListItem])
def list_users_route(request: Request):
    _require_admin(request)
    return [_user_list_item(user) for user in users.list_users()]


@app.post("/api/users", response_model=UserListItem)
def create_user_route(body: CreateUserRequest, request: Request):
    """Admin-provisioned account creation - each person gets their own login and,
    once they save it on their own Settings page, their own Alpaca account. There's
    no public registration route; only an existing admin can add someone."""
    _require_admin(request)
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username can't be empty.")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    try:
        user = users.create_user(username, body.password, is_admin=body.is_admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _user_list_item(user)


@app.post("/api/users/{user_id}/reset-password")
def reset_user_password(user_id: int, body: ResetPasswordRequest, request: Request):
    _require_admin(request)
    if users.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")
    users.set_password(user_id, body.new_password)
    return {"message": "Password reset."}


def _bankroll_status(user_id: int) -> BankrollStatus:
    balance = bankroll.current_bankroll(user_id)
    savings_balance = None
    savings_unavailable_reason = None
    try:
        account_equity = float(AlpacaBroker.for_user(user_id).account().equity)
    except BrokerUnavailable as exc:
        savings_unavailable_reason = str(exc)
    except Exception as exc:
        savings_unavailable_reason = f"Could not fetch Alpaca account: {exc}"
    else:
        savings_balance = round(account_equity - balance, 2)

    return BankrollStatus(
        bankroll_balance=balance,
        deployed_capital=bankroll.deployed_capital(user_id),
        available_to_trade=bankroll.available_to_trade(user_id),
        realized_pnl=round(bankroll.realized_pnl(user_id), 2),
        savings_balance=savings_balance,
        savings_unavailable_reason=savings_unavailable_reason,
        transactions=bankroll.transactions(limit=20, user_id=user_id),
    )


@app.get("/api/bankroll", response_model=BankrollStatus)
def get_bankroll(request: Request):
    return _bankroll_status(request.state.user_id)


@app.post("/api/bankroll/withdraw", response_model=BankrollStatus)
def withdraw_bankroll(body: BankrollWithdrawRequest, request: Request):
    user_id = request.state.user_id
    try:
        account_equity = float(AlpacaBroker.for_user(user_id).account().equity)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca account: {exc}") from exc

    try:
        bankroll.record_withdrawal(body.amount, account_equity, note=body.note, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _bankroll_status(user_id)


@app.post("/api/bankroll/return", response_model=BankrollStatus)
def return_bankroll(body: BankrollReturnRequest, request: Request):
    user_id = request.state.user_id
    try:
        bankroll.record_return_to_savings(body.amount, note=body.note, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _bankroll_status(user_id)


@app.get("/api/settings/test")
def test_settings(request: Request):
    try:
        account = AlpacaBroker.for_user(request.state.user_id).account()
    except BrokerUnavailable as exc:
        alpaca_result = {"configured": False, "ok": False, "detail": str(exc)}
    except Exception as exc:
        alpaca_result = {"configured": True, "ok": False, "detail": f"Alpaca rejected the request: {exc}"}
    else:
        alpaca_result = {"configured": True, "ok": True, "detail": f"Connected — account status: {_enum_str(account.status)}"}

    fmp = FmpClient()
    if not fmp.configured:
        fmp_result = {"configured": False, "ok": False, "detail": "No FMP key configured (optional — float data falls back to Yahoo Finance, then the local list)"}
    else:
        try:
            fmp.shares_float("AAPL", use_cache=False)
        except Exception as exc:
            fmp_result = {"configured": True, "ok": False, "detail": str(exc)}
        else:
            fmp_result = {"configured": True, "ok": True, "detail": "Connected"}

    return {"alpaca": alpaca_result, "fmp": fmp_result}


@app.post("/api/start", response_model=BotStatus)
def start(request: Request):
    return bot_registry.get_bot(request.state.user_id).start()


@app.post("/api/stop", response_model=BotStatus)
def stop(request: Request):
    return bot_registry.get_bot(request.state.user_id).stop()


@app.post("/api/tick", response_model=BotStatus)
def tick(request: Request):
    return bot_registry.get_bot(request.state.user_id).tick()


@app.post("/api/automation/start", response_model=BotStatus)
def start_automation(request: Request):
    return bot_registry.get_bot(request.state.user_id).start_auto_trading()


@app.post("/api/automation/stop", response_model=BotStatus)
def stop_automation(request: Request):
    return bot_registry.get_bot(request.state.user_id).stop_auto_trading()


@app.post("/api/automation/resume-day", response_model=BotStatus)
def resume_day(request: Request):
    """Manual override: clears today's walk-away so auto-trading may take new entries
    again. Deliberately never automatic - see TradingBot.resume_day."""
    return bot_registry.get_bot(request.state.user_id).resume_day()


@app.post("/api/automation/trades-today", response_model=BotStatus)
def correct_trades_today(count: int, request: Request):
    """Manual bookkeeping correction - see TradingBot.correct_trades_today."""
    return bot_registry.get_bot(request.state.user_id).correct_trades_today(count)


@app.post("/api/scanner")
def scan_market(body: ScannerRequest, request: Request):
    try:
        return MarketScanner(request.state.user_id).scan(body.symbols)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not scan market: {exc}") from exc


@app.post("/api/scanner/auto")
def scan_market_universe(body: AutoScannerRequest, request: Request):
    try:
        return MarketScanner(request.state.user_id).scan_universe(limit=body.limit, max_symbols=body.max_symbols)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not scan market universe: {exc}") from exc


@app.get("/api/account")
def account(request: Request):
    user_id = request.state.user_id
    try:
        acct = AlpacaBroker.for_user(user_id).account()
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
        "paper": bot_registry.get_bot(user_id).status.paper,
    }


@app.get("/api/positions")
def positions(request: Request):
    try:
        return {"positions": AlpacaBroker.for_user(request.state.user_id).positions_as_dicts()}
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca positions: {exc}") from exc


@app.get("/api/portfolio/history")
def portfolio_history(request: Request, period: str = "1D", timeframe: str = "5Min"):
    try:
        return AlpacaBroker.for_user(request.state.user_id).portfolio_history(period=period, timeframe=timeframe)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca portfolio history: {exc}") from exc


@app.get("/api/trades/history")
def trade_history(request: Request, limit: int = 50):
    return {"trades": trade_log.list_trades(limit=min(max(limit, 1), 500), user_id=request.state.user_id)}


@app.post("/api/trades/history/sync")
def sync_trade_history(request: Request):
    user_id = request.state.user_id
    try:
        broker = AlpacaBroker.for_user(user_id)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ntfy_topic = credentials.get_credentials(user_id)["ntfy_topic"]
    trade_sync.sync_orders(broker, user_id=user_id, ntfy_topic=ntfy_topic)
    return {"trades": trade_log.list_trades(limit=50, user_id=user_id)}


@app.get("/api/quote/{symbol}")
def quote(symbol: str, request: Request):
    try:
        return AlpacaBroker.for_user(request.state.user_id).latest_quote(symbol)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca quote for {symbol.upper()}: {exc}") from exc


@app.get("/api/bars/{symbol}")
def bars(
    symbol: str,
    request: Request,
    limit: int = 120,
    trading_date: date | None = None,
    days: int | None = None,
    period: str | None = None,
):
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
            "bars": AlpacaBroker.for_user(request.state.user_id).historical_bars(
                symbol, start=start, end=end, limit=limit, sort=sort, timeframe=timeframe
            ),
        }
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca bars for {symbol.upper()}: {exc}") from exc


@app.websocket("/ws/live/{symbol}")
async def live_chart_stream(websocket: WebSocket, symbol: str):
    """Chart-only real-time overlay (see finnhub_live.py) - not on the HTTP auth
    middleware's path, so the session cookie is checked by hand here instead."""
    await websocket.accept()
    user_id = verify_session_token(websocket.cookies.get(SESSION_COOKIE_NAME) or "")
    if user_id is None:
        await websocket.send_json({"error": "Login required."})
        await websocket.close()
        return
    api_key = credentials.get_credentials(user_id)["finnhub_api_key"]
    if not api_key:
        await websocket.send_json({"error": "Add a Finnhub API key in Settings to use live data."})
        await websocket.close()
        return
    await stream_trades(websocket, symbol, api_key)


@app.post("/api/trade")
def trade(body: TradeRequest, request: Request):
    try:
        return bot_registry.get_bot(request.state.user_id).submit_trade(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/orders/{symbol}/cancel")
def cancel_orders(symbol: str, request: Request):
    """Manual escape hatch: cancel every resting order on a symbol. Built for the
    duplicate-premarket-order bug (2026-08-26) - an unfilled limit buy doesn't show up
    in positions_as_dicts(), so auto_cycle's held_symbols check didn't see it and kept
    re-submitting a fresh entry every cycle. That's fixed at the source now, but this
    stays as a general-purpose way to clear stuck orders by hand."""
    bot_registry.get_bot(request.state.user_id)._cancel_open_orders(symbol)
    return {"symbol": symbol.upper(), "message": "Cancel requested for all open orders on this symbol."}


@app.post("/api/backtest")
def backtest(request: BacktestRequest):
    try:
        return run_daily_backtest(
            request.day,
            symbols=request.symbols,
            starting_capital=request.starting_capital,
            position_value=request.position_value,
        )
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Backtest failed: {exc}") from exc
