# Rolling Papers Bot

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

## Build Windows Executable

Install dependencies, then run:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --name "Rolling Papers Bot" --onefile --add-data "static;static" --add-data "data;data" app\launcher.py
```

The executable is created at:

```text
dist\Rolling Papers Bot.exe
```

Keep a local `.env` file next to the executable or run it from the project folder so API keys stay outside the bundled app.

## Safety Defaults

- Paper trading is enabled by default.
- Live trading is blocked unless `ALLOW_LIVE_TRADING=true` and `ALPACA_PAPER=false`.
- Orders are checked against daily loss, trade count, and position value limits.

This is not financial advice. Test thoroughly before risking real money.
