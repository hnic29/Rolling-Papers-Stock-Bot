from app.config import settings
from app.models import TradeRequest


class RiskManager:
    def validate(self, trade: TradeRequest, trades_today: int, daily_pnl: float) -> None:
        if trade.side.value not in {"buy", "sell"}:
            raise ValueError("Only buy and sell orders can be submitted.")
        if trade.qty <= 0:
            raise ValueError("Quantity must be positive.")
        if trades_today >= settings.max_trades_per_day:
            raise ValueError("Max trades per day reached.")
        if daily_pnl <= -abs(settings.max_daily_loss):
            raise ValueError("Max daily loss reached.")

        if trade.estimated_price is not None:
            estimated_position_value = trade.qty * trade.estimated_price
            if estimated_position_value > settings.max_position_value:
                raise ValueError("Requested trade exceeds max position value setting.")

        if trade.stop_loss_price is not None or trade.take_profit_price is not None:
            if trade.qty != int(trade.qty):
                raise ValueError("Stop-loss/take-profit orders require a whole-share quantity.")
            if trade.stop_loss_price is not None and trade.stop_loss_price <= 0:
                raise ValueError("Stop-loss price must be positive.")
            if trade.take_profit_price is not None and trade.take_profit_price <= 0:
                raise ValueError("Take-profit price must be positive.")
            if trade.stop_loss_price is not None and trade.take_profit_price is not None:
                if trade.side.value == "buy" and trade.stop_loss_price >= trade.take_profit_price:
                    raise ValueError("Stop-loss must be below take-profit for a buy order.")
                if trade.side.value == "sell" and trade.stop_loss_price <= trade.take_profit_price:
                    raise ValueError("Stop-loss must be above take-profit for a sell order.")
