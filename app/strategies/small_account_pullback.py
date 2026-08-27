from app.config import settings
from app.models import Candle, PullbackSetup, Signal, StrategyDecision, StockCandidate
from app.services.catalyst import SENTIMENT_NEGATIVE, describe_catalyst


HOT_SECTORS = {"ai", "biotech", "china", "chinese tech", "tech"}
HOT_MARKET_MAX_FLOAT = 20_000_000


class SmallAccountPullbackStrategy:
    """Rules-based version of the video strategy: quality stock first, first pullback second."""

    min_relative_volume = 5.0
    preferred_relative_volume = 20.0
    min_percent_change = 10.0
    min_total_volume = 1_000_000
    max_float = HOT_MARKET_MAX_FLOAT  # the default baseline everything else assumes
    # Warrior Trading's own trading-plan template ties float tolerance to market
    # regime: "under 20mil in hot market, under 10mil in cold market." Genuine setups
    # are scarcer on a cold day, so the float bar tightens rather than staying fixed.
    max_float_cold_market = 10_000_000
    # Fewer than this many candidates clearing 4-of-5 market-wide in a cycle counts as
    # "cold." Deliberately reuses data the sweep/gap lane already compute every cycle -
    # no extra API calls, no separate volatility/breadth indicator to get wrong.
    cold_market_qualifier_threshold = 2
    preferred_min_price = 2.0
    preferred_max_price = 20.0
    max_pullback_retrace = 0.5
    large_ask_exit_size = 50_000
    # MACD's slow EMA isn't meaningfully "warmed up" until the session has at least this
    # many 1-minute candles — before that, the fast/slow EMAs are computed from an
    # identical seed average and MACD reads exactly 0, which would reject every early-
    # session trade. Skip the filter rather than treat an undefined reading as "negative."
    macd_min_candles = 26
    # From Warrior Trading's own trading-plan template: "MACD crosses signal line,
    # decreasing volume" listed alongside a sharp reversal candle as trade-invalidation
    # triggers. The candle-pattern side was already covered (topping tail, red close);
    # these two were genuinely missing.
    volume_decay_lookback = 5
    volume_decay_ratio = 0.5

    def set_market_regime(self, qualifying_candidate_count: int) -> None:
        """Tightens the float ceiling on a cold day, relaxes it back on a hot one.
        Meant to be called once per automation cycle with THIS cycle's qualifying
        count (score>=4 candidates found market-wide) - takes effect starting the
        following cycle, a one-cycle (~60s) lag that's immaterial for a signal that
        shouldn't swing minute to minute anyway."""
        self.max_float = self.max_float_cold_market if qualifying_candidate_count < self.cold_market_qualifier_threshold else HOT_MARKET_MAX_FLOAT

    def score_candidate(self, candidate: StockCandidate) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []

        if candidate.relative_volume >= self.min_relative_volume:
            score += 1
            reasons.append("relative volume is at least 5x average")
        if candidate.total_volume >= self.min_total_volume:
            score += 1
            reasons.append("total volume is above 1M shares")
        if candidate.percent_change >= self.min_percent_change:
            score += 1
            reasons.append("stock is up at least 10%")
        if self.preferred_min_price <= candidate.price <= self.preferred_max_price:
            score += 1
            reasons.append("price is in the preferred $2-$20 range")
        if candidate.float_shares is not None and candidate.float_shares <= self.max_float:
            score += 1
            reasons.append("float is below 20M shares")

        catalyst_reason = describe_catalyst(candidate.news_category, candidate.news_sentiment)
        if catalyst_reason:
            reasons.append(catalyst_reason)
        elif candidate.is_leading_gainer:
            reasons.append("no news, but it is a leading gainer")
        elif candidate.sector and candidate.sector.lower() in HOT_SECTORS:
            reasons.append("no news, but sector is currently momentum-friendly")

        return score, reasons

    def evaluate(self, setup: PullbackSetup) -> StrategyDecision:
        score, reasons = self.score_candidate(setup.candidate)
        if score < 4:
            return StrategyDecision(signal=Signal.hold, confidence=0.0, reasons=reasons + ["candidate has fewer than 4 of 5 stock-selection pillars"])

        pattern_reasons = self._validate_pullback(setup)
        if any(reason.startswith("reject:") for reason in pattern_reasons):
            return StrategyDecision(signal=Signal.hold, confidence=0.2, reasons=reasons + pattern_reasons)

        exit_reasons = self.exit_indicators(setup)
        if exit_reasons:
            return StrategyDecision(signal=Signal.sell, confidence=0.8, reasons=reasons + pattern_reasons + exit_reasons)

        risk = round(setup.proposed_entry - setup.proposed_stop, 4)
        if risk <= 0:
            return StrategyDecision(signal=Signal.hold, confidence=0.2, reasons=reasons + pattern_reasons + ["reject: stop leaves no defined risk"])
        target = round(setup.high_of_day, 4)

        # High-of-day is only the *next visible level*, not a cap — winners are held past it
        # until an exit indicator fires (see exit_indicators). A shallow reward here isn't a
        # reason to skip the trade, just a note; the 2:1 target is enforced statistically via
        # risk-based position sizing (settings.risk_per_trade_pct), not a per-trade hard gate.
        reward_note = (
            f"reward to next high is only {round((target - setup.proposed_entry) / risk, 1)}:1 — expect to hold past it"
            if (target - setup.proposed_entry) < settings.min_reward_risk_ratio * risk
            else f"reward to next high already clears the {settings.min_reward_risk_ratio:.0f}:1 target"
        )

        return StrategyDecision(
            signal=Signal.buy,
            confidence=0.75 if score == 4 else 0.85,
            reasons=reasons + pattern_reasons + [reward_note],
            risk_per_share=risk,
            first_target=target,
        )

    def next_signal(self) -> Signal:
        return Signal.hold

    def _validate_pullback(self, setup: PullbackSetup) -> list[str]:
        candles = setup.candles
        if len(candles) < 3:
            return ["reject: need at least three 1-minute candles for a pullback setup"]

        impulse_low = min(c.low for c in candles[:-2])
        impulse_high = max(c.high for c in candles[:-2])
        impulse_range = impulse_high - impulse_low
        if impulse_range <= 0:
            return ["reject: impulse range is not valid"]

        retrace = (impulse_high - setup.pullback_low) / impulse_range
        if retrace > self.max_pullback_retrace:
            return ["reject: pullback retraced more than 50% of the prior move"]
        if setup.proposed_entry <= setup.ema9:
            return ["reject: proposed entry is below the 9 EMA"]
        if candles[-2].close < setup.vwap:
            return ["reject: pullback closed below VWAP"]
        if len(candles) >= self.macd_min_candles and setup.macd <= 0:
            return ["reject: MACD is negative — trading against the trend"]
        if setup.proposed_stop >= setup.proposed_entry:
            return ["reject: stop must be below entry"]
        if candles[-1].close <= candles[-2].high:
            return ["reject: latest candle has not made a new high"]

        impulse_volume = sum(c.volume for c in candles[:-2] if c.close > c.open) or sum(c.volume for c in candles[:-2])
        pullback_volume = sum(c.volume for c in candles[-2:-1])
        if impulse_volume and pullback_volume > impulse_volume:
            return ["reject: pullback volume is heavier than the volume on the move up"]

        macd_note = "MACD is positive" if len(candles) >= self.macd_min_candles else "not enough session history yet to read MACD"
        return [
            "first pullback is holding above 50% retracement",
            "entry is above the 9 EMA",
            "pullback held above VWAP",
            macd_note,
            "latest candle is making a new high",
            "volume on the pullback is lighter than the volume on the push",
        ]

    def exit_indicators(self, setup: PullbackSetup) -> list[str]:
        reasons: list[str] = []
        level_two = setup.level_two
        last = setup.candles[-1] if setup.candles else None

        if level_two:
            if level_two.largest_ask_size >= self.large_ask_exit_size:
                reasons.append("exit: large seller visible on level two")
            if level_two.hidden_seller_detected:
                reasons.append("exit: hidden seller detected")
            if level_two.red_tape_burst:
                reasons.append("exit: large burst of red tape")
            if level_two.buying_slowing:
                reasons.append("exit: buying is slowing")

        if last and self._has_topping_tail(last):
            reasons.append("exit: topping-tail candle formed")
        if last and last.close < last.open:
            reasons.append("exit: red candle formed")
        # Same warm-up guard evaluate() uses on entry - too early in the session, MACD
        # and its signal line are both still noisy off the same seed average, so a
        # "cross" here isn't a real signal yet.
        if len(setup.candles) >= self.macd_min_candles and setup.macd < setup.macd_signal:
            reasons.append("exit: MACD crossed below its signal line")
        if self._volume_is_decreasing(setup.candles):
            reasons.append("exit: buying volume is drying up")
        return reasons

    def _volume_is_decreasing(self, candles: list[Candle]) -> bool:
        """The latest candle's volume has fallen well below the recent pace - the
        interest that drove the move is drying up. Needs a few candles of "recent
        pace" to compare against; too little history and this simply doesn't fire
        rather than guessing."""
        if len(candles) < self.volume_decay_lookback + 1:
            return False
        recent = candles[-(self.volume_decay_lookback + 1) : -1]
        avg_recent_volume = sum(c.volume for c in recent) / len(recent)
        if avg_recent_volume <= 0:
            return False
        return candles[-1].volume < avg_recent_volume * self.volume_decay_ratio

    def _has_topping_tail(self, candle: Candle) -> bool:
        body_top = max(candle.open, candle.close)
        body = abs(candle.close - candle.open)
        upper_wick = candle.high - body_top
        return upper_wick > max(body * 1.5, 0.01)
