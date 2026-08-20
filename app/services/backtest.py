from collections import defaultdict
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
MAX_BACKTEST_DAYS = 730  # ~2 years — keeps data volume and runtime bounded for a synchronous request
AVG_VOLUME_WINDOW = 20  # trading days used as the relative-volume baseline, same convention as the live scanner


def _to_market_date(timestamp) -> date:
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp)
    return timestamp.astimezone(MARKET_TZ).date()


def _current_float_and_sector(symbol: str) -> tuple[int | None, str | None]:
    """Float share count and sector, looked up once per backtest run rather than per day.
    Share structure and sector classification change rarely enough that today's FMP/local-list
    value is a reasonable stand-in for the whole backtest window — unlike news or "leading
    gainer" status, which are genuinely day-specific and aren't practical to source historically.
    Mirrors MarketScanner's own fallback order (FMP first, local metadata list second) so a
    backtested candidate scores exactly the way the live scanner would score it today."""
    symbol = symbol.upper()
    float_shares: int | None = None
    try:
        payload = FmpClient().shares_float(symbol)
    except Exception:
        payload = None
    if payload:
        raw = payload.get("floatShares") or payload.get("float_shares")
        float_shares = int(raw) if raw else None

    metadata = MarketScanner().load_metadata().get(symbol, {})
    if float_shares is None:
        float_shares = metadata.get("float_shares")
    return float_shares, metadata.get("sector")


