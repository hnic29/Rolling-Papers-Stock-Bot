from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from alpaca.common.enums import Sort

from app.brokers.alpaca_broker import AlpacaBroker
from app.models import Candle, PullbackSetup, StockCandidate
from app.services.scanner import MarketScanner

MARKET_TZ = ZoneInfo("America/New_York")
EMA_PERIOD = 9


def compute_ema(values: list[float], period: int = EMA_PERIOD) -> float:
    if not values:
        return 0.0
    seed_len = min(period, len(values))
    ema = sum(values[:seed_len]) / seed_len
    multiplier = 2 / (period + 1)
    for value in values[seed_len:]:
        ema = (value - ema) * multiplier + ema
    return ema


def build_pullback_setup(symbol: str, scanner: MarketScanner) -> PullbackSetup:
    """Assemble a real PullbackSetup for `symbol` from live scanner + candle data."""
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
        high_of_day=high_of_day,
        pullback_low=pullback_low,
        proposed_entry=proposed_entry,
        proposed_stop=proposed_stop,
        level_two=None,
    )
