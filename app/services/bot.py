from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.config import settings
from app.models import BotStatus, Signal, TradeRequest
from app.services import trade_log
from app.services.live_setup import build_pullback_setup
from app.services.risk import RiskManager
from app.services.scanner import MarketScanner
from app.strategies.small_account_pullback import SmallAccountPullbackStrategy

MARKET_TZ = ZoneInfo("America/New_York")


def current_trading_day() -> date:
    return datetime.now(MARKET_TZ).date()


class TradingBot:
    def __init__(self) -> None:
        self.status = BotStatus(
            running=False,
            symbol=settings.bot_symbol.upper(),
            paper=settings.alpaca_paper,
        )
        self.strategy = SmallAccountPullbackStrategy()
        self.risk = RiskManager()
        self.scanner = MarketScanner()
        self._trading_day = current_trading_day()

    def refresh_status(self) -> BotStatus:
        today = current_trading_day()
        if today != self._trading_day:
            self._trading_day = today
            self.status.trades_today = 0

        try:
            self.status.daily_pnl = AlpacaBroker().daily_pnl()
        except Exception:
            pass  # keep the last known value if Alpaca isn't reachable/configured

        return self.status

    def start(self) -> BotStatus:
        self.refresh_status()
        self.status.running = True
        self.status.last_message = "Bot started"
        return self.status

    def stop(self) -> BotStatus:
        self.refresh_status()
        self.status.running = False
        self.status.last_message = "Bot stopped"
        return self.status

    def tick(self) -> BotStatus:
        self.refresh_status()
        if not self.status.running:
            self.status.last_message = "Bot is not running"
            return self.status

        try:
            setup = build_pullback_setup(self.status.symbol, self.scanner)
            decision = self.strategy.evaluate(setup)
        except BrokerUnavailable as exc:
            self.status.last_message = str(exc)
            return self.status
        except Exception as exc:
            self.status.last_message = f"Could not evaluate {self.status.symbol}: {exc}"
            return self.status

        self.status.last_signal = decision.signal
        reason = decision.reasons[-1] if decision.reasons else ""
        if decision.signal == Signal.buy:
            target = f", target ~${decision.first_target:.2f}" if decision.first_target is not None else ""
            self.status.last_message = (
                f"BUY signal for {self.status.symbol} ({decision.confidence:.0%} confidence, "
                f"entry ~${setup.proposed_entry:.2f}, stop ~${setup.proposed_stop:.2f}{target})"
            )
        elif decision.signal == Signal.sell:
            self.status.last_message = f"SELL/exit signal for {self.status.symbol}: {reason}"
        else:
            self.status.last_message = reason or "No trade signal"
        return self.status

    def submit_trade(self, trade: TradeRequest) -> dict:
        self.refresh_status()
        self.risk.validate(trade, self.status.trades_today, self.status.daily_pnl)
        try:
            broker = AlpacaBroker()
            order = broker.submit_market_order(
                trade.symbol,
                trade.qty,
                trade.side.value,
                take_profit_price=trade.take_profit_price,
                stop_loss_price=trade.stop_loss_price,
            )
        except BrokerUnavailable as exc:
            raise ValueError(str(exc)) from exc

        self.status.trades_today += 1
        self.status.last_signal = trade.side
        self.status.last_message = f"Submitted {trade.side.value} order for {trade.qty} {trade.symbol.upper()}"
        trade_log.record_trade(
            order_id=str(order.id),
            symbol=order.symbol,
            side=trade.side.value,
            qty=trade.qty,
            status=str(order.status.value if hasattr(order.status, "value") else order.status),
            stop_loss_price=trade.stop_loss_price,
            take_profit_price=trade.take_profit_price,
        )
        return {"id": str(order.id), "status": str(order.status), "symbol": order.symbol}


bot = TradingBot()
