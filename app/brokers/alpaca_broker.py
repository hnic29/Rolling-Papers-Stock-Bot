from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    GetPortfolioHistoryRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from datetime import datetime

from alpaca.common.enums import Sort
from alpaca.data.enums import DataFeed
from alpaca.data.historical import NewsClient, StockHistoricalDataClient
from alpaca.data.requests import (
    NewsRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
)
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

    def daily_pnl(self) -> float:
        account = self.account()
        equity = float(account.equity)
        last_equity = float(account.last_equity) if account.last_equity is not None else equity
        return equity - last_equity

    def positions(self):
        return self.client.get_all_positions()

    def positions_as_dicts(self) -> list[dict]:
        result = []
        for position in self.positions():
            entry_price = float(position.avg_entry_price)
            current_price = float(position.current_price) if position.current_price is not None else entry_price
            result.append(
                {
                    "symbol": position.symbol,
                    "asset_class": str(position.asset_class.value if hasattr(position.asset_class, "value") else position.asset_class),
                    "side": str(position.side.value if hasattr(position.side, "value") else position.side),
                    "qty": float(position.qty),
                    "avg_entry_price": entry_price,
                    "current_price": current_price,
                    "market_value": float(position.market_value) if position.market_value is not None else 0.0,
                    "cost_basis": float(position.cost_basis) if position.cost_basis is not None else 0.0,
                    "unrealized_pl": float(position.unrealized_pl) if position.unrealized_pl is not None else 0.0,
                    "unrealized_plpc": float(position.unrealized_plpc) if position.unrealized_plpc is not None else 0.0,
                    "change_today": float(position.change_today) if position.change_today is not None else 0.0,
                }
            )
        return result

    def portfolio_history(self, period: str = "1D", timeframe: str = "5Min") -> dict:
        request = GetPortfolioHistoryRequest(period=period, timeframe=timeframe, extended_hours=True)
        history = self.client.get_portfolio_history(history_filter=request)
        return {
            "timestamp": list(history.timestamp or []),
            "equity": [float(value) if value is not None else None for value in (history.equity or [])],
            "profit_loss": [float(value) if value is not None else None for value in (history.profit_loss or [])],
            "profit_loss_pct": [float(value) if value is not None else None for value in (history.profit_loss_pct or [])],
            "base_value": float(history.base_value) if history.base_value is not None else None,
            "timeframe": history.timeframe,
        }

    def submit_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
    ):
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

        order_class = None
        take_profit = TakeProfitRequest(limit_price=round(take_profit_price, 2)) if take_profit_price else None
        stop_loss = StopLossRequest(stop_price=round(stop_loss_price, 2)) if stop_loss_price else None
        if take_profit and stop_loss:
            order_class = OrderClass.BRACKET
        elif take_profit or stop_loss:
            order_class = OrderClass.OTO

        order = MarketOrderRequest(
            symbol=symbol.upper(),
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            order_class=order_class,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )
        return self.client.submit_order(order_data=order)

    def get_order(self, order_id: str):
        return self.client.get_order_by_id(order_id)

    def open_orders(self, symbol: str):
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol.upper()])
        return self.client.get_orders(filter=request)

    def cancel_order(self, order_id: str) -> None:
        self.client.cancel_order_by_id(order_id)

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

    def historical_bars(
        self, symbol: str, start: datetime, end: datetime, limit: int = 120, sort: Sort = Sort.ASC, timeframe: TimeFrame = TimeFrame.Minute
    ) -> list[dict]:
        request = StockBarsRequest(
            symbol_or_symbols=symbol.upper(),
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            feed=DataFeed.IEX,
            sort=sort,
        )
        bars = self.data_client.get_stock_bars(request)
        result = [
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
        if sort == Sort.DESC:
            result.reverse()
        return result

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
