import requests

from app.config import settings
from app.services import notify


class _FakeResponse:
    def __init__(self, ok=True):
        self.ok = ok


def test_send_is_a_no_op_without_a_topic(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_topic", "")
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: calls.append(a) or _FakeResponse())

    assert notify.send("Title", "message") is False
    assert calls == []


def test_send_posts_to_the_topic_url_with_headers(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_topic", "test-topic-123")
    monkeypatch.setattr(settings, "ntfy_server", "https://ntfy.sh")
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers, timeout=timeout)
        return _FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    assert notify.send("Opened ACHR", "Bought 10 ACHR", priority="high", tags="moneybag") is True
    assert captured["url"] == "https://ntfy.sh/test-topic-123"
    assert captured["data"] == b"Bought 10 ACHR"
    assert captured["headers"]["Title"] == "Opened ACHR"
    assert captured["headers"]["Priority"] == "high"
    assert captured["headers"]["Tags"] == "moneybag"
    assert captured["timeout"] == 5


def test_send_never_raises_on_a_network_error(monkeypatch):
    """A notification is never worth failing a trade or an automation cycle over."""
    monkeypatch.setattr(settings, "ntfy_topic", "test-topic-123")

    def exploding_post(*args, **kwargs):
        raise requests.exceptions.ConnectionError("ntfy unreachable")

    monkeypatch.setattr(requests, "post", exploding_post)

    assert notify.send("Title", "message") is False  # swallowed, reported as not-sent


def test_send_handles_a_self_hosted_server_with_a_trailing_slash(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_topic", "my-topic")
    monkeypatch.setattr(settings, "ntfy_server", "http://ntfy.lan:8080/")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return _FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    notify.send("Title", "message")
    assert captured["url"] == "http://ntfy.lan:8080/my-topic"
