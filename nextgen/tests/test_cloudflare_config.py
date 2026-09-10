from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cloudflare_deadman_crons_are_dst_safe_and_weekday_only() -> None:
    config = json.loads((ROOT / "cloudflare" / "wrangler.jsonc").read_text(encoding="utf-8"))
    assert set(config["triggers"]["crons"]) == {
        "0 12 * * MON-FRI",
        "10,25 14 * * MON-FRI",
        "10,25 15 * * MON-FRI",
    }


def test_dispatch_credential_is_never_committed_as_plaintext() -> None:
    config = json.loads((ROOT / "cloudflare" / "wrangler.jsonc").read_text(encoding="utf-8"))
    assert "GH_ACTIONS_DISPATCH_TOKEN" not in config.get("vars", {})
    source = (ROOT / "cloudflare" / "src" / "index.js").read_text(encoding="utf-8")
    assert "env.GH_ACTIONS_DISPATCH_TOKEN" in source
    assert "await verifyGithubWorkflow(env, now);" in source
    assert "await dispatchFallback(env, now);" in source


def test_mobile_dashboard_is_installable_and_operator_refresh_is_paper_only() -> None:
    source = (ROOT / "cloudflare" / "src" / "index.js").read_text(encoding="utf-8")
    dashboard = (ROOT / "cloudflare" / "src" / "dashboard.js").read_text(encoding="utf-8")
    assert 'url.pathname === "/manifest.webmanifest"' in source
    assert 'url.pathname === "/icon-192.png"' in source
    assert 'url.pathname === "/icon-512.png"' in source
    assert 'url.pathname === "/dashboard/refresh"' in source
    assert "requireDashboardAuth(request, env);" in source
    assert 'env.GH_WORKFLOW_FILE !== "trend3-paper.yml"' in source
    assert "10 * 60 * 1000" in source
    assert 'href="/manifest.webmanifest"' in dashboard
    assert 'apple-mobile-web-app-capable' in dashboard
    assert "Sync broker now" in dashboard
    assert "PAPER TRADING" in dashboard


def test_dashboard_status_includes_equity_history_and_sanitized_event_log() -> None:
    source = (ROOT / "cloudflare" / "src" / "index.js").read_text(encoding="utf-8")
    assert "FROM equity_snapshots ORDER BY observed_at DESC LIMIT 180" in source
    assert "status.equity_history" in source
    assert "FROM events ORDER BY id DESC LIMIT 30" in source
    assert "status.recent_events" in source


def test_worker_observability_is_enabled() -> None:
    config = json.loads((ROOT / "cloudflare" / "wrangler.jsonc").read_text(encoding="utf-8"))
    assert config["observability"] == {"enabled": True, "head_sampling_rate": 1}