def _daily_candidates(broker: AlpacaBroker, symbol: str, start: date, end: date) -> dict[date, StockCandidate]:
    """One StockCandidate per trading day, scored the same way the live scanner would —
    except has_news and is_leading_gainer, which are genuinely day-specific and aren't
    practical to source historically, so those two never contribute to the score in a
    backtest. float_shares and sector ARE sourced (see _current_float_and_sector) since
    they're effectively static, so a backtested candidate needs 4 of the *other* 4 pillars
    the same way live trading needs 4 of 5 — not a stricter "4 of 4" bar.
    """
    float_shares, sector = _current_float_and_sector(symbol)
    buffer_start = datetime.combine(start - timedelta(days=45), time.min, tzinfo=MARKET_TZ)
    end_dt = datetime.combine(end, time.max, tzinfo=MARKET_TZ)
    bars_response = broker.daily_bars([symbol], start=buffer_start, end=end_dt)
    daily_bars = list(bars_response.data.get(symbol.upper(), []))
    if len(daily_bars) < 2:
        raise ValueError(f"not enough daily history for {symbol} to run a backtest")

    candidates: dict[date, StockCandidate] = {}
    for i in range(1, len(daily_bars)):
        bar_date = _to_market_date(daily_bars[i].timestamp)
        if bar_date < start or bar_date > end:
            continue

        price = float(daily_bars[i].close)
        previous_close = float(daily_bars[i - 1].close)
        percent_change = ((price - previous_close) / previous_close) * 100 if previous_close else 0.0
        total_volume = int(daily_bars[i].volume or 0)

        window = daily_bars[max(0, i - AVG_VOLUME_WINDOW) : i]
        avg_volume = sum(int(bar.volume or 0) for bar in window) / len(window) if window else 0
        relative_volume = total_volume / avg_volume if avg_volume else 0.0

        candidates[bar_date] = StockCandidate(
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
    return candidates


def _minute_bars_by_day(broker: AlpacaBroker, symbol: str, start: date, end: date) -> dict[date, list[dict]]:
    start_dt = datetime.combine(start, time.min, tzinfo=MARKET_TZ)
    end_dt = datetime.combine(end, time.max, tzinfo=MARKET_TZ)
    days = max((end - start).days, 1)
    bars = broker.historical_bars(symbol, start=start_dt, end=end_dt, limit=days * 390, sort=Sort.ASC)

    by_day: dict[date, list[dict]] = defaultdict(list)
    for bar in bars:
        by_day[_to_market_date(bar["timestamp"])].append(bar)
    return by_day


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


def run_backtest(
    symbol: str,
    start: date,
    end: date,
    starting_capital: float = 10000.0,
    position_value: float = 1000.0,
) -> dict:
    if end <= start:
        raise ValueError("End date must be after start date.")
    if (end - start).days > MAX_BACKTEST_DAYS:
        raise ValueError(f"Backtest range is limited to {MAX_BACKTEST_DAYS} days to keep it running in a reasonable time.")

    broker = AlpacaBroker()
    strategy = SmallAccountPullbackStrategy()

    candidates = _daily_candidates(broker, symbol, start, end)
    minute_bars = _minute_bars_by_day(broker, symbol, start, end)

    trades: list[dict] = []
    equity = starting_capital
    equity_curve = [{"date": start.isoformat(), "equity": equity}]
    days_scanned = 0
    days_qualified = 0

    for day in sorted(set(candidates) & set(minute_bars)):
        days_scanned += 1
        candidate = candidates[day]
        score, _reasons = strategy.score_candidate(candidate)
        if score < 4:
            continue
        days_qualified += 1

        day_bars = minute_bars[day]
        if len(day_bars) < 3:
            continue

        position = None  # {"qty", "entry_price", "entry_time", "stop", "target"}

        for i in range(2, len(day_bars)):
            bars_so_far = day_bars[: i + 1]
            current = bars_so_far[-1]

            if position is not None:
                exit_price = None
                exit_reason = None
                if current["low"] <= position["stop"]:
                    exit_price = position["stop"]
                    exit_reason = "stop"
                elif position["target"] is not None and current["high"] >= position["target"]:
                    exit_price = position["target"]
                    exit_reason = "target"
                else:
                    decision = strategy.evaluate(_build_setup(candidate, bars_so_far))
                    if decision.signal == Signal.sell:
                        exit_price = current["close"]
                        exit_reason = "signal"

                if exit_price is not None:
                    pnl = (exit_price - position["entry_price"]) * position["qty"]
                    equity += pnl
                    trades.append(
                        {
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
                    equity_curve.append({"date": _to_market_date(current["timestamp"]).isoformat(), "equity": round(equity, 2)})
                    position = None
                    continue

            if position is None:
                setup = _build_setup(candidate, bars_so_far)
                decision = strategy.evaluate(setup)
                if decision.signal == Signal.buy:
                    risk_per_share = setup.proposed_entry - setup.proposed_stop
                    qty_by_risk = int(settings.risk_per_trade // risk_per_share) if risk_per_share > 0 else 0
                    qty_by_capital = int(position_value // setup.proposed_entry)
                    qty = min(qty_by_risk, qty_by_capital) if qty_by_risk else qty_by_capital
                    if qty >= 1:
                        position = {
                            "qty": qty,
                            "entry_price": setup.proposed_entry,
                            "entry_time": current["timestamp"],
                            "stop": setup.proposed_stop,
                            "target": decision.first_target,
                        }

        if position is not None:
            last = day_bars[-1]
            exit_price = last["close"]
            pnl = (exit_price - position["entry_price"]) * position["qty"]
            equity += pnl
            trades.append(
                {
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
            equity_curve.append({"date": day.isoformat(), "equity": round(equity, 2)})

    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "symbol": symbol.upper(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "starting_capital": starting_capital,
        "ending_equity": round(equity, 2),
        "total_return_pct": round((equity - starting_capital) / starting_capital * 100, 2) if starting_capital else 0.0,
        "trade_count": len(trades),
        "win_count": len(wins),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "best_trade_pct": max((t["pnl_pct"] for t in trades), default=0.0),
        "worst_trade_pct": min((t["pnl_pct"] for t in trades), default=0.0),
        "days_scanned": days_scanned,
        "days_qualified": days_qualified,
        "trades": trades,
        "equity_curve": equity_curve,
    }
