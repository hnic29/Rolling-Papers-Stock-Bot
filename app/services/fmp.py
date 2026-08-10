from __future__ import annotations

import requests

from app.config import settings


class FmpClient:
    base_url = "https://financialmodelingprep.com/stable"

    def __init__(self) -> None:
        self.api_key = settings.fmp_api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def shares_float(self, symbol: str) -> dict | None:
        if not self.configured:
            return None

        response = requests.get(
            f"{self.base_url}/shares-float",
            params={"symbol": symbol.upper(), "apikey": self.api_key},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload[0] if payload else None
        if isinstance(payload, dict):
            return payload
        return None
