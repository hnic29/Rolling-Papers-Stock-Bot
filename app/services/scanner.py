from datetime import UTC, datetime, time as dtime, timedelta
import csv
import re
from zoneinfo import ZoneInfo

from app.brokers.alpaca_broker import AlpacaBroker
from app.models import ScannerResponse, ScannerResult, Signal, StockCandidate
from app.paths import resource_path
from app.services import float_lookup
from app.services.catalyst import classify_catalyst
from app.strategies.small_account_pullback import SmallAccountPullbackStrategy

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
TRADING_SESSION_MINUTES = 390  # 9:30-16:00 ET
AVG_VOLUME_WINDOW = 20  # trading days used as the relative-volume baseline
# Alpaca's free tier rejects a SIP (consolidated) query any newer than ~15 minutes -
# verified directly (14 worked, 5 and 0 minutes back did not). Daily-bar-based scoring
# doesn't need to-the-second freshness, so trading that lag away for true consolidated
# volume (see AlpacaBroker.daily_bars) is a clear win, not a compromise.
SIP_RECENCY_BUFFER_MINUTES = 16

_UNIVERSE_BUILD_DATE_PATTERN = re.compile(r"Built by scripts/build_universe\.py on (\d{4}-\d{2}-\d{2})")
_COMMON_STOCK_SYMBOL = re.compile(r"[A-Z]{1,5}")
# NASDAQ's 5th-letter suffix conventions: W = warrant, U = unit, R = rights - none of
# which are the common shares this strategy trades (a top-gainers list is full of
# +300% warrants at $0.01 that would just waste scan slots).
_DERIVATIVE_SUFFIXES = "WUR"

# Whole-market sweep settings. ~8,200 tradable symbols / 300 per batched daily-bars
# request = ~28 requests per sweep - comfortably inside Alpaca's free-tier rate limit
# (200/min) even on a 2-minute cycle.
SWEEP_CHUNK_SIZE = 300
SWEEP_MAX_HITS = 25  # hits get the full (quotes/news/float) scan - keep that bounded
# Real-time gap lane: minimum TODAY volume on the IEX feed just to prove a gapping
# print is real trading, not one stale odd lot. IEX carries only a slice of
# consolidated volume (~2-3% verified), so this is deliberately small.
GAP_LANE_MIN_IEX_VOLUME = 10_000
GAP_LANE_MAX_CANDIDATES = 25
# The tradable-symbol list barely changes intraday; refetching ~14K assets every
# 2-minute cycle would be pure waste. Cached per calendar day, module-level so the
# manual Auto Scan button and the automation loop share it.
_tradable_symbols_cache: dict = {"date": None, "symbols": []}


def _session_progress_fraction(now: datetime) -> float:
    """How far into today's regular session `now` falls, as a fraction of a full trading
    day. The daily bar Alpaca returns for "today" is a partial, still-forming bar during
    market hours - comparing its volume-so-far directly against prior days' *full-day*
    average volume systematically understates relative volume any time before the close
    (a stock trading at its normal pace reads as a small fraction of "average" at 10am,
    not because it's quiet, but because the day isn't over yet). Prorating the historical
    average down to the same point in the session fixes that."""
    local = now.astimezone(MARKET_TZ)
    if local.weekday() >= 5 or local.time() < MARKET_OPEN or local.time() >= MARKET_CLOSE:
        return 1.0  # no partial bar in progress - the latest bar is already a full day
    elapsed_minutes = (local.hour * 60 + local.minute) - (MARKET_OPEN.hour * 60 + MARKET_OPEN.minute)
    return max(elapsed_minutes / TRADING_SESSION_MINUTES, 0.05)


