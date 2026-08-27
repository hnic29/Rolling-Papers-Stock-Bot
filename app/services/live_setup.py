from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from alpaca.common.enums import Sort

from app.brokers.alpaca_broker import AlpacaBroker
from app.models import Candle, PullbackSetup, StockCandidate
from app.services.scanner import MarketScanner

MARKET_TZ = ZoneInfo("America/New_York")
EMA_PERIOD = 9
MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9  # the classic 12/26/9 MACD convention


def _ema_series(values: list[float], period: int) -> list[float]:
    """Same seeding/smoothing as compute_ema, but returns every intermediate value,
    not just the last - needed to build the MACD line's own signal line (an EMA of
    the MACD line itself), which compute_macd's single final value can't provide."""
    if not values:
        return []
    seed_len = min(period, len(values))
    seed = sum(values[:seed_len]) / seed_len
    series = [seed] * seed_len  # backfill so the series aligns 1:1 with `values`
    multiplier = 2 / (period + 1)
    ema = seed
    for value in values[seed_len:]:
        ema = (value - ema) * multiplier + ema
        series.append(ema)
    return series


def compute_macd_signal(
    values: list[float], fast: int = MACD_FAST_PERIOD, slow: int = MACD_SLOW_PERIOD, signal_period: int = MACD_SIGNAL_PERIOD
) -> float:
    """The MACD line's own signal line - MACD crossing below this is the classic
    momentum-decay exit signal (Warrior Trading's own trading-plan template lists
    "MACD crosses signal line" as an explicit trade-invalidation trigger, alongside
    decreasing volume and a sharp reversal candle - the other two were already
    covered by exit_indicators()'s red-candle and topping-tail checks; this was the
    one genuinely missing)."""
    if not values:
        return 0.0
    fast_series = _ema_series(values, fast)
    slow_series = _ema_series(values, slow)
    macd_series = [f - s for f, s in zip(fast_series, slow_series)]
    return _ema_series(macd_series, signal_period)[-1] if macd_series else 0.0


def compute_ema(values: list[float], period: int = EMA_PERIOD) -> float:
    if not values:
        return 0.0
    seed_len = min(period, len(values))
    ema = sum(values[:seed_len]) / seed_len
    multiplier = 2 / (period + 1)
    for value in values[seed_len:]:
        ema = (value - ema) * multiplier + ema
    return ema


def compute_macd(values: list[float], fast: int = MACD_FAST_PERIOD, slow: int = MACD_SLOW_PERIOD) -> float:
    """MACD line (fast EMA minus slow EMA) — used as a binary go/no-go trend filter:
    "we don't like to trade when the MACD is negative, we're trading against a
    headwind." With fewer candles than `slow` in the session so far, compute_ema's
    own partial-data seeding still returns a usable (if rougher) approximation
    rather than requiring a minimum history."""
    if not values:
        return 0.0
    return compute_ema(values, fast) - compute_ema(values, slow)


def compute_vwap(candles: list[Candle]) -> float:
    """Session-to-date volume-weighted average price. Callers must pass only the
    current trading day's candles — VWAP resets every session."""
    total_volume = sum(c.volume for c in candles)
    if not total_volume:
        return candles[-1].close if candles else 0.0
    total_dollar_volume = sum(((c.high + c.low + c.close) / 3) * c.volume for c in candles)
    return total_dollar_volume / total_volume


def build_pullback_setup(symbol: str, scanner: MarketScanner, candidate: StockCandidate | None = None) -> PullbackSetup:
    """Assemble a real PullbackSetup for `symbol` from live scanner + candle data.

    A caller may inject a pre-built `candidate` - the real-time gap lane does, because
    its candidate carries LIVE gap/price numbers, while re-deriving one here would
    score it from the 16-minute-lagged daily feed (which, before today's consolidated
    bar exists, would silently describe YESTERDAY's session instead of the move
    happening right now)."""
    if candidate is None:
        scan = scanner.scan([symbol])
        if not scan.results:
            raise ValueError(f"no market data available for {symbol}")
        result = scan.results[0]

        candidate = StockCandidate(
            symbol=result.symbol,
            price=result.price,
            percent_change=result.percent_change,
            relative_volume=result.relative_volume or 0.0,
            total_volume=result.total_volume,
            float_shares=result.float_shares,
            has_news=result.has_news,
            sector=result.sector,
            is_leading_gainer=False,
        )

    broker = AlpacaBroker()
    end = datetime.now(UTC)
    start = end - timedelta(days=5)
    # DESC + reverse (handled inside historical_bars) gets the most recent bars even
    # right after a weekend/holiday, rather than an empty window.
    bars = broker.historical_bars(symbol, start=start, end=end, limit=390, sort=Sort.DESC)
    if not bars:
        raise ValueError(f"no recent candles available for {symbol}")

    latest_date = datetime.fromisoformat(bars[-1]["timestamp"]).astimezone(MARKET_TZ).date()
    session_bars = [
        bar for bar in bars if datetime.fromisoformat(bar["timestamp"]).astimezone(MARKET_TZ).date() == latest_date
    ]
    if len(session_bars) < 3:
        raise ValueError(f"not enough candles in the latest session for {symbol} to evaluate a pullback setup")

    candles = [
        Candle(open=bar["open"], high=bar["high"], low=bar["low"], close=bar["close"], volume=int(bar["volume"]))
        for bar in session_bars
    ]
    closes = [candle.close for candle in candles]
    ema9 = compute_ema(closes)
    macd = compute_macd(closes)
    macd_signal = compute_macd_signal(closes)
    vwap = compute_vwap(candles)
    high_of_day = max(candle.high for candle in candles)
    # The strategy treats candles[:-2] as the prior impulse and candles[-2] as the
    # pullback bar itself, so its low is the natural pullback_low.
    pullback_low = candles[-2].low
    proposed_entry = candles[-1].close
    stop_buffer = max(0.01, pullback_low * 0.001)
    proposed_stop = round(pullback_low - stop_buffer, 4)

    return PullbackSetup(
        candidate=candidate,
        candles=candles,
        ema9=round(ema9, 4),
        macd=round(macd, 4),
        macd_signal=round(macd_signal, 4),
        vwap=round(vwap, 4),
        high_of_day=high_of_day,
        pullback_low=pullback_low,
        proposed_entry=proposed_entry,
        proposed_stop=proposed_stop,
        level_two=None,
    )
