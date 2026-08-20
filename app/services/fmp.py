from __future__ import annotations

import time

import requests

from app.config import settings

_STATUS_MESSAGES = {
    401: "FMP rejected the key as invalid",
    403: "FMP rejected the key as invalid",
    429: "FMP rate limit reached — wait a bit and try again",
}

# Float share counts change rarely (companies don't reprice their share structure minute to
# minute), so successful lookups are cached across all FmpClient instances for a day. FMP's own
# docs list "reduce request frequency" as the fix for 429s — this is that, applied structurally
# rather than left to callers to remember. Shared at module level (not per-instance) since the
# scanner used by manual scans and the one used by the automation loop are separate FmpClient
# owners that would otherwise duplicate every lookup.
_FLOAT_CACHE_TTL_SECONDS = 24 * 60 * 60
_float_cache: dict[str, tuple[float, dict | None]] = {}


class FmpRequestError(Exception):
    """An FMP request failed. Never carries the request URL, which includes the API key."""


class FmpClient:
    base_url = "https://financialmodelingprep.com/stable"

    def __init__(self) -> None:
        self.api_key = settings.fmp_api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def shares_float(self, symbol: str, use_cache: bool = True) -> dict | None:
        if not self.configured:
            return None

        symbol = symbol.upper()
        if use_cache:
            cached = _float_cache.get(symbol)
            if cached is not None and time.time() - cached[0] < _FLOAT_CACHE_TTL_SECONDS:
                return cached[1]

        try:
            response = requests.get(
                f"{self.base_url}/shares-float",
                params={"symbol": symbol, "apikey": self.api_key},
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            raise FmpRequestError(f"could not reach FMP ({type(exc).__name__})") from exc

        if not response.ok:
            raise FmpRequestError(_STATUS_MESSAGES.get(response.status_code, f"FMP returned HTTP {response.status_code}"))

        payload = response.json()
        if isinstance(payload, list):
            result = payload[0] if payload else None
        elif isinstance(payload, dict):
            result = payload
        else:
            result = None

        _float_cache[symbol] = (time.time(), result)
        return result
