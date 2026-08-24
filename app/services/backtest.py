from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from alpaca.common.enums import Sort

from app.brokers.alpaca_broker import AlpacaBroker
from app.config import settings
from app.models import Candle, PullbackSetup, Signal, StockCandidate
from app.services.fmp import FmpClient
from app.services.live_setup import compute_ema, compute_macd, compute_vwap
from app.services.scanner import MarketScanner
from app.strategies.small_account_pullback import SmallAccountPullbackStrategy

MARKET_TZ = ZoneInfo("America/New_York")
AVG_VOLUME_WINDOW = 20  # trading days used as the relative-volume baseline, same convention as the live scanner
MAX_BACKTEST_SYMBOLS = 100  # guards a user-supplied symbol list; the real universe is far smaller


def _to_market_date(timestamp) -> date:
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp)
    return timestamp.astimezone(MARKET_TZ).date()


def _float_and_sector(symbol: str, metadata: dict) -> tuple[int | None, str | None]:
    """Float share count and sector for one symbol. Checks the local, already-curated
    metadata list FIRST (data/symbol_metadata.csv - free, instant, and accurate for
    anything in the real universe, since scripts/backfill_float_data.py keeps it that
    way) and only falls back to a live FMP lookup for a symbol that isn't in it - e.g.
    a custom one-off symbol typed into the backtest form. Share structure and sector
    change rarely enough that today's value is a reasonable stand-in for the whole day
    being tested - unlike news, which is genuinely day-specific and isn't sourced here."""
    symbol = symbol.upper()
    row = metadata.get(symbol, {})
    float_shares = row.get("float_shares")
    sector = row.get("sector")
    if float_shares is not None:
        return float_shares, sector

    try:
        payload = FmpClient().shares_float(symbol)
    except Exception:
        payload = None
    if payload:
        raw = payload.get("floatShares") or payload.get("float_shares")
        float_shares = int(raw) if raw else None
    return float_shares, sector


def _daily_candidate(broker: AlpacaBroker, symbol: str, day: date, metadata: dict) -> StockCandidate | None:
    """The single StockCandidate for `symbol` on `day`, scored the same way the live
    scanner would - except has_news and is_leading_gainer, which are genuinely
    day-specific and aren't practical to source historically, so those two never
    contribute to the score here. A candidate needs 4 of the *other* 4 pillars the
    same way live trading needs 4 of 5 - not a stricter "4 of 4" bar. None if there's
    no bar for `day` at all (holiday, not yet listed, delisted, no data)."""
    buffer_start = datetime.combine(day - timedelta(days=45), time.min, tzinfo=MARKET_TZ)
    end_dt = datetime.combine(day, time.max, tzinfo=MARKET_TZ)
    bars_response = broker.daily_bars([symbol], start=buffer_start, end=end_dt)
    daily_bars = list(bars_response.data.get(symbol.upper(), []))

    match = next((i for i in range(1, len(daily_bars)) if _to_market_date(daily_bars[i].timestamp) == day), None)
    if match is None:
        return None

    price = float(daily_bars[match].close)
    previous_close = float(daily_bars[match - 1].close)
    percent_change = ((price - previous_close) / previous_close) * 100 if previous_close else 0.0
    total_volume = int(daily_bars[match].volume or 0)

    window = daily_bars[max(0, match - AVG_VOLUME_WINDOW) : match]
    avg_volume = sum(int(bar.volume or 0) for bar in window) / len(window) if window else 0
    relative_volume = total_volume / avg_volume if avg_volume else 0.0

    float_shares, sector = _float_and_sector(symbol, metadata)
    return StockCandidate(
        symbol=symbol.upper(),
        price=price,
        percent_change=percent_change,
        relative_volume=relative_volume,
        total_volume=total_volume,
        float_shares=float_shares,
        has_news=False,
        sector=sector,
        is_leading_gainer=False,
    )


def _minute_bars_for_day(broker: AlpacaBroker, symbol: str, day: date) -> list[dict]:
    start_dt = datetime.combine(day, time.min, tzinfo=MARKET_TZ)
    end_dt = datetime.combine(day, time.max, tzinfo=MARKET_TZ)
    bars = broker.historical_bars(symbol, start=start_dt, end=end_dt, limit=390, sort=Sort.ASC)
    return [bar for bar in bars if _to_market_date(bar["timestamp"]) == day]


