from app.models import TradeRequest
from app.services import bankroll


class RiskManager:
    def validate(self, trade: TradeRequest, trades_today: int, daily_pnl: float, user_settings: dict, user_id: int = 1) -> None:
        """user_settings is one person's own row from app.services.credentials
        (max_trades_per_day, max_daily_loss_pct, max_position_value_pct) - these are
        per-user limits, not a single deployment-wide policy, so nothing here reads
        the global settings singleton."""
        if trade.side.value not in {"buy", "sell"}:
            raise ValueError("Only buy and sell orders can be submitted.")
        if trade.qty <= 0:
            raise ValueError("Quantity must be positive.")

        # Everything below only gates BUYS - these limits exist to stop opening NEW
        # risk, and must never block a sell that reduces it. Blocking sells meant that
        # once the day's trade cap or loss limit tripped, an exit signal could no
        # longer close a losing position - the exact moment closing matters most.
        if trade.side.value != "buy":
            return self._validate_bracket_prices(trade)

        if trades_today >= user_settings["max_trades_per_day"]:
            raise ValueError("Max trades per day reached.")

        # Percentages of the current bankroll, not fixed dollars - see app.config's
        # comment on why (a static dollar figure doesn't track the bankroll changing).
        # Both checks are skipped entirely at a $0 bankroll: a 0% cap of nothing would
        # otherwise reject at the exact boundary (e.g. $0.00 daily P&L against a $0.00
        # cap) even when nothing has actually gone wrong, and a buy with no bankroll is
        # already correctly rejected downstream (bot._validate_against_bankroll) with a
        # much clearer message - this would just get there first with a worse one.
        current_bankroll = bankroll.current_bankroll(user_id)
        if current_bankroll > 0:
            max_daily_loss = current_bankroll * user_settings["max_daily_loss_pct"] / 100
            if daily_pnl <= -abs(max_daily_loss):
                raise ValueError("Max daily loss reached.")

            if trade.estimated_price is not None:
                estimated_position_value = trade.qty * trade.estimated_price
                max_position_value = current_bankroll * user_settings["max_position_value_pct"] / 100
                if estimated_position_value > max_position_value:
                    raise ValueError("Requested trade exceeds max position value setting.")

        self._validate_bracket_prices(trade)

    def _validate_bracket_prices(self, trade: TradeRequest) -> None:
        if trade.stop_loss_price is None and trade.take_profit_price is None:
            return
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
