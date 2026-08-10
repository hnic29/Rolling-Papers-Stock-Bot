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