class MarketScanner:
    def __init__(self) -> None:
        self.strategy = SmallAccountPullbackStrategy()
        self.universe_path = resource_path("data/stock_universe.txt")
        self.metadata_path = resource_path("data/symbol_metadata.csv")

    def scan(self, symbols: list[str]) -> ScannerResponse:
        clean_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        if not clean_symbols:
            return ScannerResponse(results=[])

        broker = AlpacaBroker()
        # Offset past Alpaca's free-tier SIP recency restriction (see
        # SIP_RECENCY_BUFFER_MINUTES) - session_progress is computed against this SAME
        # timestamp, not literal now(), so the proration matches what the data actually
        # reflects: volume accumulated as of `end`, not as of this instant.
        end = datetime.now(UTC) - timedelta(minutes=SIP_RECENCY_BUFFER_MINUTES)
        start = end - timedelta(days=80)
        bars = broker.daily_bars(clean_symbols, start=start, end=end)
        metadata = self.load_metadata()
        news = self._safe_news(broker, clean_symbols, start=end - timedelta(days=3), end=end)
        session_progress = _session_progress_fraction(end)
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
            # Last AVG_VOLUME_WINDOW trading days only, not every day this request
            # happened to fetch - otherwise this drifts with the fetch window's size
            # (80 calendar days is ~56 trading days) instead of the intended 20-day
            # baseline, silently diverging from the backtest's own 20-day convention.
            prior_bars = symbol_bars[:-1][-AVG_VOLUME_WINDOW:]
            full_day_avg_volume = sum(int(bar.volume or 0) for bar in prior_bars) / max(len(prior_bars), 1)
            avg_volume = full_day_avg_volume * session_progress
            total_volume = int(latest.volume or 0)
            relative_volume = total_volume / avg_volume if avg_volume else None

            quote = self._safe_quote(broker, symbol)
            symbol_metadata = metadata.get(symbol, {})
            latest_news = news.get(symbol)
            sector = symbol_metadata.get("sector")

            # Live float lookups (FMP -> Yahoo fallback, see float_lookup) are slow and
            # FMP's share of them is quota-limited - spending one on a symbol that's
            # already failing the cheap, locally-computable pillars wastes it on
            # something that can't qualify anyway. Only look up float once a symbol
            # already clears enough of the rest to make float the deciding pillar.
            cheap_pillars = sum([
                relative_volume is not None and relative_volume >= self.strategy.min_relative_volume,
                total_volume >= self.strategy.min_total_volume,
                percent_change >= self.strategy.min_percent_change,
                self.strategy.preferred_min_price <= price <= self.strategy.preferred_max_price,
            ])
            if cheap_pillars >= 3:
                float_shares = float_lookup.float_shares(symbol) or symbol_metadata.get("float_shares")
            else:
                float_shares = symbol_metadata.get("float_shares")
            news_headline = latest_news.get("headline") if latest_news else None
            news_category, news_sentiment = classify_catalyst(news_headline)
            candidate = StockCandidate(
                symbol=symbol,
                price=price,
                percent_change=percent_change,
                relative_volume=relative_volume or 0.0,
                total_volume=total_volume,
                float_shares=float_shares,
                has_news=latest_news is not None,
                news_headline=news_headline,
                news_category=news_category,
                news_sentiment=news_sentiment,
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
                    news_headline=news_headline,
                    news_url=latest_news.get("url") if latest_news else None,
                    news_category=news_category,
                    news_sentiment=news_sentiment,
                    score=score,
                    signal=signal,
                    reasons=reasons,
                )
            )

        results.sort(key=lambda item: (item.score, item.percent_change, item.total_volume), reverse=True)
        return ScannerResponse(results=results, scanned_count=len(clean_symbols))

    def scan_universe(self, limit: int = 25, max_symbols: int = 250) -> ScannerResponse:
        """Three candidate sources, merged every cycle:

        1. A true WHOLE-MARKET sweep - every tradable NASDAQ/NYSE/AMEX common stock
           (~8,000+), batched through SIP daily bars and screened against the actual
           pillar math (price, % change, total volume, relative volume). This is the
           Ross-style scanner: nothing needs to be on any list in advance to be found.
        2. Alpaca's top-gainers screener - a fast secondary net, and the fallback if
           a sweep fails mid-cycle.
        3. The static universe list - pre-screened small-floats worth watching even
           on days they haven't (yet) moved enough to surface in 1 or 2.

        The full scan (quotes, news, float lookups) then runs only on this merged
        shortlist - the sweep does its filtering on nothing but bar math, so covering
        the whole market stays cheap."""
        symbols = self.load_universe()[: max(1, min(max_symbols, 1000))]
        sweep_hits, swept_count = self.full_market_sweep()
        merged = sorted(set(symbols) | self.todays_gainers() | sweep_hits)
        response = self.scan(merged)
        ranked = response.results[: max(1, min(limit, 100))]
        return ScannerResponse(results=ranked, scanned_count=response.scanned_count, swept_count=swept_count)

    def realtime_gap_candidates(self) -> list[StockCandidate]:
        """The Ross lane: a LIVE gap scan of the whole market with zero data lag,
        producing fully-built candidates for immediate entry evaluation. This is what
        he's watching at the open that the lagged sweep can't see until ~9:46.

        Sources are chosen so nothing is fabricated:
        - price and gap%% - real-time snapshot latest trade vs yesterday's close
        - relative volume - today's IEX volume vs a 20-day IEX average, same-source so
          the ratio is unbiased even though IEX is a slice of consolidated volume
        - total volume - today's CONSOLIDATED (SIP) bar when it exists; before it does
          (the first ~16 minutes), the pillar simply scores zero and a candidate must
          earn its 4-of-5 from gap, price, float, and relative volume instead
        - float - the FMP->Yahoo->local chain

        Failure-tolerant everywhere: any layer failing just narrows this cycle's lane."""
        symbols = self._tradable_symbols()
        if not symbols:
            return []

        broker = AlpacaBroker()
        surviving: list[tuple[float, str, float, int]] = []  # (gap_pct, symbol, price, iex_volume)
        for i in range(0, len(symbols), SWEEP_CHUNK_SIZE):
            chunk = symbols[i : i + SWEEP_CHUNK_SIZE]
            try:
                snaps = broker.snapshots(chunk)
            except Exception:
                continue
            for symbol, snap in snaps.items():
                price = snap.get("price")
                prev_close = snap.get("prev_close")
                if not price or not prev_close:
                    continue
                if len(symbol) == 5 and symbol[-1] in _DERIVATIVE_SUFFIXES:
                    continue
                if not (self.strategy.preferred_min_price <= price <= self.strategy.preferred_max_price):
                    continue
                gap_pct = (price / prev_close - 1) * 100
                if gap_pct < self.strategy.min_percent_change:
                    continue
                if snap.get("today_volume_iex", 0) < GAP_LANE_MIN_IEX_VOLUME:
                    continue
                surviving.append((gap_pct, symbol, price, snap["today_volume_iex"]))

        surviving.sort(reverse=True)
        surviving = surviving[:GAP_LANE_MAX_CANDIDATES]
        if not surviving:
            return []

        survivors = [symbol for _, symbol, _, _ in surviving]
        end = datetime.now(UTC)
        start = end - timedelta(days=40)

        # Same-source (IEX/IEX) baseline for relative volume.
        try:
            from alpaca.data.enums import DataFeed

            iex_bars = broker.daily_bars(survivors, start=start, end=end - timedelta(minutes=1), feed=DataFeed.IEX)
        except Exception:
            iex_bars = None

        # Consolidated volume for the absolute pillar, where a today-bar exists yet.
        try:
            sip_bars = broker.daily_bars(survivors, start=start, end=end - timedelta(minutes=SIP_RECENCY_BUFFER_MINUTES))
        except Exception:
            sip_bars = None

        session_progress = _session_progress_fraction(end)
        today = datetime.now(MARKET_TZ).date()
        metadata = self.load_metadata()
        # A stock gapping hard premarket almost always has a real catalyst behind it -
        # "some news event explains the volume and price spike" is one of the five
        # pillars this whole strategy is built on. The gap lane never checked news at
        # all before this; only ~25 survivors reach here, so one batched call is cheap.
        news = self._safe_news(broker, survivors, start=end - timedelta(days=3), end=end)

        candidates: list[StockCandidate] = []
        for gap_pct, symbol, price, iex_volume_today in surviving:
            relative_volume = 0.0
            if iex_bars is not None:
                history = [
                    int(bar.volume or 0)
                    for bar in iex_bars.data.get(symbol, [])
                    if bar.timestamp.astimezone(MARKET_TZ).date() != today
                ][-AVG_VOLUME_WINDOW:]
                if history:
                    baseline = (sum(history) / len(history)) * session_progress
                    relative_volume = iex_volume_today / baseline if baseline else 0.0

            total_volume = 0
            if sip_bars is not None:
                todays_sip = [
                    bar for bar in sip_bars.data.get(symbol, [])
                    if bar.timestamp.astimezone(MARKET_TZ).date() == today
                ]
                if todays_sip:
                    total_volume = int(todays_sip[0].volume or 0)

            float_shares = float_lookup.float_shares(symbol) or metadata.get(symbol, {}).get("float_shares")
            symbol_news = news.get(symbol)
            news_headline = symbol_news.get("headline") if symbol_news else None
            news_category, news_sentiment = classify_catalyst(news_headline)
            candidates.append(
                StockCandidate(
                    symbol=symbol,
                    price=price,
                    percent_change=round(gap_pct, 2),
                    relative_volume=round(relative_volume, 2),
                    total_volume=total_volume,
                    float_shares=float_shares,
                    has_news=symbol_news is not None,
                    news_headline=news_headline,
                    news_category=news_category,
                    news_sentiment=news_sentiment,
                    sector=metadata.get(symbol, {}).get("sector"),
                    is_leading_gainer=True,
                )
            )
        return candidates

    def _tradable_symbols(self) -> list[str]:
        """All tradable symbols, cached per calendar day - listings barely change
        intraday and the asset fetch is ~14K rows."""
        today = datetime.now(UTC).date()
        if _tradable_symbols_cache["date"] == today and _tradable_symbols_cache["symbols"]:
            return _tradable_symbols_cache["symbols"]
        try:
            symbols = AlpacaBroker().all_tradable_symbols()
        except Exception:
            return _tradable_symbols_cache["symbols"]  # stale beats nothing mid-day
        _tradable_symbols_cache["date"] = today
        _tradable_symbols_cache["symbols"] = symbols
        return symbols

    def full_market_sweep(self) -> tuple[set[str], int]:
        """Screens EVERY tradable symbol against the four locally-computable pillars
        (price $2-$20, up 10%+, 1M+ shares traded, 5x relative volume) using nothing
        but batched daily bars - no per-symbol API calls, so the whole market costs
        ~28 requests. A symbol clearing all four is already a 4-of-5 candidate before
        float is even looked up, i.e. it qualifies no matter what float says.

        Returns (hit symbols capped at SWEEP_MAX_HITS by biggest move, total symbols
        swept). Failure-tolerant per chunk: one bad batch skips 300 symbols for one
        cycle, not the sweep."""
        symbols = self._tradable_symbols()
        if not symbols:
            return set(), 0

        broker = AlpacaBroker()
        end = datetime.now(UTC) - timedelta(minutes=SIP_RECENCY_BUFFER_MINUTES)
        start = end - timedelta(days=40)  # ~AVG_VOLUME_WINDOW trading days plus buffer
        session_progress = _session_progress_fraction(end)

        hits: list[tuple[float, str]] = []
        swept = 0
        for i in range(0, len(symbols), SWEEP_CHUNK_SIZE):
            chunk = symbols[i : i + SWEEP_CHUNK_SIZE]
            try:
                bars = broker.daily_bars(chunk, start=start, end=end)
            except Exception:
                continue
            for symbol in chunk:
                symbol_bars = list(bars.data.get(symbol, []))
                if len(symbol_bars) < 2:
                    continue
                swept += 1
                latest = symbol_bars[-1]
                # On any trading day that hasn't closed yet (this covers premarket too,
                # not just 9:30-16:00), a "latest" bar from a PREVIOUS session means
                # today's consolidated bar doesn't exist yet. Scoring it would present
                # YESTERDAY's runners as if they're moving right now - the real-time
                # gap lane owns that window with live data; the sweep stays honest and
                # silent until its own data is actually today's. Keyed on REAL
                # wall-clock time, NOT session_progress: progress is computed from the
                # lagged timestamp, which reads "pre-open" (1.0) during exactly the
                # windows this guard needs to catch.
                now_local = datetime.now(MARKET_TZ)
                today_not_yet_closed = now_local.weekday() < 5 and now_local.time() < MARKET_CLOSE
                if today_not_yet_closed and latest.timestamp.astimezone(MARKET_TZ).date() != now_local.date():
                    continue
                previous = symbol_bars[-2]
                price = float(latest.close)
                if not (self.strategy.preferred_min_price <= price <= self.strategy.preferred_max_price):
                    continue
                previous_close = float(previous.close)
                percent_change = ((price - previous_close) / previous_close) * 100 if previous_close else 0.0
                if percent_change < self.strategy.min_percent_change:
                    continue
                total_volume = int(latest.volume or 0)
                if total_volume < self.strategy.min_total_volume:
                    continue
                prior_bars = symbol_bars[:-1][-AVG_VOLUME_WINDOW:]
                avg_volume = (sum(int(bar.volume or 0) for bar in prior_bars) / max(len(prior_bars), 1)) * session_progress
                if not avg_volume or total_volume / avg_volume < self.strategy.min_relative_volume:
                    continue
                if len(symbol) == 5 and symbol[-1] in _DERIVATIVE_SUFFIXES:
                    continue
                hits.append((percent_change, symbol))

        hits.sort(reverse=True)
        return {symbol for _, symbol in hits[:SWEEP_MAX_HITS]}, swept

    def todays_gainers(self) -> set[str]:
        """Symbols from Alpaca's live top-gainers screener worth scanning: common shares
        (no warrants/units/rights) inside the strategy's price range, already up at least
        the strategy's minimum move. Failure-tolerant - a screener hiccup just means this
        cycle scans the static universe alone, same as before this existed."""
        try:
            gainers = AlpacaBroker().top_gainers(top=50)
        except Exception:
            return set()

        picked: set[str] = set()
        for gainer in gainers:
            symbol = str(gainer.get("symbol", "")).upper()
            try:
                price = float(gainer.get("price") or 0)
                percent_change = float(gainer.get("percent_change") or 0)
            except (TypeError, ValueError):
                continue
            if not _COMMON_STOCK_SYMBOL.fullmatch(symbol):
                continue
            if len(symbol) == 5 and symbol[-1] in _DERIVATIVE_SUFFIXES:
                continue
            if not (self.strategy.preferred_min_price <= price <= self.strategy.preferred_max_price):
                continue
            if percent_change < self.strategy.min_percent_change:
                continue
            picked.add(symbol)
        return picked

    def load_universe(self) -> list[str]:
        if not self.universe_path.exists():
            return []
        symbols = []
        for line in self.universe_path.read_text(encoding="utf-8").splitlines():
            symbol = line.strip().upper()
            if symbol and not symbol.startswith("#"):
                symbols.append(symbol)
        return sorted(set(symbols))

    def universe_age_days(self) -> int | None:
        """Days since scripts/build_universe.py last regenerated the universe list, read
        from the build-date it stamps into its own header comment. None if the file's
        missing or wasn't written by that script (e.g. hand-edited) - staleness can't be
        judged either way, so this deliberately doesn't guess. Float, price, and volume
        all drift over time; nothing re-runs that script automatically, so this is what
        actually surfaces "this data is old" instead of it going silently stale forever."""
        if not self.universe_path.exists():
            return None
        text = self.universe_path.read_text(encoding="utf-8")
        first_line = text.splitlines()[0] if text else ""
        match = _UNIVERSE_BUILD_DATE_PATTERN.search(first_line)
        if not match:
            return None
        built_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        return (datetime.now(UTC).date() - built_date).days

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

