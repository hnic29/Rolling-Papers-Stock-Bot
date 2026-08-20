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
