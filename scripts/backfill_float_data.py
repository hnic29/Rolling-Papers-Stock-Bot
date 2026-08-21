"""One-time (or periodic) backfill of float-share counts into data/symbol_metadata.csv.

Sourced from Yahoo Finance via yfinance instead of FMP - float barely changes day to
day, so there's no need to depend on FMP's tight per-scan quota for this. Re-run
occasionally (monthly is plenty) to pick up new listings or real float changes.

Usage: .venv/Scripts/python scripts/backfill_float_data.py
"""

import csv
import time
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = ROOT / "data" / "stock_universe.txt"
METADATA_PATH = ROOT / "data" / "symbol_metadata.csv"


def load_universe() -> list[str]:
    symbols = []
    for line in UNIVERSE_PATH.read_text(encoding="utf-8").splitlines():
        symbol = line.strip().upper()
        if symbol and not symbol.startswith("#"):
            symbols.append(symbol)
    return sorted(set(symbols))


def load_existing_metadata() -> dict[str, dict]:
    if not METADATA_PATH.exists():
        return {}
    with METADATA_PATH.open(newline="", encoding="utf-8") as handle:
        return {row["symbol"].upper(): row for row in csv.DictReader(handle) if row.get("symbol")}


def main() -> None:
    symbols = load_universe()
    existing = load_existing_metadata()
    print(f"Backfilling float data for {len(symbols)} symbols...")

    results = []
    found = 0
    for i, symbol in enumerate(symbols, 1):
        # Preserve whatever sector value is already on file - yfinance's sector
        # vocabulary ("Technology", "Industrials", ...) doesn't match the strategy's
        # hot-sector list ("tech", "ai", "biotech", ...), so overwriting it would
        # silently break the "hot sector" hint for symbols that already have one set.
        sector = existing.get(symbol, {}).get("sector", "")
        float_shares = ""
        try:
            value = yf.Ticker(symbol).info.get("floatShares")
            if value:
                float_shares = str(int(value))
                found += 1
        except Exception as exc:
            print(f"  [{i}/{len(symbols)}] {symbol}: FAILED ({exc})")
        else:
            print(f"  [{i}/{len(symbols)}] {symbol}: float={float_shares or 'unavailable'}")
        results.append({"symbol": symbol, "float_shares": float_shares, "sector": sector})
        time.sleep(0.5)  # not a real rate-limited API, but no reason to hammer it

    with METADATA_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "float_shares", "sector"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone: {found}/{len(symbols)} symbols got a float value. Wrote {METADATA_PATH}")


if __name__ == "__main__":
    main()
