from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.models import AutoScannerRequest, BotStatus, PullbackSetup, ScannerRequest, TradeRequest
from app.services.bot import bot
from app.services.scanner import MarketScanner

app = FastAPI(title="Stockbot", version="0.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
scanner = MarketScanner()


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/api/status", response_model=BotStatus)
def status():
    return bot.status


@app.post("/api/start", response_model=BotStatus)
def start():
    return bot.start()


@app.post("/api/stop", response_model=BotStatus)
def stop():
    return bot.stop()


@app.post("/api/tick", response_model=BotStatus)
def tick():
    return bot.tick()


@app.post("/api/strategy/evaluate")
def evaluate_strategy(setup: PullbackSetup):
    return bot.strategy.evaluate(setup)


@app.post("/api/scanner")
def scan_market(request: ScannerRequest):
    try:
        return scanner.scan(request.symbols)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not scan market: {exc}") from exc


@app.post("/api/scanner/auto")
def scan_market_universe(request: AutoScannerRequest):
    try:
        return scanner.scan_universe(limit=request.limit, max_symbols=request.max_symbols)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not scan market universe: {exc}") from exc


@app.get("/api/account")
def account():
    try:
        acct = AlpacaBroker().account()
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": acct.id,
        "status": acct.status,
        "currency": acct.currency,
        "buying_power": acct.buying_power,
        "equity": acct.equity,
        "paper": bot.status.paper,
    }


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    try:
        return AlpacaBroker().latest_quote(symbol)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca quote for {symbol.upper()}: {exc}") from exc


@app.get("/api/bars/{symbol}")
def bars(symbol: str, limit: int = 120, trading_date: date | None = None):
    try:
        if trading_date:
            market_tz = ZoneInfo("America/New_York")
            start = datetime.combine(trading_date, time(9, 30), tzinfo=market_tz).astimezone(UTC)
            end = datetime.combine(trading_date, time(16, 0), tzinfo=market_tz).astimezone(UTC)
            limit = min(max(limit, 1), 390)
        else:
            end = datetime.now(UTC)
            start = end - timedelta(days=7)
        return {"symbol": symbol.upper(), "bars": AlpacaBroker().minute_bars(symbol, start=start, end=end, limit=limit)}
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Alpaca bars for {symbol.upper()}: {exc}") from exc


@app.post("/api/trade")
def trade(request: TradeRequest):
    try:
        return bot.submit_trade(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
