from app.services import bot_state, trade_log


def test_load_returns_none_when_nothing_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    assert bot_state.load() is None


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot_state.save(
        auto_trading_enabled=True,
        trading_day="2026-08-24",
        trades_today=2,
        peak_daily_pnl=15.5,
        consecutive_losses=1,
        walked_away_for_day=False,
        walk_away_reason=None,
        auto_trading_started_at="2026-08-24T13:00:00+00:00",
    )

    saved = bot_state.load()

    assert saved["auto_trading_enabled"] == 1
    assert saved["trading_day"] == "2026-08-24"
    assert saved["trades_today"] == 2
    assert saved["peak_daily_pnl"] == 15.5
    assert saved["consecutive_losses"] == 1
    assert saved["walked_away_for_day"] == 0
    assert saved["walk_away_reason"] is None
    assert saved["auto_trading_started_at"] == "2026-08-24T13:00:00+00:00"


def test_save_overwrites_the_single_row_rather_than_appending(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot_state.save(
        auto_trading_enabled=False, trading_day="2026-08-24", trades_today=0,
        peak_daily_pnl=0.0, consecutive_losses=0, walked_away_for_day=False,
        walk_away_reason=None, auto_trading_started_at=None,
    )
    bot_state.save(
        auto_trading_enabled=True, trading_day="2026-08-25", trades_today=5,
        peak_daily_pnl=42.0, consecutive_losses=3, walked_away_for_day=True,
        walk_away_reason="3 losing trades in a row", auto_trading_started_at=None,
    )

    saved = bot_state.load()

    assert saved["trading_day"] == "2026-08-25"
    assert saved["trades_today"] == 5
    assert saved["walked_away_for_day"] == 1
    assert saved["walk_away_reason"] == "3 losing trades in a row"
