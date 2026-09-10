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
