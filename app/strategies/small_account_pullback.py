from app.models import Candle, PullbackSetup, Signal, StrategyDecision, StockCandidate


HOT_SECTORS = {"ai", "biotech", "crypto", "china", "chinese tech", "tech"}


class SmallAccountPullbackStrategy:
    """Rules-based version of the video strategy: quality stock first, first pullback second."""

    min_relative_volume = 5.0
    preferred_relative_volume = 20.0
    min_percent_change = 10.0
    min_total_volume = 1_000_000
    max_float = 20_000_000
    preferred_min_price = 2.0
    preferred_max_price = 20.0
    max_pullback_retrace = 0.5
    large_ask_exit_size = 50_000

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

        if candidate.has_news:
            reasons.append("has a news catalyst")
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
        target = round(setup.high_of_day, 4)
        return StrategyDecision(
            signal=Signal.buy,
            confidence=0.75 if score == 4 else 0.85,
            reasons=reasons + pattern_reasons,
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
        if setup.proposed_stop >= setup.proposed_entry:
            return ["reject: stop must be below entry"]
        if candles[-1].close <= candles[-2].high:
            return ["reject: latest candle has not made a new high"]

        sell_volume = sum(c.volume for c in candles[-3:-1] if c.close < c.open)
        buy_volume = candles[-1].volume if candles[-1].close > candles[-1].open else 0
        volume_note = "green candle volume is stronger than recent red candles" if buy_volume >= sell_volume / 2 else "volume confirmation is modest"
        return ["first pullback is holding above 50% retracement", "entry is above the 9 EMA", "latest candle is making a new high", volume_note]

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
        return reasons

    def _has_topping_tail(self, candle: Candle) -> bool:
        body_top = max(candle.open, candle.close)
        body = abs(candle.close - candle.open)
        upper_wick = candle.high - body_top
        return upper_wick > max(body * 1.5, 0.01)
