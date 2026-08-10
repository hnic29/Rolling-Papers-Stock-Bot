# Stockbot

Paper-first day-trading bot scaffold using FastAPI and Alpaca.

Full feature documentation lives in [docs/FEATURES.md](docs/FEATURES.md).

## Setup

Python has already been installed on this machine, and this project uses a local `.venv`.

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add Alpaca paper keys.

Start the app:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000`.

## Safety Defaults

- Paper trading is enabled by default.
- Live trading is blocked unless `ALLOW_LIVE_TRADING=true` and `ALPACA_PAPER=false`.
- Orders are checked against daily loss, trade count, and position value limits.

This is not financial advice. Test thoroughly before risking real money.
