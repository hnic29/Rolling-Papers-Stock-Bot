from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from datetime import datetime

from alpaca.data.enums import DataFeed
from alpaca.data.historical import NewsClient, StockHistoricalDataClient
from alpaca.data.requests import NewsRequest, StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

from app.config import settings


class BrokerUnavailable(RuntimeError):
    pass


class AlpacaBroker:
    def __init__(self) -> None:
        if not settings.alpaca_api_key or not settings.alpaca_secret_key:
            raise BrokerUnavailable("Missing Alpaca API credentials. Add paper keys to .env.")
        if not settings.alpaca_paper and not settings.allow_live_trading:
            raise BrokerUnavailable("Live trading is blocked. Set ALLOW_LIVE_TRADING=true explicitly.")

        self.client = TradingClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )
        self.data_client = StockHistoricalDataClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
        )
        self.news_client = NewsClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
        )

    def account(self):
        return self.client.get_account()

    def positions(self):
        return self.client.get_all_positions()

    def submit_market_order(self, symbol: str, qty: int, side: str):
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        order = MarketOrderRequest(
            symbol=symbol.upper(),
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        return self.client.submit_order(order_data=order)

    def latest_quote(self, symbol: str) -> dict:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol.upper(), feed=DataFeed.IEX)
        quotes = self.data_client.get_stock_latest_quote(request)
        quote = quotes[symbol.upper()]
        bid = float(quote.bid_price or 0)
        ask = float(quote.ask_price or 0)
        midpoint = round((bid + ask) / 2, 4) if bid and ask else None
        return {
            "symbol": symbol.upper(),
            "bid_price": bid,
            "bid_size": int(quote.bid_size or 0),
            "ask_price": ask,
            "ask_size": int(quote.ask_size or 0),
            "midpoint": midpoint,
            "timestamp": quote.timestamp.isoformat() if quote.timestamp else None,
        }

    def daily_bars(self, symbols: list[str], start: datetime, end: datetime):
        request = StockBarsRequest(
            symbol_or_symbols=[symbol.upper() for symbol in symbols],
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        return self.data_client.get_stock_bars(request)

    def minute_bars(self, symbol: str, start: datetime, end: datetime, limit: int = 120) -> list[dict]:
        request = StockBarsRequest(
            symbol_or_symbols=symbol.upper(),
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            limit=limit,
            feed=DataFeed.IEX,
        )
        bars = self.data_client.get_stock_bars(request)
        return [
            {
                "timestamp": bar.timestamp.isoformat(),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume or 0),
            }
            for bar in bars.data.get(symbol.upper(), [])
        ]

    def latest_news(self, symbols: list[str], start: datetime, end: datetime, limit: int = 50):
        request = NewsRequest(
            symbols=",".join(symbol.upper() for symbol in symbols),
            start=start,
            end=end,
            limit=limit,
            include_content=False,
            exclude_contentless=True,
        )
        return self.news_client.get_news(request)
