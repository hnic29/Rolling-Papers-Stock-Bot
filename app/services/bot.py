from datetime import UTC, date, datetime, time as dtime
from zoneinfo import ZoneInfo

from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.config import settings
from app.models import BotStatus, Signal, TradeRequest
from app.services import bankroll, bot_state, trade_log
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
        self._restore_persisted_state()

    def _restore_persisted_state(self) -> None:
        """Every deploy restarts this process, which used to silently drop
        auto_trading_enabled back to off (and the day's risk counters back to zero) with
        no signal to anyone - discovered the hard way when a position sat unwatched for
        hours after a routine restart. Stale state from a *previous* trading day is
        harmless to restore here: refresh_status()'s existing day-rollover check resets
        it correctly on the very next call, the same way it already handles a rollover
        that happens mid-session."""
        try:
            saved = bot_state.load()
        except Exception:
            saved = None
        if not saved:
            return
        self.status.auto_trading_enabled = bool(saved["auto_trading_enabled"])
        self._trading_day = date.fromisoformat(saved["trading_day"])
        self.status.trades_today = saved["trades_today"]
        self.status.peak_daily_pnl = saved["peak_daily_pnl"]
        self.status.consecutive_losses = saved["consecutive_losses"]
        self.status.walked_away_for_day = bool(saved["walked_away_for_day"])
        self.status.walk_away_reason = saved["walk_away_reason"]
        self._auto_trading_started_at = (
            datetime.fromisoformat(saved["auto_trading_started_at"]) if saved["auto_trading_started_at"] else None
        )

    def _persist_state(self) -> None:
        try:
            bot_state.save(
                auto_trading_enabled=self.status.auto_trading_enabled,
                trading_day=self._trading_day.isoformat(),
                trades_today=self.status.trades_today,
                peak_daily_pnl=self.status.peak_daily_pnl,
                consecutive_losses=self.status.consecutive_losses,
                walked_away_for_day=self.status.walked_away_for_day,
                walk_away_reason=self.status.walk_away_reason,
                auto_trading_started_at=self._auto_trading_started_at.isoformat() if self._auto_trading_started_at else None,
            )
        except Exception:
            pass  # persistence is a nice-to-have across restarts, never worth crashing a live cycle over

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
            self._persist_state()

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

    def _qty_within_bankroll(self, price: float) -> int:
        """Max shares affordable within what's currently available to trade
        in the bankroll ledger (app.services.bankroll) — a hard cap
        independent of, and in addition to, the risk/capital caps above."""
        available = bankroll.available_to_trade()
        return int(available // price) if available > 0 and price > 0 else 0

    def _validate_against_bankroll(self, trade: TradeRequest) -> None:
        """Applies to manual buy orders too, not just the automated loop -
        the whole point of the bankroll is that nothing opens a new position
        beyond what was actually "withdrawn," regardless of how the order
        was submitted."""
        available = bankroll.available_to_trade()
        if available <= 0:
            raise ValueError("No bankroll available. Withdraw funds on the Bankroll panel before placing a trade.")
        if trade.estimated_price is not None:
            estimated_cost = trade.qty * trade.estimated_price
            if estimated_cost > available:
                raise ValueError(
                    f"This trade would cost ~${estimated_cost:,.2f}, but only ${available:,.2f} is available in your bankroll."
                )

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
            return  # already persisted whenever it was first set - nothing changed here

        # try/finally rather than a persist call before each return below - guarantees
        # whatever this method decided gets saved no matter which branch exits it,
        # instead of depending on every future edit remembering to add one too.
        try:
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
        finally:
            self._persist_state()

    def _manage_open_positions(self, positions: list[dict]) -> list[str]:
        """This is what actually delivers "winners are held past the first target until
        an exit indicator fires" (see the reward_note in SmallAccountPullbackStrategy.evaluate):
        an auto-entered buy only ever rests a stop-loss at the broker, no take-profit, so
        nothing caps a winner automatically. Every cycle, each held position gets re-checked
        against the same exit_indicators() a fresh evaluation would use — level-two selling
        pressure, a topping-tail candle, a red candle — and gets closed the moment one fires.
        Called from manage_open_positions(), independent of auto_cycle()'s new-entry gates -
        managing risk that's already open should never depend on those."""
        exited: list[str] = []
        for position in positions:
            symbol = position.get("symbol")
            qty = position.get("qty")
            if not symbol or not qty:
                continue
            try:
                setup = build_pullback_setup(symbol, self.scanner)
                if not self.strategy.exit_indicators(setup):
                    continue
                self.submit_trade(TradeRequest(symbol=symbol, qty=qty, side=Signal.sell))
                exited.append(symbol)
            except Exception:
                continue  # a single bad symbol/order should never stall managing the rest
        return exited

    def start_auto_trading(self) -> BotStatus:
        self.refresh_status()
        self.status.auto_trading_enabled = True
        self._auto_trading_started_at = datetime.now(UTC)
        self.status.last_message = "Auto-trading enabled — will scan and trade automatically while the market is open"
        self._persist_state()
        return self.status

    def stop_auto_trading(self) -> BotStatus:
        self.refresh_status()
        self.status.auto_trading_enabled = False
        self._auto_trading_started_at = None
        self.status.last_message = "Auto-trading disabled"
        self._persist_state()
        return self.status

    def auto_cycle(self) -> BotStatus:
        """One automation pass: scans the universe and submits bracket trades for genuine
        new buy signals. Reuses submit_trade() so every existing risk guard (daily loss,
        trade cap, position cap, paper/live gating) applies exactly as it does to a
        manually submitted order.

        Deliberately does NOT manage existing positions - see manage_open_positions(),
        which app.main's automation loop calls every cycle regardless of this method's
        own auto_trading_enabled gate. Watching risk that's already open should never
        depend on whether new-entry auto-trading happens to be switched on; a position
        opened manually with no stop-loss attached would otherwise go completely
        unwatched the moment this toggle is off."""
        self.refresh_status()
        if not self.status.auto_trading_enabled:
            return self.status

        self.status.last_automation_run_at = datetime.now(UTC).isoformat()

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

        self._update_session_state()
        if self.status.walked_away_for_day:
            self.status.last_message = f"Auto-trading paused for new entries — {self.status.walk_away_reason}"
            return self.status

        if not self._within_trading_window(datetime.now(UTC)):
            self.status.last_message = (
                f"Auto-trading idle — outside the {settings.trading_window_start}-{settings.trading_window_end} trading window"
            )
            return self.status

        if bankroll.available_to_trade() <= 0:
            self.status.last_message = (
                "Auto-trading idle — no bankroll available. Withdraw funds on the Bankroll panel to start trading."
            )
            return self.status

        # scan_universe() itself only returns the top `limit` ranked results, not every
        # symbol it actually looked at - track the real attempted count separately so the
        # status message doesn't understate how much of the universe got covered.
        scanned_count = len(self.scanner.load_universe()[: max(1, settings.automation_max_symbols)])
        try:
            scan_results = self.scanner.scan_universe(
                limit=settings.automation_scan_limit,
                max_symbols=settings.automation_max_symbols,
            ).results
        except BrokerUnavailable as exc:
            self.status.last_message = str(exc)
            return self.status
        except Exception as exc:
            self.status.last_message = f"Automation scan failed: {exc}"
            return self.status

        qualifying = [c for c in scan_results if c.score >= 4 and c.symbol not in held_symbols]

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
            qty_by_bankroll = self._qty_within_bankroll(setup.proposed_entry)
            size_candidates = [q for q in (qty_by_risk, qty_by_capital) if q > 0]
            qty = min(min(size_candidates) if size_candidates else qty_by_capital, qty_by_bankroll)
            if qty < 1:
                continue

            # No take_profit_price here on purpose - only the stop-loss rests at the
            # broker, so a winner isn't automatically capped at the first target. See
            # _manage_open_positions for what actually closes the position later.
            trade = TradeRequest(
                symbol=candidate.symbol,
                qty=qty,
                side=Signal.buy,
                estimated_price=setup.proposed_entry,
                stop_loss_price=setup.proposed_stop,
            )
            try:
                self.submit_trade(trade)
                traded.append(candidate.symbol)
            except ValueError as exc:
                skipped.append(f"{candidate.symbol} ({exc})")
                continue

        parts = []
        if traded:
            parts.append(f"opened {', '.join(traded)}")
        if skipped:
            parts.append(f"skipped {'; '.join(skipped)}")
        self.status.last_message = (
            "Auto-trading: " + "; ".join(parts)
            if parts
            else f"Auto-trading scanned {scanned_count} symbols — no qualifying buy signals"
        )
        return self.status

    def manage_open_positions(self) -> BotStatus:
        """Watches every currently-held position for a real exit signal and closes it if
        one fires - runs every automation cycle regardless of whether new-entry
        auto-trading is switched on (see app.main's automation loop). This is the only
        thing protecting a position that doesn't have its own broker-side stop-loss, e.g.
        a manual order placed without one - it has no other safety net."""
        self.refresh_status()
        try:
            broker = AlpacaBroker()
            clock = broker.client.get_clock()
        except BrokerUnavailable:
            return self.status
        except Exception:
            return self.status

        if not clock.is_open:
            return self.status

        try:
            held_positions = broker.positions_as_dicts()
        except Exception:
            held_positions = []

        exited = self._manage_open_positions(held_positions)
        if exited:
            self.status.last_message = f"Closed {', '.join(exited)} on an exit signal"
        return self.status

    def submit_trade(self, trade: TradeRequest) -> dict:
        self.refresh_status()
        self.risk.validate(trade, self.status.trades_today, self.status.daily_pnl)
        if trade.side == Signal.buy:
            self._validate_against_bankroll(trade)
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
        self._persist_state()
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
