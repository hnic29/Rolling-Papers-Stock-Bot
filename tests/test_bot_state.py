import sqlite3

from app.services import bot_state, trade_log


def test_load_returns_none_when_nothing_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    assert bot_state.load() is None


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot_state.save(
        auto_trading_enabled=True,
        running=False,
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
        auto_trading_enabled=False, running=False, trading_day="2026-08-24", trades_today=0,
        peak_daily_pnl=0.0, consecutive_losses=0, walked_away_for_day=False,
        walk_away_reason=None, auto_trading_started_at=None,
    )
    bot_state.save(
        auto_trading_enabled=True, running=True, trading_day="2026-08-25", trades_today=5,
        peak_daily_pnl=42.0, consecutive_losses=3, walked_away_for_day=True,
        walk_away_reason="3 losing trades in a row", auto_trading_started_at=None,
    )

    saved = bot_state.load()

    assert saved["trading_day"] == "2026-08-25"
    assert saved["trades_today"] == 5
    assert saved["walked_away_for_day"] == 1
    assert saved["walk_away_reason"] == "3 losing trades in a row"


def test_each_user_gets_their_own_independent_state(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot_state.save(
        auto_trading_enabled=True, running=True, trading_day="2026-08-24", trades_today=3,
        peak_daily_pnl=10.0, consecutive_losses=0, walked_away_for_day=False,
        walk_away_reason=None, auto_trading_started_at=None, user_id=1,
    )
    bot_state.save(
        auto_trading_enabled=False, running=False, trading_day="2026-08-24", trades_today=0,
        peak_daily_pnl=0.0, consecutive_losses=3, walked_away_for_day=True,
        walk_away_reason="3 losing trades in a row", auto_trading_started_at=None, user_id=2,
    )

    assert bot_state.load(user_id=1)["trades_today"] == 3
    assert bot_state.load(user_id=2)["walked_away_for_day"] == 1
    assert bot_state.load(user_id=2)["trades_today"] == 0  # untouched by user 1's save


def test_migrates_a_pre_multi_user_single_row_table_to_user_id_1(tmp_path, monkeypatch):
    """Regression guard for the schema upgrade: a database created before multi-user
    support had bot_state hard-constrained to exactly one row (`id INTEGER PRIMARY
    KEY CHECK (id = 1)`) - that row's data must survive the migration to `user_id`,
    landing under the same user id Stage 1's login migration uses."""
    db_path = tmp_path / "trade_log.db"
    monkeypatch.setattr(trade_log, "DB_PATH", db_path)

    # Build the OLD schema directly, bypassing bot_state's own (already-migrated)
    # _connect(), to simulate a real pre-existing production database.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE bot_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            auto_trading_enabled INTEGER NOT NULL DEFAULT 0,
            running INTEGER NOT NULL DEFAULT 0,
            trading_day TEXT NOT NULL,
            trades_today INTEGER NOT NULL DEFAULT 0,
            peak_daily_pnl REAL NOT NULL DEFAULT 0,
            consecutive_losses INTEGER NOT NULL DEFAULT 0,
            walked_away_for_day INTEGER NOT NULL DEFAULT 0,
            walk_away_reason TEXT,
            auto_trading_started_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO bot_state VALUES (1, 1, 1, '2026-08-24', 7, 42.5, 1, 0, NULL, '2026-08-24T13:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    saved = bot_state.load(user_id=1)

    assert saved["trades_today"] == 7
    assert saved["peak_daily_pnl"] == 42.5
    assert saved["auto_trading_started_at"] == "2026-08-24T13:00:00+00:00"
