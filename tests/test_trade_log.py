from app.services import trade_log


def test_record_and_list_trade(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    trade_log.record_trade(order_id="abc123", symbol="AAPL", side="buy", qty=2, status="accepted")
    trades = trade_log.list_trades()

    assert len(trades) == 1
    assert trades[0]["order_id"] == "abc123"
    assert trades[0]["symbol"] == "AAPL"
    assert trades[0]["status"] == "accepted"
    assert trades[0]["filled_avg_price"] is None


def test_pending_order_ids_excludes_terminal_statuses(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    trade_log.record_trade(order_id="pending1", symbol="AAPL", side="buy", qty=1, status="accepted")
    trade_log.record_trade(order_id="done1", symbol="TSLA", side="sell", qty=1, status="filled")

    assert trade_log.pending_order_ids() == ["pending1"]


def test_update_fill_marks_order_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    trade_log.record_trade(order_id="xyz", symbol="AAPL", side="buy", qty=1, status="accepted")
    trade_log.update_fill(order_id="xyz", status="filled", filled_avg_price=123.45, filled_qty=1, filled_at="2026-08-10T00:00:00Z")

    trades = trade_log.list_trades()
    assert trades[0]["status"] == "filled"
    assert trades[0]["filled_avg_price"] == 123.45
    assert trade_log.pending_order_ids() == []
