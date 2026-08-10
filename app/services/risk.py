from app.config import settings
from app.models import TradeRequest


class RiskManager:
    def validate(self, trade: TradeRequest, trades_today: int, realized_pnl_today: float) -> None:
        if trade.side.value not in {"buy", "sell"}:
            raise ValueError("Only buy and sell orders can be submitted.")
        if trade.qty <= 0:
            raise ValueError("Quantity must be positive.")
        if trades_today >= settings.max_trades_per_day:
            raise ValueError("Max trades per day reached.")
        if realized_pnl_today <= -abs(settings.max_daily_loss):
            raise ValueError("Max daily loss reached.")

        if trade.estimated_price is not None:
            estimated_position_value = trade.qty * trade.estimated_price
            if estimated_position_value > settings.max_position_value:
                raise ValueError("Requested trade exceeds max position value setting.")
