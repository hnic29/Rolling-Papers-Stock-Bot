from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.config import settings
from app.models import BotStatus, Signal, TradeRequest
from app.services.risk import RiskManager
from app.strategies.small_account_pullback import SmallAccountPullbackStrategy


class TradingBot:
    def __init__(self) -> None:
        self.status = BotStatus(
            running=False,
            symbol=settings.bot_symbol.upper(),
            paper=settings.alpaca_paper,
        )
        self.strategy = SmallAccountPullbackStrategy()
        self.risk = RiskManager()

    def start(self) -> BotStatus:
        self.status.running = True
        self.status.last_message = "Bot started"
        return self.status

    def stop(self) -> BotStatus:
        self.status.running = False
        self.status.last_message = "Bot stopped"
        return self.status

    def tick(self) -> BotStatus:
        if not self.status.running:
            self.status.last_message = "Bot is not running"
            return self.status

        signal = self.strategy.next_signal()
        self.status.last_signal = signal
        self.status.last_message = "No trade signal" if signal == Signal.hold else f"{signal.value} signal"
        return self.status

    def submit_trade(self, trade: TradeRequest) -> dict:
        self.risk.validate(trade, self.status.trades_today, self.status.realized_pnl_today)
        try:
            broker = AlpacaBroker()
            order = broker.submit_market_order(trade.symbol, trade.qty, trade.side.value)
        except BrokerUnavailable as exc:
            raise ValueError(str(exc)) from exc

        self.status.trades_today += 1
        self.status.last_signal = trade.side
        self.status.last_message = f"Submitted {trade.side.value} order for {trade.qty} {trade.symbol.upper()}"
        return {"id": str(order.id), "status": str(order.status), "symbol": order.symbol}


bot = TradingBot()
