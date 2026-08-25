from unittest.mock import MagicMock

import pytest

from app.services import float_lookup


@pytest.fixture(autouse=True)
def _clear_yahoo_cache():
    float_lookup._yf_cache.clear()
    yield
    float_lookup._yf_cache.clear()


def _patch_fmp(monkeypatch, payload=None, error=None):
    client = MagicMock()
    if error:
        client.shares_float.side_effect = error
    else:
        client.shares_float.return_value = payload
    monkeypatch.setattr(float_lookup, "FmpClient", lambda: client)
    return client


def _patch_yahoo(monkeypatch, float_value=None, error=None):
    """Plain stand-in class, NOT a MagicMock with a class-level property - mutating
    type(MagicMock()) patches the shared MagicMock class itself and can poison every
    other test in the session. Returns the list of symbols Yahoo was asked about."""
    import yfinance

    calls = []

    class _Ticker:
        def __init__(self, symbol):
            calls.append(symbol)

        @property
        def info(self):
            if error:
                raise error
            return {"floatShares": float_value}

    monkeypatch.setattr(yfinance, "Ticker", _Ticker)
    return calls


def test_fmp_answer_wins_and_yahoo_is_never_consulted(monkeypatch):
    _patch_fmp(monkeypatch, payload={"floatShares": 933_061})
    yahoo_calls = _patch_yahoo(monkeypatch, float_value=999)

    assert float_lookup.float_shares("XPON") == 933_061
    assert yahoo_calls == []


def test_yahoo_answers_when_fmp_is_rate_limited(monkeypatch):
    """The whole point of the chain: FMP's quota running dry (a real recurring event)
    must not blank the float pillar for the day's top gainers."""
    _patch_fmp(monkeypatch, error=Exception("FMP rate limit reached"))
    _patch_yahoo(monkeypatch, float_value=933_061)

    assert float_lookup.float_shares("XPON") == 933_061


def test_yahoo_result_is_cached_for_subsequent_calls(monkeypatch):
    _patch_fmp(monkeypatch, payload=None)
    yahoo_calls = _patch_yahoo(monkeypatch, float_value=477_666)

    assert float_lookup.float_shares("JUNS") == 477_666
    assert float_lookup.float_shares("JUNS") == 477_666  # served from cache

    assert yahoo_calls == ["JUNS"]


def test_returns_none_when_both_sources_fail(monkeypatch):
    _patch_fmp(monkeypatch, error=Exception("FMP down"))
    _patch_yahoo(monkeypatch, error=Exception("Yahoo down"))

    assert float_lookup.float_shares("XPON") is None


def test_a_yahoo_failure_is_not_cached_so_the_next_call_retries(monkeypatch):
    _patch_fmp(monkeypatch, payload=None)
    _patch_yahoo(monkeypatch, error=Exception("transient"))
    assert float_lookup.float_shares("XPON") is None

    _patch_yahoo(monkeypatch, float_value=933_061)
    assert float_lookup.float_shares("XPON") == 933_061  # retried, not stuck on None