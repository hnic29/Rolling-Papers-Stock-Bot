import requests

from app.config import settings
from app.services import notify


class _FakeResponse:
    def __init__(self, ok=True):
        self.ok = ok


def test_send_is_a_no_op_without_a_topic(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: calls.append(a) or _FakeResponse())

    assert notify.send("", "Title", "message") is False
    assert calls == []


def test_send_posts_to_the_topic_url_with_headers(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_server", "https://ntfy.sh")
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers, timeout=timeout)
        return _FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    assert notify.send("test-topic-123", "Opened ACHR", "Bought 10 ACHR", priority="high", tags="moneybag") is True
    assert captured["url"] == "https://ntfy.sh/test-topic-123"
    assert captured["data"] == b"Bought 10 ACHR"
    assert captured["headers"]["Title"] == "Opened ACHR"
    assert captured["headers"]["Priority"] == "high"
    assert captured["headers"]["Tags"] == "moneybag"
    assert captured["timeout"] == 5


def test_send_never_raises_on_a_network_error(monkeypatch):
    """A notification is never worth failing a trade or an automation cycle over."""

    def exploding_post(*args, **kwargs):
        raise requests.exceptions.ConnectionError("ntfy unreachable")

    monkeypatch.setattr(requests, "post", exploding_post)

    assert notify.send("test-topic-123", "Title", "message") is False  # swallowed, reported as not-sent


def test_send_handles_a_self_hosted_server_with_a_trailing_slash(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_server", "http://ntfy.lan:8080/")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return _FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    notify.send("my-topic", "Title", "message")
    assert captured["url"] == "http://ntfy.lan:8080/my-topic"


def test_two_users_own_topics_are_fully_independent(monkeypatch):
    """The whole point of making topic a parameter, not a global setting - one
    person's phone must never get another person's trade alerts."""
    posted_urls = []

    def fake_post(url, **kwargs):
        posted_urls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(settings, "ntfy_server", "https://ntfy.sh")

    notify.send("alice-topic", "Title", "message")
    notify.send("bob-topic", "Title", "message")

    assert posted_urls == ["https://ntfy.sh/alice-topic", "https://ntfy.sh/bob-topic"]
