import pytest
import requests

from app.config import settings
from app.services import fmp as fmp_module
from app.services.fmp import FmpClient, FmpRequestError


@pytest.fixture(autouse=True)
def _clear_float_cache():
    fmp_module._float_cache.clear()
    yield
    fmp_module._float_cache.clear()


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload

    def json(self):
        return self._payload


def _with_fmp_key(monkeypatch, key="test-fmp-key"):
    monkeypatch.setattr(settings, "fmp_api_key", key)
    return FmpClient()


def test_shares_float_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "fmp_api_key", "")

    assert FmpClient().shares_float("AAPL") is None


def test_shares_float_returns_dict_payload(monkeypatch):
    client = _with_fmp_key(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse(200, {"symbol": "AAPL", "floatShares": 1000}))

    result = client.shares_float("AAPL")

    assert result == {"symbol": "AAPL", "floatShares": 1000}


def test_shares_float_returns_first_item_of_list_payload(monkeypatch):
    client = _with_fmp_key(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse(200, [{"symbol": "AAPL", "floatShares": 1000}]))

    result = client.shares_float("AAPL")

    assert result == {"symbol": "AAPL", "floatShares": 1000}


def test_shares_float_raises_a_clean_error_on_rate_limit_without_leaking_the_key(monkeypatch):
    client = _with_fmp_key(monkeypatch, key="super-secret-key")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse(429))

    try:
        client.shares_float("AAPL")
        assert False, "expected FmpRequestError"
    except FmpRequestError as exc:
        assert "super-secret-key" not in str(exc)
        assert "apikey" not in str(exc)
        assert "rate limit" in str(exc).lower()


def test_shares_float_raises_a_clean_error_on_bad_key_without_leaking_it(monkeypatch):
    client = _with_fmp_key(monkeypatch, key="super-secret-key")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse(401))

    try:
        client.shares_float("AAPL")
        assert False, "expected FmpRequestError"
    except FmpRequestError as exc:
        assert "super-secret-key" not in str(exc)


def test_shares_float_wraps_network_errors(monkeypatch):
    client = _with_fmp_key(monkeypatch)

    def raise_connection_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(requests, "get", raise_connection_error)

    try:
        client.shares_float("AAPL")
        assert False, "expected FmpRequestError"
    except FmpRequestError as exc:
        assert "could not reach FMP" in str(exc)


def test_shares_float_caches_successful_lookups(monkeypatch):
    client = _with_fmp_key(monkeypatch)
    call_count = 0

    def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(200, {"symbol": "AAPL", "floatShares": 1000})

    monkeypatch.setattr(requests, "get", fake_get)

    first = client.shares_float("AAPL")
    second = client.shares_float("AAPL")

    assert first == second == {"symbol": "AAPL", "floatShares": 1000}
    assert call_count == 1


def test_shares_float_cache_is_shared_across_client_instances(monkeypatch):
    monkeypatch.setattr(settings, "fmp_api_key", "test-fmp-key")
    call_count = 0

    def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(200, {"symbol": "AAPL", "floatShares": 1000})

    monkeypatch.setattr(requests, "get", fake_get)

    FmpClient().shares_float("AAPL")
    FmpClient().shares_float("AAPL")

    assert call_count == 1


def test_shares_float_use_cache_false_always_makes_a_fresh_request(monkeypatch):
    client = _with_fmp_key(monkeypatch)
    call_count = 0

    def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(200, {"symbol": "AAPL", "floatShares": 1000})

    monkeypatch.setattr(requests, "get", fake_get)

    client.shares_float("AAPL")
    client.shares_float("AAPL", use_cache=False)

    assert call_count == 2
