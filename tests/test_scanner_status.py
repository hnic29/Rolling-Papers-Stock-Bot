from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import scanner_status
from app.services.scanner import MarketScanner


def test_snapshot_defaults_to_idle_for_a_user_who_has_never_scanned():
    snap = scanner_status.snapshot(user_id=999)

    assert snap["scanning"] is False
    assert snap["phase"] == "idle"
    assert snap["results"] == []
    assert snap["scanned_count"] == 0


def test_start_update_finish_transitions_through_the_expected_shape():
    scanner_status.start(1, phase="sweeping")
    mid = scanner_status.snapshot(1)
    assert mid["scanning"] is True
    assert mid["phase"] == "sweeping"

    scanner_status.update(1, phase="scoring", detail="AAPL", done=3, total=10, found=1)
    mid2 = scanner_status.snapshot(1)
    assert mid2["scanning"] is True  # update() alone doesn't end the run
    assert mid2["phase"] == "scoring"
    assert mid2["detail"] == "AAPL"
    assert mid2["progress_done"] == 3
    assert mid2["progress_total"] == 10
    assert mid2["found"] == 1

    scanner_status.finish(1, results=["fake-result"], scanned_count=10, swept_count=500)
    done = scanner_status.snapshot(1)
    assert done["scanning"] is False
    assert done["phase"] == "idle"
    assert done["results"] == ["fake-result"]
    assert done["scanned_count"] == 10
    assert done["swept_count"] == 500


def test_fail_ends_the_run_and_records_the_reason():
    scanner_status.start(2, phase="scoring")

    scanner_status.fail(2, "Alpaca credentials are not configured")

    snap = scanner_status.snapshot(2)
    assert snap["scanning"] is False
    assert snap["detail"] == "Alpaca credentials are not configured"


def test_different_users_scanner_status_is_fully_isolated():
    scanner_status.start(10, phase="sweeping")
    scanner_status.finish(10, results=["alice-result"], scanned_count=5)

    # user 11 never scanned - still idle even though user 10 just finished
    snap_11 = scanner_status.snapshot(11)
    assert snap_11["scanning"] is False
    assert snap_11["results"] == []

    snap_10 = scanner_status.snapshot(10)
    assert snap_10["results"] == ["alice-result"]


def _bar(close, volume):
    return SimpleNamespace(open=close, high=close, low=close, close=close, volume=volume)


def _mock_broker(bars_by_symbol):
    broker = MagicMock()
    broker.daily_bars.return_value = SimpleNamespace(data=bars_by_symbol)
    broker.latest_quote.side_effect = Exception("no quote in test")
    broker.latest_news.side_effect = Exception("no news in test")
    return broker


def test_a_real_scan_call_leaves_scanner_status_finished_with_its_own_results(monkeypatch):
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 1.0)
    bars = {"ACHR": [_bar(10.0, 1_000_000) for _ in range(20)] + [_bar(10.5, 500_000)]}
    scanner = MarketScanner(user_id=42)

    with patch.object(scanner, "_broker", return_value=_mock_broker(bars)):
        response = scanner.scan(["ACHR"])

    snap = scanner_status.snapshot(42)
    assert snap["scanning"] is False
    assert snap["scanned_count"] == 1
    assert [r.symbol for r in snap["results"]] == [r.symbol for r in response.results]


def test_a_failed_scan_call_marks_scanner_status_as_failed_not_stuck_scanning():
    scanner = MarketScanner(user_id=43)
    broker = MagicMock()
    broker.daily_bars.side_effect = RuntimeError("simulated Alpaca outage")

    with patch.object(scanner, "_broker", return_value=broker):
        try:
            scanner.scan(["ACHR"])
            assert False, "expected the broker failure to propagate"
        except RuntimeError:
            pass

    snap = scanner_status.snapshot(43)
    assert snap["scanning"] is False
    assert "simulated Alpaca outage" in snap["detail"]
