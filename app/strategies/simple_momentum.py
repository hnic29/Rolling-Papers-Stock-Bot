from app.models import Signal


class SimpleMomentumStrategy:
    """Placeholder strategy until historical/streaming data is wired in."""

    def next_signal(self) -> Signal:
        return Signal.hold
