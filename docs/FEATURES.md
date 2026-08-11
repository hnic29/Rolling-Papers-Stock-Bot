# Rolling Papers Bot Feature Guide

Rolling Papers Bot is a paper-first day-trading bot and dashboard. It is currently built for learning, testing, and paper execution before any live-trading work is enabled.

This is not financial advice. Do not use live trading until the strategy, data feeds, logs, risk limits, and broker behavior have been tested thoroughly.

## Current Features

### Web Dashboard

The dashboard is served from:

```text
http://127.0.0.1:8000
```

Use it to:

- Start and stop the bot state.
- Run one strategy tick.
- View current bot status.
- Submit a manual paper order after Alpaca paper credentials are configured.

The dashboard background is a CSS-only animated aurora (starfield plus drifting gradient bands), defined in `static/styles.css` — no image asset behind it.

### Bot State

The bot tracks:

- Whether it is running.
- The configured symbol.
- Whether Alpaca is in paper mode.
- The last strategy signal.
- The latest status message.
- Trades submitted today.
- Realized P&L placeholder for the day.

Check status with:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/status
```

### Paper-First Alpaca Broker

Alpaca support is implemented in:

```text
app/brokers/alpaca_broker.py
```

Safety defaults:

- `ALPACA_PAPER=true`
- `ALLOW_LIVE_TRADING=false`
- Live trading is blocked unless `ALPACA_PAPER=false` and `ALLOW_LIVE_TRADING=true`.

To configure Alpaca:

1. Copy `.env.example` to `.env`.
2. Add Alpaca paper API credentials:

```text
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_PAPER=true
```

You can also configure keys from the dashboard `Settings` tab. Saved settings are written to `.env`; secret values are masked when displayed back in the browser.

### Manual Paper Orders

Manual order submission is available through the dashboard and API:

```text
POST /api/trade
```

Example request:

```json
{
  "symbol": "AAPL",
  "qty": 1,
  "side": "buy",
  "estimated_price": 190.25
}
```

Before submission, the order is checked by the risk manager.

### Alpaca Ticker

The homepage includes an Alpaca ticker panel. Enter a stock symbol and press `Refresh` to fetch the latest bid, ask, midpoint, and timestamp from Alpaca market data.

The ticker uses:

```text
GET /api/quote/{symbol}
```

Example:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/quote/AAPL
```

If Alpaca credentials are missing or market data access fails, the dashboard shows the API error in the status message area.

### Candlestick Chart

The homepage includes a candlestick chart for the symbol in the manual paper order form.

Use it by:

1. Entering a symbol in the manual paper order `Symbol` field.
2. Optionally selecting a historical date.
3. Pressing `Refresh Chart`, or changing the symbol/date field.

The chart uses:

```text
GET /api/bars/{symbol}
```

It currently draws Alpaca IEX 1-minute candles directly on an HTML canvas. Without a selected date, it fetches recent candles. With a selected date, it fetches regular market hours for that trading day. Hover over a candle to see the exact timestamp, open, high, low, close, and volume.

### Market Scanner

The homepage includes a market scanner panel.

Use `Scan` to scan a comma-separated watchlist.

Use `Auto Scan` to scan the local stock universe in:

```text
data/stock_universe.txt
```

The scanner currently:

- Fetches recent Alpaca daily bars.
- Calculates latest price.
- Calculates percent change from the prior daily close.
- Calculates relative volume using recent average daily volume.
- Fetches latest bid/ask when available.
- Fetches recent Alpaca news to flag possible catalysts.
- Fetches float data from Financial Modeling Prep when `FMP_API_KEY` is configured.
- Falls back to local sector and float metadata from `data/symbol_metadata.csv`.
- Scores each symbol against the stock-selection pillars from the strategy.
- Sorts stronger candidates first.

API endpoint:

```text
POST /api/scanner
POST /api/scanner/auto
```

Example request:

```json
{
  "symbols": ["AAPL", "TSLA", "NVDA", "AMD", "PLTR"]
}
```

The scanner does not yet discover every market gainer by itself, and it does not yet know float/news for arbitrary symbols. Those require another data source or a broader screener feed. For now, use it to scan a watchlist or symbols from an external movers list.

`Auto Scan` is the first market-wide upgrade. It scans a curated local universe automatically, ranks candidates, and avoids requiring you to type symbols one by one. The deeper enrichment layer now adds Alpaca news catalysts and local sector/float metadata. It is not yet a full exchange-wide screener.

### Financial Modeling Prep Float Data

FMP is optional and can be used on the free tier if your account has access to the `shares-float` endpoint.

Configure it in `.env`:

```text
FMP_API_KEY=your_fmp_key
```

The scanner calls:

```text
https://financialmodelingprep.com/stable/shares-float?symbol=AAPL
```

Expected fields include `floatShares`, `outstandingShares`, `freeFloat`, `date`, and `source` when available.

### Risk Manager

Risk checks live in:

```text
app/services/risk.py
```

Current checks:

- Only `buy` and `sell` orders can be submitted.
- Quantity must be positive.
- Max trades per day is enforced.
- Max daily loss is enforced.
- Estimated position value is checked when `estimated_price` is provided.

Risk settings are configured in `.env`:

```text
MAX_DAILY_LOSS=100
MAX_TRADES_PER_DAY=5
MAX_POSITION_VALUE=1000
```

### Small Account Pullback Strategy

The strategy from the YouTube video is implemented in:

```text
app/strategies/small_account_pullback.py
```

It models the video's core rules:

- Focus on leading gainers.
- Prefer stocks with at least `5x` relative volume.
- Prefer high total volume.
- Prefer stocks up at least `10%`.
- Prefer price between `$2` and `$20`.
- Prefer float under `20M` shares.
- Allow no-news exceptions only when the stock is an obvious leading gainer or in a hot sector.
- Wait for a first pullback pattern.
- Reject pullbacks that retrace more than 50% of the prior move.
- Require entry above the 9 EMA.
- Require the latest candle to make a new high.
- Respect exit indicators such as large sellers, hidden sellers, red tape bursts, slowing buying, topping tails, and red candles.

Because the original strategy uses scanners, Level 2, and time-and-sales interpretation, the current implementation accepts those values as structured inputs. The next planned step is wiring live scanner and market data into those fields.

## Running The App

From the project folder:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## Windows Executable

The app can be bundled as a Windows executable. The executable starts the local server and opens the dashboard in your default browser.

Build it with:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --name "Rolling Papers Bot" --onefile --add-data "static;static" --add-data "data;data" app\launcher.py
```

Output:

```text
dist\Rolling Papers Bot.exe
```

The `.env` file is intentionally not bundled. Keep `.env` outside the executable so Alpaca and FMP keys stay private.

## Testing

Run tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Current tests cover:

- Risk validation.
- Small account pullback strategy buy/reject behavior.

## Planned Features

- Dashboard form for strategy setup testing.
- Market-wide gainers integration.
- Float and news/catalyst enrichment.
- Historical candle loading.
- EMA calculation from real candles.
- Trade journal and export.
- Paper-trading automation loop.
- Position monitoring and exits.
- Backtesting.
- Safer daily reset and market-hours handling.
