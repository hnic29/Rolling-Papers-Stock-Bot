from datetime import UTC, date, datetime, time as dtime
from zoneinfo import ZoneInfo

from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.config import settings
from app.models import BotStatus, Signal, TradeRequest
from app.services import bankroll, bot_state, notify, trade_log
from app.services.live_setup import build_pullback_setup
from app.services.risk import RiskManager
from app.services.scanner import MarketScanner
from app.strategies.small_account_pullback import SmallAccountPullbackStrategy

MARKET_TZ = ZoneInfo("America/New_York")
# Float/price/volume all drift; nothing re-runs scripts/build_universe.py on a schedule,
# so this is what actually surfaces staleness instead of it going unnoticed indefinitely.
STALE_UNIVERSE_DAYS = 45


def current_trading_day() -> date:
    return datetime.now(MARKET_TZ).date()


def _todays_session_open_utc() -> datetime:
    """9:30 ET on the current trading day, in UTC - the earliest moment 'idle time'
    can meaningfully start accumulating."""
    open_local = datetime.combine(current_trading_day(), dtime(9, 30), tzinfo=MARKET_TZ)
    return open_local.astimezone(UTC)


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
        # In-memory position-management state; lost on restart, which is safe - peaks
        # rebuild from the next cycle's price, and re-protecting an already-protected
        # position just re-places the same breakeven stop.
        self._position_peaks: dict[str, float] = {}
        self._protected_positions: set[str] = set()
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
        self.status.running = bool(saved["running"])
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
                running=self.status.running,
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
        # Settings can flip paper/live at runtime (the Settings page save takes effect
        # on the next request - no restart) and this was only set at construction, so
        # the dashboard's Mode field kept saying "Paper" after switching to live: the
        # one field that must never lie about which world the money is in.
        self.status.paper = settings.alpaca_paper

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
            # Scoped to this bot's own trades (today's realized P&L from the trade log),
            # not AlpacaBroker().daily_pnl() (the WHOLE account's equity change) - the
            # daily-loss circuit breaker (RiskManager, checked against a percentage of
            # the bankroll) needs to compare against P&L from the same bankroll-scoped
            # world, not something that can swing on unrelated account activity. Also
            # means this keeps working even when Alpaca itself is unreachable.
            realized = trade_log.todays_realized_trades(self._trading_day)
            self.status.daily_pnl = round(sum((trade["realized_pnl"] or 0.0) for trade in realized), 2)
        except Exception:
            pass  # keep the last known value if the trade log isn't reachable for some reason

        return self.status

    def start(self) -> BotStatus:
        self.refresh_status()
        self.status.running = True
        self.status.last_message = "Bot started"
        self._persist_state()
        return self.status

    def stop(self) -> BotStatus:
        self.refresh_status()
        self.status.running = False
        self.status.last_message = "Bot stopped"
        self._persist_state()
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
                # Idle time only counts WITHIN today's session. The baseline (auto-trading
                # started, or the day's state rollover at midnight) can sit hours before
                # the open - unclamped, the very first 9:30 cycle read "425 minutes idle"
                # and walked away at the bell without one single scan. Discovered live on
                # 2026-08-25: the bot's first whole-market morning, silenced at 9:30:00.
                baseline = max(baseline, _todays_session_open_utc())
                idle_minutes = (datetime.now(UTC) - baseline).total_seconds() / 60
                if idle_minutes > settings.max_minutes_without_trade:
                    self.status.walked_away_for_day = True
                    self.status.walk_away_reason = f"no trade taken in over {settings.max_minutes_without_trade} minutes — momentum has cooled"
        finally:
            self._persist_state()
            # Only reachable on the cycle that TRIPPED a walk-away (already-tripped days
            # take the early return above), so this fires exactly once per trip.
            if self.status.walked_away_for_day:
                notify.send(
                    "Walked away for the day",
                    f"Auto-trading paused for new entries — {self.status.walk_away_reason}. "
                    "Open positions stay protected; entries resume next trading day.",
                    priority="high",
                    tags="octagonal_sign",
                )

    def _manage_open_positions(self, positions: list[dict]) -> list[str]:
        """Every cycle, every held position gets three layers of management, in order:

        1. Exit indicators - the same deterioration checks a fresh evaluation would use
           (topping tail, red candle). Fires -> close at market.
        2. Profit giveback - once a position has been up 2R (Ross's minimum-target
           multiple), falling back below +1R closes it. This is what banks a winner
           instead of riding it all the way back down: EXOD went +$28 unrealized to
           +$8 realized waiting for a reversal candle alone.
        3. Breakeven protect - the first time 2R prints, the original entry stop gets
           replaced with a resting stop at the entry price ("move the stop up toward
           break-even" - his documented rule). A proven winner can no longer turn into
           a loss.

        2 and 3 need a defined R, so they only apply to positions whose entry recorded
        a stop; a stop-less manual buy still gets layer 1. Called from
        manage_open_positions(), independent of auto_cycle()'s new-entry gates."""
        exited: list[str] = []
        for position in positions:
            symbol = position.get("symbol")
            qty = position.get("qty")
            if not symbol or not qty:
                continue
            try:
                setup = build_pullback_setup(symbol, self.scanner)
                if self.strategy.exit_indicators(setup):
                    self._close_position(symbol, qty, "exit_signal")
                    exited.append(symbol)
                    continue

                entry_rows = trade_log.open_filled_buys(symbol)
                entry_row = entry_rows[-1] if entry_rows else None
                current_price = float(position.get("current_price") or 0)
                if not entry_row or not entry_row.get("stop_loss_price") or not entry_row.get("filled_avg_price") or not current_price:
                    continue

                entry = float(entry_row["filled_avg_price"])
                risk = entry - float(entry_row["stop_loss_price"])
                if risk <= 0:
                    continue

                peak = max(self._position_peaks.get(symbol, entry), current_price)
                self._position_peaks[symbol] = peak

                if peak >= entry + 2 * risk:
                    if current_price <= entry + risk:
                        self._close_position(symbol, qty, "giveback")
                        exited.append(symbol)
                        continue
                    if symbol not in self._protected_positions:
                        # Replace the entry stop with a breakeven stop, and link it so
                        # trade_sync realizes the exit if THAT stop is what ends up filling.
                        self._cancel_open_orders(symbol)
                        broker = AlpacaBroker()
                        stop_order = broker.submit_stop_order(symbol, qty, entry)
                        trade_log.record_pending_exit(entry_row["order_id"], str(stop_order.id), "stop")
                        self._protected_positions.add(symbol)
                        notify.send(
                            f"{symbol} hit 2R — protected",
                            f"{symbol} reached twice its risk; stop moved to breakeven (${entry:,.2f}). Worst case is now a scratch.",
                            tags="shield",
                        )
            except Exception:
                continue  # a single bad symbol/order should never stall managing the rest

        for symbol in exited:
            self._position_peaks.pop(symbol, None)
            self._protected_positions.discard(symbol)
        return exited

    def _close_position(self, symbol: str, qty: float, exit_reason: str) -> None:
        """Cancel anything resting (the original or breakeven stop holds the shares -
        a second sell would be rejected), then market-close with exit linkage."""
        self._cancel_open_orders(symbol)
        self.submit_trade(TradeRequest(symbol=symbol, qty=qty, side=Signal.sell), exit_reason=exit_reason)

    def _cancel_open_orders(self, symbol: str) -> None:
        broker = AlpacaBroker()
        for order in broker.open_orders(symbol):
            try:
                broker.cancel_order(str(order.id))
            except Exception:
                continue  # already filled/canceled in the race window - nothing to do

    def start_auto_trading(self) -> BotStatus:
        self.refresh_status()
        self.status.auto_trading_enabled = True
        self._auto_trading_started_at = datetime.now(UTC)
        self.status.last_message = "Auto-trading enabled — will scan and trade automatically while the market is open"
        self._persist_state()
        return self.status

    def resume_day(self) -> BotStatus:
        """Explicit manual override that clears a walk-away for the rest of today. The
        stickiness is deliberate (coming back after walking away is the FOMO trap the
        rules exist to prevent), so this never happens automatically - but the human
        stays in charge of their own discipline, and it's also the recovery path when
        a trip was wrong (the unclamped idle-baseline bug walked away AT the open)."""
        self.refresh_status()
        self.status.walked_away_for_day = False
        self.status.walk_away_reason = None
        self._auto_trading_started_at = datetime.now(UTC)  # restart the idle clock too
        self.status.last_message = "Walk-away cleared — auto-trading may take new entries again today"
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

        try:
            scan_response = self.scanner.scan_universe(
                limit=settings.automation_scan_limit,
                max_symbols=settings.automation_max_symbols,
            )
            scan_results = scan_response.results
            # The response's own count, since results are truncated to the top N and the
            # scan now covers live top-gainers on top of the static universe.
            scanned_count = scan_response.scanned_count
        except BrokerUnavailable as exc:
            self.status.last_message = str(exc)
            return self.status
        except Exception as exc:
            self.status.last_message = f"Automation scan failed: {exc}"
            return self.status

        # The Ross lane first: LIVE gap candidates from real-time snapshots, evaluated
        # with their live numbers injected (no 16-minute lag, works from the opening
        # bell). The lagged-but-consolidated scan below stays as the second net.
        gap_lane: list = []
        try:
            for live in self.scanner.realtime_gap_candidates():
                score, _reasons = self.strategy.score_candidate(live)
                if score >= 4 and live.symbol not in held_symbols:
                    gap_lane.append(live)
        except Exception:
            pass  # gap lane failing narrows this cycle to the lagged scan, never breaks it

        gap_symbols = {c.symbol for c in gap_lane}
        qualifying = [c for c in scan_results if c.score >= 4 and c.symbol not in held_symbols and c.symbol not in gap_symbols]

        # (symbol, injected live candidate or None) - gap lane evaluated first.
        entry_queue = [(c.symbol, c) for c in gap_lane] + [(c.symbol, None) for c in qualifying]

        traded: list[str] = []
        skipped: list[str] = []
        for symbol, live_candidate in entry_queue:
            if self.status.trades_today >= settings.max_trades_per_day:
                break
            try:
                setup = build_pullback_setup(symbol, self.scanner, candidate=live_candidate)
                decision = self.strategy.evaluate(setup)
            except Exception:
                continue
            if decision.signal != Signal.buy:
                continue

            # Percentages of the current bankroll, not fixed dollars - see app.config's
            # comment on risk_per_trade_pct for why.
            current_bankroll = bankroll.current_bankroll()
            risk_dollars = current_bankroll * settings.risk_per_trade_pct / 100
            position_value_dollars = current_bankroll * settings.max_position_value_pct / 100

            risk_per_share = setup.proposed_entry - setup.proposed_stop
            qty_by_risk = int(risk_dollars // risk_per_share) if risk_per_share > 0 else 0
            qty_by_capital = int(position_value_dollars // setup.proposed_entry)
            qty_by_bankroll = self._qty_within_bankroll(setup.proposed_entry)
            size_candidates = [q for q in (qty_by_risk, qty_by_capital) if q > 0]
            qty = min(min(size_candidates) if size_candidates else qty_by_capital, qty_by_bankroll)
            if qty < 1:
                continue

            # No take_profit_price here on purpose - only the stop-loss rests at the
            # broker, so a winner isn't automatically capped at the first target. See
            # _manage_open_positions for what actually closes the position later.
            trade = TradeRequest(
                symbol=symbol,
                qty=qty,
                side=Signal.buy,
                estimated_price=setup.proposed_entry,
                stop_loss_price=setup.proposed_stop,
            )
            try:
                self.submit_trade(trade)
                traded.append(symbol)
            except ValueError as exc:
                skipped.append(f"{symbol} ({exc})")
                continue

        parts = []
        if traded:
            parts.append(f"opened {', '.join(traded)}")
        if skipped:
            parts.append(f"skipped {'; '.join(skipped)}")
        self.status.last_message = (
            "Auto-trading: " + "; ".join(parts)
            if parts
            else (
                f"Auto-trading swept {scan_response.swept_count:,} symbols market-wide, "
                f"evaluated {scanned_count} candidates — no qualifying buy signals"
                if scan_response.swept_count
                else f"Auto-trading scanned {scanned_count} symbols — no qualifying buy signals"
            )
        )

        # Nothing re-runs build_universe.py on its own - without this, stale float/price/
        # volume data would just silently keep getting used forever with no one told.
        universe_age = self.scanner.universe_age_days()
        if universe_age is not None and universe_age >= STALE_UNIVERSE_DAYS:
            self.status.last_message += (
                f" (universe data is {universe_age} days old — consider re-running scripts/build_universe.py)"
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

    def submit_trade(self, trade: TradeRequest, exit_reason: str = "manual_close") -> dict:
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
            entry_price_estimate=trade.estimated_price if trade.side == Signal.buy else None,
        )

        # EVERY sell that closes a long links back to the open buy row(s) it closes -
        # not just automated exit-signal sells. A sell isn't a bracket leg of the buy
        # order, so without this link the buy would look open forever: deployed_capital
        # keeps charging the bankroll for a closed position, and the realized P&L never
        # reaches the walk-away rules. This used to live only in the exit-signal path,
        # which left manual closes (dashboard sell / API) with exactly that bug.
        # trade_sync completes the exit (price, P&L) once the sell actually fills.
        if trade.side == Signal.sell:
            for buy in trade_log.open_filled_buys(trade.symbol):
                trade_log.record_pending_exit(buy["order_id"], str(order.id), exit_reason)

        # Buys only - sells get their notification when the exit CONFIRMS with real
        # P&L (trade_sync), which is the number that actually matters. Notifying the
        # sell submission too would just double-ping every exit.
        if trade.side == Signal.buy:
            entry = f" @ ~${trade.estimated_price:,.2f}" if trade.estimated_price is not None else ""
            stop = f", stop ${trade.stop_loss_price:,.2f}" if trade.stop_loss_price is not None else ", no stop attached"
            notify.send(
                f"Opened {trade.symbol.upper()}",
                f"Bought {trade.qty:g} {trade.symbol.upper()}{entry}{stop}",
                tags="chart_with_upwards_trend",
            )

        return {"id": str(order.id), "status": str(order.status), "symbol": order.symbol}


bot = TradingBot()
