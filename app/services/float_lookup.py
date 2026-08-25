"""Live float-share lookups with a fallback chain: FMP first, Yahoo Finance second.

FMP's free quota is small and shared across everything this app scans - it recovers
daily, but a busy scan day can exhaust it, and a symbol from the live top-gainers feed
(the ones that matter most, like XPON on its +80% day) then scored only 4/5 with its
float pillar blank. Yahoo (via yfinance, already a dependency - it's what
scripts/backfill_float_data.py uses) has no fixed quota, so chaining it behind FMP
means a float number keeps arriving even with FMP dry. Both layers cache for a day:
share structure doesn't move intraday, and the Yahoo lookup is slow enough (~1s) that
repeating it every 2-minute scan cycle would be pure waste.
"""

import time

from app.services.fmp import FmpClient

_YF_CACHE_TTL_SECONDS = 24 * 60 * 60
_yf_cache: dict[str, tuple[float, int | None]] = {}


def float_shares(symbol: str) -> int | None:
    symbol = symbol.upper()

    # 1) FMP - authoritative when available; FmpClient carries its own day-long cache.
    try:
        payload = FmpClient().shares_float(symbol)
    except Exception:
        payload = None
    if payload:
        raw = payload.get("floatShares") or payload.get("float_shares")
        if raw:
            return int(raw)

    # 2) Yahoo Finance. Lazy import - yfinance drags in pandas and friends, which
    # server startup shouldn't pay for.
    cached = _yf_cache.get(symbol)
    if cached is not None and time.time() - cached[0] < _YF_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        import yfinance as yf

        value = yf.Ticker(symbol).info.get("floatShares")
        result = int(value) if value else None
    except Exception:
        return None  # transient failure - deliberately NOT cached, so the next cycle retries

    _yf_cache[symbol] = (time.time(), result)
    return result
