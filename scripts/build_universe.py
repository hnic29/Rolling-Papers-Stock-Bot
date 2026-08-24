"""Builds data/stock_universe.txt from a live screen of Alpaca's tradable US equities,
instead of a hand-picked list. Screens for the actual profile this strategy targets:
liquid, moderately priced, and genuinely small-float - not mega-caps. The prior list
was ~110 popular names (AAPL, TSLA, NVDA, ...) that almost universally have floats in
the hundreds of millions to billions, structurally unable to ever clear the strategy's
float pillar.

Two stages to keep this fast: a cheap batched price/volume pass over the whole tradable
universe first (Alpaca daily bars, chunked), then a float check (yfinance) only on
symbols that already cleared that bar - so the slow per-symbol lookup only runs on
serious candidates, not all ~8,000 tradable tickers.

Usage: .venv/Scripts/python scripts/build_universe.py
"""

import csv
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yfinance as yf
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `app.*` resolves regardless of how this script is invoked

from app.brokers.alpaca_broker import AlpacaBroker  # noqa: E402
from app.strategies.small_account_pullback import SmallAccountPullbackStrategy  # noqa: E402

UNIVERSE_PATH = ROOT / "data" / "stock_universe.txt"
METADATA_PATH = ROOT / "data" / "symbol_metadata.csv"

MIN_PRICE = SmallAccountPullbackStrategy.preferred_min_price
MAX_PRICE = SmallAccountPullbackStrategy.preferred_max_price
# NOT the strategy's real min_total_volume (1M) - a genuine low-float runner is usually
# quiet most days and only spikes to millions of shares on the day it actually moves, so
# requiring a sustained 30-day *average* of 1M would select for the opposite profile
# (steadily-liquid, usually higher-float names). This floor only exists to exclude
# truly dead/halted tickers; the live scanner already checks TODAY's volume for real.
MIN_AVG_VOLUME = 50_000
# The strategy's exact float gate, not a looser approximation of it - headroom here just
# produces symbols that pass this screen but can never actually clear score_candidate's
# real check, the same structural mismatch that made the old hand-picked list useless.
MAX_FLOAT = SmallAccountPullbackStrategy.max_float
CHUNK_SIZE = 300


def _clean_symbol(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,5}", symbol))


def load_tradable_symbols() -> list[str]:
    broker = AlpacaBroker()
    request = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
    assets = broker.client.get_all_assets(request)
    return sorted({
        a.symbol
        for a in assets
        if a.tradable and a.exchange.value in ("NASDAQ", "NYSE", "AMEX") and _clean_symbol(a.symbol)
    })


def screen_by_price_and_volume(symbols: list[str]) -> list[str]:
    """Cheap, batched first pass - keeps the slow per-symbol float lookup limited to
    real candidates instead of the whole tradable universe."""
    broker = AlpacaBroker()
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    survivors = []

    for i in range(0, len(symbols), CHUNK_SIZE):
        chunk = symbols[i : i + CHUNK_SIZE]
        print(f"Price/volume pass: {min(i + CHUNK_SIZE, len(symbols))}/{len(symbols)}...", flush=True)
        try:
            bars = broker.daily_bars(chunk, start=start, end=end)
        except Exception as exc:
            print(f"  chunk failed ({exc}), skipping")
            continue
        for symbol in chunk:
            symbol_bars = list(bars.data.get(symbol, []))
            if len(symbol_bars) < 5:
                continue
            price = float(symbol_bars[-1].close)
            if not (MIN_PRICE <= price <= MAX_PRICE):
                continue
            avg_volume = sum(int(b.volume or 0) for b in symbol_bars) / len(symbol_bars)
            if avg_volume < MIN_AVG_VOLUME:
                continue
            survivors.append(symbol)

    return survivors


def screen_by_float(symbols: list[str]) -> dict[str, int]:
    """Slow per-symbol pass (yfinance) - only runs on symbols that already cleared
    price and volume, so this stays a few hundred lookups, not thousands."""
    qualifying: dict[str, int] = {}
    for i, symbol in enumerate(symbols, 1):
        try:
            float_shares = yf.Ticker(symbol).info.get("floatShares")
        except Exception:
            float_shares = None
        if float_shares and float_shares <= MAX_FLOAT:
            qualifying[symbol] = int(float_shares)
            print(f"  [{i}/{len(symbols)}] {symbol}: MATCH, float={int(float_shares):,}", flush=True)
        elif i % 25 == 0:
            print(f"  [{i}/{len(symbols)}] checked, {len(qualifying)} matches so far", flush=True)
        time.sleep(0.4)
    return qualifying


def main() -> None:
    print("Loading Alpaca's tradable US equity universe...", flush=True)
    tradable = load_tradable_symbols()
    print(f"{len(tradable)} clean, tradable symbols on NASDAQ/NYSE/AMEX", flush=True)

    price_volume_survivors = screen_by_price_and_volume(tradable)
    print(
        f"\n{len(price_volume_survivors)} symbols clear price (${MIN_PRICE}-${MAX_PRICE}) "
        f"and volume (>={MIN_AVG_VOLUME:,}/day)",
        flush=True,
    )

    print("\nChecking float on survivors (this is the slow part)...", flush=True)
    matches = screen_by_float(price_volume_survivors)
    print(f"\n{len(matches)} symbols clear float (<= {MAX_FLOAT:,} shares) too - these are the real candidates", flush=True)

    if len(matches) < 10:
        print(
            f"\nOnly {len(matches)} matches - that's suspiciously low and more likely a screening "
            "problem (bad filter, blocked/rate-limited lookups) than reality. Not overwriting the "
            "existing universe/metadata files - investigate before re-running.",
            flush=True,
        )
        return

    UNIVERSE_PATH.write_text(
        "# Built by scripts/build_universe.py - live-screened for price $2-$20, "
        f"avg volume >= {MIN_AVG_VOLUME:,}/day, and float <= {MAX_FLOAT:,} shares.\n"
        + "\n".join(sorted(matches)) + "\n",
        encoding="utf-8",
    )

    existing: dict[str, dict] = {}
    if METADATA_PATH.exists():
        with METADATA_PATH.open(newline="", encoding="utf-8") as handle:
            existing = {row["symbol"].upper(): row for row in csv.DictReader(handle) if row.get("symbol")}

    with METADATA_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "float_shares", "sector"])
        writer.writeheader()
        for symbol in sorted(matches):
            sector = existing.get(symbol, {}).get("sector", "")
            writer.writerow({"symbol": symbol, "float_shares": matches[symbol], "sector": sector})

    print(f"\nWrote {len(matches)} symbols to {UNIVERSE_PATH} and {METADATA_PATH}", flush=True)


if __name__ == "__main__":
    main()