def _candles_from_bars(bars: list[dict]) -> list[Candle]:
    return [Candle(open=bar["open"], high=bar["high"], low=bar["low"], close=bar["close"], volume=int(bar["volume"])) for bar in bars]


def _build_setup(candidate: StockCandidate, bars_so_far: list[dict]) -> PullbackSetup:
    candles = _candles_from_bars(bars_so_far)
    closes = [candle.close for candle in candles]
    ema9 = compute_ema(closes)
    macd = compute_macd(closes)
    vwap = compute_vwap(candles)
    high_of_day = max(candle.high for candle in candles)
    pullback_low = candles[-2].low
    proposed_entry = candles[-1].close
    stop_buffer = max(0.01, pullback_low * 0.001)
    proposed_stop = round(pullback_low - stop_buffer, 4)
    return PullbackSetup(
        candidate=candidate,
        candles=candles,
        ema9=round(ema9, 4),
        macd=round(macd, 4),
        vwap=round(vwap, 4),
        high_of_day=high_of_day,
        pullback_low=pullback_low,
        proposed_entry=proposed_entry,
        proposed_stop=proposed_stop,
        level_two=None,
    )


def run_daily_backtest(
    day: date,
    symbols: list[str] | None = None,
    starting_capital: float = 10000.0,
    position_value: float = 1000.0,
) -> dict:
    """Replays one real trading day exactly the way live auto-trading would run it:
    scans a universe, scores every symbol against the same five pillars, and for
    anything that qualifies, walks its actual 1-minute candles evaluating the same
    entry/exit rules submit_trade and manage_open_positions use. Multiple symbols can
    be held at once, sized against a single shared pool of capital - the same way the
    real bankroll works - so a second candidate on the same day sizes itself against
    what the first one left available, not an independent pot of money.

    No fixed take-profit, on purpose: an open position is only ever closed by its
    stop-loss or a real exit_indicators() signal (a red candle, a topping tail), never
    a price target. Capping winners at a target here would silently test a strategy
    the live bot doesn't actually run."""
    if day >= datetime.now(MARKET_TZ).date():
        raise ValueError("Pick a date in the past — today and future dates have no completed session to replay.")

    broker = AlpacaBroker()
    strategy = SmallAccountPullbackStrategy()
    scanner = MarketScanner()
    metadata = scanner.load_metadata()

    requested = [s.strip().upper() for s in symbols if s.strip()] if symbols else []
    universe = sorted(set(requested)) if requested else scanner.load_universe()
    if not universe:
        raise ValueError("No symbols to test — the universe list is empty and none were provided.")
    if len(universe) > MAX_BACKTEST_SYMBOLS:
        raise ValueError(f"Too many symbols ({len(universe)}) — limited to {MAX_BACKTEST_SYMBOLS} per run.")

    candidates: dict[str, StockCandidate] = {}
    day_bars: dict[str, list[dict]] = {}
    scanned: list[dict] = []

    for symbol in universe:
        candidate = _daily_candidate(broker, symbol, day, metadata)
        if candidate is None:
            continue
        score, reasons = strategy.score_candidate(candidate)
        scanned.append({"symbol": symbol, "score": score, "qualified": score >= 4, "reasons": reasons})
        if score < 4:
            continue
        bars = _minute_bars_for_day(broker, symbol, day)
        if len(bars) < 3:
            continue
        candidates[symbol] = candidate
        day_bars[symbol] = bars

    scanned.sort(key=lambda row: row["score"], reverse=True)

    # A shared timeline across every qualifying symbol, oldest first - this is what
    # lets one pool of capital (not one per symbol) drive sizing, and lets
    # max_trades_per_day count across all of them combined, the same way a real
    # trading day works.
    timeline: list[tuple[str, int]] = [
        (symbol, i) for symbol, bars in day_bars.items() for i in range(2, len(bars))
    ]
    timeline.sort(key=lambda item: (day_bars[item[0]][item[1]]["timestamp"], item[0]))

    cash = starting_capital
    trades_today = 0
    open_positions: dict[str, dict] = {}
    trades: list[dict] = []

    for symbol, i in timeline:
        bars_so_far = day_bars[symbol][: i + 1]
        current = bars_so_far[-1]
        candidate = candidates[symbol]

        position = open_positions.get(symbol)
        if position is not None:
            exit_price = None
            exit_reason = None
            if current["low"] <= position["stop"]:
                exit_price = position["stop"]
                exit_reason = "stop"
            elif strategy.exit_indicators(_build_setup(candidate, bars_so_far)):
                exit_price = current["close"]
                exit_reason = "exit_signal"

            if exit_price is not None:
                pnl = (exit_price - position["entry_price"]) * position["qty"]
                cash += position["qty"] * exit_price
                trades.append(
                    {
                        "symbol": symbol,
                        "entry_time": position["entry_time"],
                        "entry_price": round(position["entry_price"], 4),
                        "exit_time": current["timestamp"],
                        "exit_price": round(exit_price, 4),
                        "qty": position["qty"],
                        "pnl": round(pnl, 2),
                        "pnl_pct": round((exit_price - position["entry_price"]) / position["entry_price"] * 100, 2),
                        "exit_reason": exit_reason,
                    }
                )
                del open_positions[symbol]
            continue

        if symbol in open_positions or trades_today >= settings.max_trades_per_day:
            continue

        setup = _build_setup(candidate, bars_so_far)
        decision = strategy.evaluate(setup)
        if decision.signal != Signal.buy:
            continue

        risk_dollars = cash * settings.risk_per_trade_pct / 100
        risk_per_share = setup.proposed_entry - setup.proposed_stop
        qty_by_risk = int(risk_dollars // risk_per_share) if risk_per_share > 0 else 0
        qty_by_capital = int(position_value // setup.proposed_entry)
        qty_by_cash = int(cash // setup.proposed_entry)  # the multi-position safeguard - a shared pool, not one per symbol
        size_candidates = [q for q in (qty_by_risk, qty_by_capital) if q > 0]
        qty = min(min(size_candidates) if size_candidates else qty_by_capital, qty_by_cash)
        if qty < 1:
            continue

        open_positions[symbol] = {
            "qty": qty,
            "entry_price": setup.proposed_entry,
            "entry_time": current["timestamp"],
            "stop": setup.proposed_stop,
        }
        cash -= qty * setup.proposed_entry
        trades_today += 1

    # Anything still open at the close exits at the last available price.
    for symbol, position in open_positions.items():
        last = day_bars[symbol][-1]
        exit_price = last["close"]
        pnl = (exit_price - position["entry_price"]) * position["qty"]
        cash += position["qty"] * exit_price
        trades.append(
            {
                "symbol": symbol,
                "entry_time": position["entry_time"],
                "entry_price": round(position["entry_price"], 4),
                "exit_time": last["timestamp"],
                "exit_price": round(exit_price, 4),
                "qty": position["qty"],
                "pnl": round(pnl, 2),
                "pnl_pct": round((exit_price - position["entry_price"]) / position["entry_price"] * 100, 2),
                "exit_reason": "end_of_day",
            }
        )

    trades.sort(key=lambda t: t["entry_time"])
    equity = starting_capital
    equity_curve = [{"time": datetime.combine(day, time(9, 30), tzinfo=MARKET_TZ).isoformat(), "equity": equity}]
    for trade in sorted(trades, key=lambda t: t["exit_time"]):
        equity = round(equity + trade["pnl"], 2)
        equity_curve.append({"time": trade["exit_time"], "equity": equity})

    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "day": day.isoformat(),
        "starting_capital": starting_capital,
        "ending_equity": round(cash, 2),
        "total_return_pct": round((cash - starting_capital) / starting_capital * 100, 2) if starting_capital else 0.0,
        "trade_count": len(trades),
        "win_count": len(wins),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "best_trade_pct": max((t["pnl_pct"] for t in trades), default=0.0),
        "worst_trade_pct": min((t["pnl_pct"] for t in trades), default=0.0),
        "symbols_scanned": len(scanned),
        "symbols_qualified": sum(1 for row in scanned if row["qualified"]),
        "symbols_traded": len({t["symbol"] for t in trades}),
        "candidates": scanned,
        "trades": trades,
        "equity_curve": equity_curve,
    }
