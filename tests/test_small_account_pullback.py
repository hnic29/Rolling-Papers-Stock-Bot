from app.models import Candle, PullbackSetup, StockCandidate, Signal
from app.strategies.small_account_pullback import SmallAccountPullbackStrategy


def strong_setup() -> PullbackSetup:
    return PullbackSetup(
        candidate=StockCandidate(
            symbol="YXT",
            price=7.6,
            percent_change=150,
            relative_volume=25,
            total_volume=5_000_000,
            float_shares=3_000_000,
            is_leading_gainer=True,
        ),
        candles=[
            Candle(open=6.8, high=7.8, low=6.7, close=7.6, volume=900_000),
            Candle(open=7.6, high=7.7, low=7.35, close=7.42, volume=280_000),
            Candle(open=7.42, high=7.82, low=7.4, close=7.72, volume=380_000),
        ],
        ema9=7.35,
        macd=0.02,
        vwap=7.3,
        high_of_day=7.8,
        pullback_low=7.35,
        proposed_entry=7.6,
        proposed_stop=7.45,
    )


def test_pullback_strategy_buys_quality_first_pullback():
    decision = SmallAccountPullbackStrategy().evaluate(strong_setup())

    assert decision.signal == Signal.buy
    assert decision.risk_per_share == 0.15
    assert decision.first_target == 7.8


def test_pullback_strategy_rejects_weak_candidate():
    setup = strong_setup()
    setup.candidate.relative_volume = 1
    setup.candidate.total_volume = 100_000
    setup.candidate.float_shares = 100_000_000

    decision = SmallAccountPullbackStrategy().evaluate(setup)

    assert decision.signal == Signal.hold
    assert "candidate has fewer than 4 of 5 stock-selection pillars" in decision.reasons


def test_pullback_strategy_rejects_pullback_that_closed_below_vwap():
    setup = strong_setup()
    setup.vwap = 7.5  # above the pullback candle's 7.42 close

    decision = SmallAccountPullbackStrategy().evaluate(setup)

    assert decision.signal == Signal.hold
    assert "reject: pullback closed below VWAP" in decision.reasons


def _flat_candle(price=7.6, volume=300_000):
    return Candle(open=price, high=price, low=price, close=price, volume=volume)


def test_macd_crossing_below_signal_triggers_an_exit():
    """From Warrior Trading's own trading-plan template: "MACD crosses signal line" is
    listed as an explicit trade-invalidation trigger, alongside decreasing volume and
    a sharp reversal candle - the candle side was already covered, this was missing."""
    setup = strong_setup()
    setup.candles = [_flat_candle() for _ in range(30)]  # past the warm-up threshold
    setup.macd = -0.01
    setup.macd_signal = 0.02

    reasons = SmallAccountPullbackStrategy().exit_indicators(setup)

    assert "exit: MACD crossed below its signal line" in reasons


def test_macd_below_signal_is_ignored_before_warm_up():
    """Too early in the session, MACD and its signal line are both still noisy off
    the same seed average - a "cross" here isn't a real signal yet, same guard
    evaluate() already applies on entry."""
    setup = strong_setup()  # only 3 candles, well under macd_min_candles (26)
    setup.macd = -0.01
    setup.macd_signal = 0.02

    reasons = SmallAccountPullbackStrategy().exit_indicators(setup)

    assert "exit: MACD crossed below its signal line" not in reasons


def test_decreasing_volume_triggers_an_exit():
    strategy = SmallAccountPullbackStrategy()
    setup = strong_setup()
    # 5 candles at steady volume, then a final candle at well under half that pace.
    setup.candles = [_flat_candle(volume=500_000) for _ in range(5)] + [_flat_candle(volume=100_000)]

    reasons = strategy.exit_indicators(setup)

    assert "exit: buying volume is drying up" in reasons


def test_steady_volume_does_not_trigger_an_exit():
    strategy = SmallAccountPullbackStrategy()
    setup = strong_setup()
    setup.candles = [_flat_candle(volume=500_000) for _ in range(5)] + [_flat_candle(volume=480_000)]

    reasons = strategy.exit_indicators(setup)

    assert "exit: buying volume is drying up" not in reasons


