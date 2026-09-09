from __future__ import annotations

from urllib.parse import urlsplit

import requests


def _require_https(url: str, variable: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError(f"{variable} must be an HTTPS URL without embedded credentials")


def send_alert(webhook_url: str, title: str, detail: str) -> None:
    if not webhook_url:
        raise RuntimeError("ALERT_WEBHOOK_URL is not configured")
    _require_https(webhook_url, "ALERT_WEBHOOK_URL")
    host = (urlsplit(webhook_url).hostname or "").lower()
    message = f"{title}\n{detail}"
    # Discord and Slack both offer no-cost incoming webhooks but use different
    # JSON field names.  Host-based selection avoids another mutable setting.
    payload = {"content": message[:1900]} if host.endswith("discord.com") else {"text": message}
    response = requests.post(
        webhook_url,
        json=payload,
        timeout=15,
        allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Alert webhook returned HTTP {response.status_code}")


def ping_heartbeat(url: str) -> None:
    if not url:
        raise RuntimeError("HEARTBEAT_URL is not configured")
    _require_https(url, "HEARTBEAT_URL")
    response = requests.get(url, timeout=15, allow_redirects=False)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Heartbeat returned HTTP {response.status_code}")
