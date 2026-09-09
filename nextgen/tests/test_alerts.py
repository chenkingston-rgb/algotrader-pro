from types import SimpleNamespace

import pytest

import trader.alerts as alerts


def test_alert_requires_https():
    with pytest.raises(RuntimeError, match="HTTPS"):
        alerts.send_alert("http://example.com/hook", "title", "detail")


def test_heartbeat_rejects_embedded_credentials():
    with pytest.raises(RuntimeError, match="embedded credentials"):
        alerts.ping_heartbeat("https://user:secret@example.com/ping")


def test_alert_posts_without_following_redirects(monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(alerts.requests, "post", fake_post)
    alerts.send_alert("https://example.com/hook", "title", "detail")
    assert seen["json"] == {"text": "title\ndetail"}
    assert seen["allow_redirects"] is False
    assert seen["timeout"] == 15


def test_heartbeat_gets_without_following_redirects(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(alerts.requests, "get", fake_get)
    alerts.ping_heartbeat("https://example.com/ping")
    assert seen["allow_redirects"] is False
    assert seen["timeout"] == 15


def test_discord_webhook_uses_content_field(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        alerts.requests,
        "post",
        lambda url, **kwargs: seen.update(url=url, **kwargs) or SimpleNamespace(status_code=204),
    )
    alerts.send_alert("https://discord.com/api/webhooks/id/token", "title", "detail")
    assert seen["json"] == {"content": "title\ndetail"}