def test_decreasing_volume_check_needs_enough_history():
    strategy = SmallAccountPullbackStrategy()
    setup = strong_setup()  # only 3 candles, under the 6-candle lookback requirement
    setup.candles = [_flat_candle(volume=500_000), _flat_candle(volume=500_000), _flat_candle(volume=1)]

    reasons = strategy.exit_indicators(setup)

    assert "exit: buying volume is drying up" not in reasons


def test_pullback_strategy_rejects_heavier_volume_on_the_pullback_than_the_push():
    setup = strong_setup()
    setup.candles[1].volume = 2_000_000  # pullback candle now outweighs the push volume

    decision = SmallAccountPullbackStrategy().evaluate(setup)

    assert decision.signal == Signal.hold
    assert "reject: pullback volume is heavier than the volume on the move up" in decision.reasons


def _with_full_macd_history(setup: PullbackSetup) -> PullbackSetup:
    """Prepends 24 flat, low-volume candles so the setup has >=26 candles (the point at
    which the strategy considers MACD readable) without disturbing the impulse/pullback
    math, which only looks at range/volume bounds already set by the real 3 candles."""
    padding = [Candle(open=6.75, high=6.8, low=6.7, close=6.78, volume=10_000) for _ in range(24)]
    setup.candles = padding + setup.candles
    return setup


def test_pullback_strategy_rejects_negative_macd_once_theres_enough_history():
    setup = _with_full_macd_history(strong_setup())
    setup.macd = -0.05

    decision = SmallAccountPullbackStrategy().evaluate(setup)

    assert decision.signal == Signal.hold
    assert "reject: MACD is negative — trading against the trend" in decision.reasons


def test_pullback_strategy_ignores_macd_early_in_the_session():
    setup = strong_setup()  # only 3 candles — not enough session history to read MACD yet
    setup.macd = -0.05

    decision = SmallAccountPullbackStrategy().evaluate(setup)

    assert decision.signal == Signal.buy
    assert "not enough session history yet to read MACD" in decision.reasons


def test_pullback_strategy_flags_shallow_reward_but_still_buys():
    setup = strong_setup()
    setup.high_of_day = 7.65  # only 0.05 of room vs. 0.15 risk — well under 2:1

    decision = SmallAccountPullbackStrategy().evaluate(setup)

    assert decision.signal == Signal.buy
    assert any("expect to hold past it" in reason for reason in decision.reasons)


def test_market_regime_defaults_to_the_hot_ceiling():
    strategy = SmallAccountPullbackStrategy()
    assert strategy.max_float == 20_000_000


def test_cold_market_tightens_the_float_ceiling():
    """Warrior Trading's own trading-plan template ties float tolerance to market
    regime: "under 20mil in hot market, under 10mil in cold market." """
    strategy = SmallAccountPullbackStrategy()
    strategy.set_market_regime(qualifying_candidate_count=0)  # below cold_market_qualifier_threshold

    assert strategy.max_float == strategy.max_float_cold_market == 10_000_000


def test_hot_market_keeps_the_normal_ceiling():
    strategy = SmallAccountPullbackStrategy()
    strategy.set_market_regime(qualifying_candidate_count=0)  # first go cold...
    strategy.set_market_regime(qualifying_candidate_count=5)  # ...then confirm it relaxes back

    assert strategy.max_float == 20_000_000


def test_regime_change_is_visible_to_score_candidate():
    """The whole point: a stock with float BETWEEN the cold and hot ceilings should
    lose the float pillar on a cold day and keep it on a hot one."""
    strategy = SmallAccountPullbackStrategy()
    candidate = StockCandidate(
        symbol="MIDFLOAT", price=7.0, percent_change=20.0, relative_volume=10.0,
        total_volume=2_000_000, float_shares=15_000_000,  # between 10M and 20M
    )

    strategy.set_market_regime(qualifying_candidate_count=5)  # hot
    hot_score, _ = strategy.score_candidate(candidate)

    strategy.set_market_regime(qualifying_candidate_count=0)  # cold
    cold_score, _ = strategy.score_candidate(candidate)

    assert hot_score == cold_score + 1  # float pillar lost, nothing else changed
