from datetime import UTC, date, datetime, time as dtime
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


def _parse_clock(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


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
        self._auto_trading_started_at: datetime | None = None

    def refresh_status(self) -> BotStatus:
        today = current_trading_day()
        if today != self._trading_day:
            self._trading_day = today
            self.status.trades_today = 0
            self.status.peak_daily_pnl = 0.0
            self.status.consecutive_losses = 0
            self.status.walked_away_for_day = False
            self.status.walk_away_reason = None
            self._auto_trading_started_at = datetime.now(UTC) if self.status.auto_trading_enabled else None

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

    def _within_trading_window(self, now: datetime) -> bool:
        local_time = now.astimezone(MARKET_TZ).time()
        return _parse_clock(settings.trading_window_start) <= local_time <= _parse_clock(settings.trading_window_end)

    def _update_session_state(self) -> None:
        """Reconcile today's realized trades against the daily walk-away rules from the
        strategy research: stop once roughly half the day's peak profit has been given
        back, or after three losses in a row. Once tripped, stays tripped until the next
        trading day rolls over in refresh_status() — coming back after walking away is
        exactly the FOMO trap those rules exist to prevent."""
        if self.status.walked_away_for_day:
            return

        realized = trade_log.todays_realized_trades(self._trading_day)
        running_pnl = 0.0
        peak = 0.0
        streak = 0
        for trade in realized:
            pnl = trade["realized_pnl"] or 0.0
            running_pnl += pnl
            peak = max(peak, running_pnl)
            streak = streak + 1 if pnl < 0 else 0

        self.status.peak_daily_pnl = round(peak, 2)
        self.status.consecutive_losses = streak

        if streak >= settings.max_consecutive_losses:
            self.status.walked_away_for_day = True
            self.status.walk_away_reason = f"{streak} losing trades in a row"
            return

        if peak > 0 and running_pnl <= peak * (1 - settings.max_daily_giveback_pct / 100):
            self.status.walked_away_for_day = True
            self.status.walk_away_reason = f"gave back more than {settings.max_daily_giveback_pct:.0f}% of today's peak profit"
            return

        submitted = trade_log.todays_submitted_trades(self._trading_day)
        if submitted:
            baseline = datetime.fromisoformat(submitted[-1]["submitted_at"])
            if baseline.tzinfo is None:
                baseline = baseline.replace(tzinfo=UTC)
        else:
            baseline = self._auto_trading_started_at

        if baseline is not None:
            idle_minutes = (datetime.now(UTC) - baseline).total_seconds() / 60
            if idle_minutes > settings.max_minutes_without_trade:
                self.status.walked_away_for_day = True
                self.status.walk_away_reason = f"no trade taken in over {settings.max_minutes_without_trade} minutes — momentum has cooled"

    def start_auto_trading(self) -> BotStatus:
        self.refresh_status()
        self.status.auto_trading_enabled = True
        self._auto_trading_started_at = datetime.now(UTC)
        self.status.last_message = "Auto-trading enabled — will scan and trade automatically while the market is open"
        return self.status

    def stop_auto_trading(self) -> BotStatus:
        self.refresh_status()
        self.status.auto_trading_enabled = False
        self._auto_trading_started_at = None
        self.status.last_message = "Auto-trading disabled"
        return self.status

    def auto_cycle(self) -> BotStatus:
        """One automation pass: scan the universe, evaluate real pullback setups for anything
        that clears the scoring gate, and submit bracket trades for genuine buy signals. Reuses
        submit_trade() so every existing risk guard (daily loss, trade cap, position cap, paper/live
        gating) applies exactly as it does to a manually submitted order."""
        self.refresh_status()
        if not self.status.auto_trading_enabled:
            return self.status

        self.status.last_automation_run_at = datetime.now(UTC).isoformat()

        self._update_session_state()
        if self.status.walked_away_for_day:
            self.status.last_message = f"Auto-trading paused for the day — {self.status.walk_away_reason}"
            return self.status

        if not self._within_trading_window(datetime.now(UTC)):
            self.status.last_message = (
                f"Auto-trading idle — outside the {settings.trading_window_start}-{settings.trading_window_end} trading window"
            )
            return self.status

        try:
            broker = AlpacaBroker()
            clock = broker.client.get_clock()
        except BrokerUnavailable as exc:
            self.status.last_message = str(exc)
            return self.status
        except Exception as exc:
            self.status.last_message = f"Automation could not check the market clock: {exc}"
            return self.status

        if not clock.is_open:
            self.status.last_message = "Auto-trading idle — market is closed"
            return self.status

        try:
            held_symbols = {position["symbol"] for position in broker.positions_as_dicts()}
        except Exception:
            held_symbols = set()

        try:
            candidates = self.scanner.scan_universe(
                limit=settings.automation_scan_limit,
                max_symbols=settings.automation_max_symbols,
            ).results
        except BrokerUnavailable as exc:
            self.status.last_message = str(exc)
            return self.status
        except Exception as exc:
            self.status.last_message = f"Automation scan failed: {exc}"
            return self.status

        qualifying = [c for c in candidates if c.score >= 4 and c.symbol not in held_symbols]

        traded: list[str] = []
        skipped: list[str] = []
        for candidate in qualifying:
            if self.status.trades_today >= settings.max_trades_per_day:
                break
            try:
                setup = build_pullback_setup(candidate.symbol, self.scanner)
                decision = self.strategy.evaluate(setup)
            except Exception:
                continue
            if decision.signal != Signal.buy:
                continue

            risk_per_share = setup.proposed_entry - setup.proposed_stop
            qty_by_risk = int(settings.risk_per_trade // risk_per_share) if risk_per_share > 0 else 0
            qty_by_capital = int(settings.max_position_value // setup.proposed_entry)
            qty = min(qty_by_risk, qty_by_capital) if qty_by_risk else qty_by_capital
            if qty < 1:
                continue

            trade = TradeRequest(
                symbol=candidate.symbol,
                qty=qty,
                side=Signal.buy,
                estimated_price=setup.proposed_entry,
                stop_loss_price=setup.proposed_stop,
                take_profit_price=decision.first_target,
            )
            try:
                self.submit_trade(trade)
                traded.append(candidate.symbol)
            except ValueError as exc:
                skipped.append(f"{candidate.symbol} ({exc})")
                continue

        if traded:
            self.status.last_message = f"Auto-trading placed orders: {', '.join(traded)}"
        elif skipped:
            self.status.last_message = f"Auto-trading found signals but skipped them: {'; '.join(skipped)}"
        else:
            self.status.last_message = f"Auto-trading scanned {len(candidates)} symbols — no qualifying buy signals"
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
