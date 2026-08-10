from datetime import UTC, datetime, timedelta
import csv
from pathlib import Path

from app.brokers.alpaca_broker import AlpacaBroker
from app.models import ScannerResponse, ScannerResult, Signal, StockCandidate
from app.services.fmp import FmpClient
from app.strategies.small_account_pullback import SmallAccountPullbackStrategy


class MarketScanner:
    def __init__(self) -> None:
        self.strategy = SmallAccountPullbackStrategy()
        self.universe_path = Path("data/stock_universe.txt")
        self.metadata_path = Path("data/symbol_metadata.csv")
        self.fmp = FmpClient()

    def scan(self, symbols: list[str]) -> ScannerResponse:
        clean_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        if not clean_symbols:
            return ScannerResponse(results=[])

        broker = AlpacaBroker()
        end = datetime.now(UTC)
        start = end - timedelta(days=80)
        bars = broker.daily_bars(clean_symbols, start=start, end=end)
        metadata = self.load_metadata()
        news = self._safe_news(broker, clean_symbols, start=end - timedelta(days=3), end=end)
        results: list[ScannerResult] = []

        for symbol in clean_symbols:
            symbol_bars = list(bars.data.get(symbol, []))
            if len(symbol_bars) < 2:
                continue

            latest = symbol_bars[-1]
            previous = symbol_bars[-2]
            price = float(latest.close)
            previous_close = float(previous.close)
            percent_change = ((price - previous_close) / previous_close) * 100 if previous_close else 0.0
            avg_volume = sum(int(bar.volume or 0) for bar in symbol_bars[:-1]) / max(len(symbol_bars) - 1, 1)
            total_volume = int(latest.volume or 0)
            relative_volume = total_volume / avg_volume if avg_volume else None

            quote = self._safe_quote(broker, symbol)
            symbol_metadata = metadata.get(symbol, {})
            latest_news = news.get(symbol)
            fmp_float = self._safe_float(symbol)
            float_shares = fmp_float.get("float_shares") if fmp_float else symbol_metadata.get("float_shares")
            sector = symbol_metadata.get("sector")
            candidate = StockCandidate(
                symbol=symbol,
                price=price,
                percent_change=percent_change,
                relative_volume=relative_volume or 0.0,
                total_volume=total_volume,
                float_shares=float_shares,
                has_news=latest_news is not None,
                sector=sector,
                is_leading_gainer=False,
            )
            score, reasons = self.strategy.score_candidate(candidate)
            signal = Signal.hold if score < 4 else Signal.buy
            if score < 4:
                reasons.append("scanner score is below 4 of 5 stock-selection pillars")
            else:
                reasons.append("candidate is worth watching for a first pullback")

            results.append(
                ScannerResult(
                    symbol=symbol,
                    price=round(price, 4),
                    percent_change=round(percent_change, 2),
                    total_volume=total_volume,
                    relative_volume=round(relative_volume, 2) if relative_volume is not None else None,
                    bid_price=quote.get("bid_price") if quote else None,
                    ask_price=quote.get("ask_price") if quote else None,
                    float_shares=float_shares,
                    sector=sector,
                    has_news=latest_news is not None,
                    news_headline=latest_news.get("headline") if latest_news else None,
                    news_url=latest_news.get("url") if latest_news else None,
                    score=score,
                    signal=signal,
                    reasons=reasons,
                )
            )

        results.sort(key=lambda item: (item.score, item.percent_change, item.total_volume), reverse=True)
        return ScannerResponse(results=results)

    def scan_universe(self, limit: int = 25, max_symbols: int = 250) -> ScannerResponse:
        symbols = self.load_universe()[: max(1, min(max_symbols, 1000))]
        response = self.scan(symbols)
        ranked = response.results[: max(1, min(limit, 100))]
        return ScannerResponse(results=ranked)

    def load_universe(self) -> list[str]:
        if not self.universe_path.exists():
            return []
        symbols = []
        for line in self.universe_path.read_text(encoding="utf-8").splitlines():
            symbol = line.strip().upper()
            if symbol and not symbol.startswith("#"):
                symbols.append(symbol)
        return sorted(set(symbols))

    def load_metadata(self) -> dict[str, dict]:
        if not self.metadata_path.exists():
            return {}

        metadata: dict[str, dict] = {}
        with self.metadata_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                symbol = (row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                float_text = (row.get("float_shares") or "").strip()
                metadata[symbol] = {
                    "float_shares": int(float_text) if float_text.isdigit() else None,
                    "sector": (row.get("sector") or "").strip().lower() or None,
                }
        return metadata

    def _safe_quote(self, broker: AlpacaBroker, symbol: str) -> dict | None:
        try:
            return broker.latest_quote(symbol)
        except Exception:
            return None

    def _safe_news(self, broker: AlpacaBroker, symbols: list[str], start: datetime, end: datetime) -> dict[str, dict]:
        try:
            response = broker.latest_news(symbols, start=start, end=end, limit=min(len(symbols) * 3, 50))
        except Exception:
            return {}

        items = response.data.get("news", []) if hasattr(response, "data") else []
        news_by_symbol: dict[str, dict] = {}
        for item in items:
            headline = getattr(item, "headline", None) if not isinstance(item, dict) else item.get("headline")
            url = getattr(item, "url", None) if not isinstance(item, dict) else item.get("url")
            item_symbols = getattr(item, "symbols", []) if not isinstance(item, dict) else item.get("symbols", [])
            for symbol in item_symbols:
                symbol = symbol.upper()
                if symbol in symbols and symbol not in news_by_symbol:
                    news_by_symbol[symbol] = {"headline": headline, "url": url}
        return news_by_symbol

    def _safe_float(self, symbol: str) -> dict | None:
        try:
            payload = self.fmp.shares_float(symbol)
        except Exception:
            return None
        if not payload:
            return None

        float_value = payload.get("floatShares") or payload.get("float_shares")
        outstanding = payload.get("outstandingShares") or payload.get("outstanding_shares")
        return {
            "float_shares": int(float_value) if float_value else None,
            "outstanding_shares": int(outstanding) if outstanding else None,
            "source": payload.get("source"),
            "date": payload.get("date"),
        }
