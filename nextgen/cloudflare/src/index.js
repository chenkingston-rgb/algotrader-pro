import { DASHBOARD_HTML } from "./dashboard.js";

const TRANSITIONS = Object.freeze({
  CREATED: ["DATA_VALIDATED", "ATTENTION", "FAILED"],
  DATA_VALIDATED: ["SIGNAL_LOCKED", "ATTENTION", "FAILED"],
  SIGNAL_LOCKED: ["RECONCILED", "ATTENTION", "FAILED"],
  RECONCILED: ["SELLING", "VERIFYING", "ATTENTION", "FAILED"],
  SELLING: ["SELLS_CONFIRMED", "ATTENTION", "FAILED"],
  SELLS_CONFIRMED: ["BUYING", "VERIFYING", "ATTENTION", "FAILED"],
  BUYING: ["VERIFYING", "ATTENTION", "FAILED"],
  VERIFYING: ["COMPLETE", "ATTENTION", "FAILED"],
  COMPLETE: [],
  ATTENTION: [],
  FAILED: [],
});

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store, max-age=0",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
    },
  });
}

function dashboardPage() {
  return new Response(DASHBOARD_HTML, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store, max-age=0",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
      "x-frame-options": "DENY",
      "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
    },
  });
}

function safeEqual(left, right) {
  const a = String(left || "");
  const b = String(right || "");
  let diff = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    diff |= (a.charCodeAt(i % Math.max(a.length, 1)) || 0)
      ^ (b.charCodeAt(i % Math.max(b.length, 1)) || 0);
  }
  return diff === 0;
}

function bearer(request) {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

function requireDashboardAuth(request, env) {
  const supplied = request.headers.get("authorization") || "";
  const expectedUser = String(env.DASHBOARD_USERNAME || "");
  const expectedPassword = String(env.DASHBOARD_PASSWORD || "");
  if (!supplied.startsWith("Basic ") || !expectedUser || !expectedPassword) {
    throw Object.assign(new Error("unauthorized"), { status: 401 });
  }
  let decoded = "";
  try {
    decoded = atob(supplied.slice(6));
  } catch (_error) {
    throw Object.assign(new Error("unauthorized"), { status: 401 });
  }
  if (!safeEqual(decoded, `${expectedUser}:${expectedPassword}`)) {
    throw Object.assign(new Error("unauthorized"), { status: 401 });
  }
}

function requireToken(request, expected) {
  if (!expected || !safeEqual(bearer(request), expected)) {
    throw Object.assign(new Error("unauthorized"), { status: 401 });
  }
}

async function bodyObject(request) {
  const size = Number(request.headers.get("content-length") || "0");
  if (size > 900_000) throw Object.assign(new Error("request too large"), { status: 413 });
  const value = await request.json();
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw Object.assign(new Error("JSON object required"), { status: 400 });
  }
  return value;
}

function requiredString(value, field, maximum = 256) {
  if (typeof value !== "string" || !value || value.length > maximum) {
    throw Object.assign(new Error(`${field} is invalid`), { status: 400 });
  }
  return value;
}

function encodePayload(payload) {
  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    throw Object.assign(new Error("payload object required"), { status: 400 });
  }
  const encoded = JSON.stringify(payload);
  if (encoded.length > 850_000) {
    throw Object.assign(new Error("payload too large"), { status: 413 });
  }
  return encoded;
}

function decodePayload(raw) {
  return raw ? JSON.parse(raw) : null;
}

async function startRun(env, request) {
  const body = await bodyObject(request);
  const runId = requiredString(body.run_id, "run_id");
  const payload = encodePayload(body.payload);
  const now = new Date().toISOString();
  const insert = await env.DB.prepare(
    "INSERT OR IGNORE INTO runs(run_id,status,payload,updated_at) VALUES(?1,'CREATED',?2,?3)"
  ).bind(runId, payload, now).run();
  if ((insert.meta?.changes || 0) === 1) {
    await env.DB.prepare(
      "INSERT INTO events(run_id,event,payload,created_at) VALUES(?1,'START',?2,?3)"
    ).bind(runId, payload, now).run();
  }
  const row = await env.DB.prepare("SELECT status FROM runs WHERE run_id=?1").bind(runId).first();
  if (!row) throw new Error("run insert was not durable");
  return json({ ok: true, status: row.status });
}

async function transitionRun(env, request) {
  const body = await bodyObject(request);
  const runId = requiredString(body.run_id, "run_id");
  const next = requiredString(body.new_status, "new_status", 32);
  const row = await env.DB.prepare("SELECT status FROM runs WHERE run_id=?1").bind(runId).first();
  if (!row) throw Object.assign(new Error("unknown run"), { status: 404 });
  const old = String(row.status);
  if (!(TRANSITIONS[old] || []).includes(next)) {
    throw Object.assign(new Error(`illegal transition ${old} -> ${next}`), { status: 409 });
  }
  const payload = encodePayload(body.payload);
  const now = new Date().toISOString();
  const results = await env.DB.batch([
    env.DB.prepare(
      "UPDATE runs SET status=?1,payload=?2,updated_at=?3 WHERE run_id=?4 AND status=?5"
    ).bind(next, payload, now, runId, old),
    env.DB.prepare(
      "INSERT INTO events(run_id,event,payload,created_at) "
        + "SELECT ?1,?2,?3,?4 WHERE EXISTS "
        + "(SELECT 1 FROM runs WHERE run_id=?1 AND status=?5 AND updated_at=?4)"
    ).bind(runId, `${old}->${next}`, payload, now, next),
  ]);
  if ((results[0].meta?.changes || 0) !== 1) {
    throw Object.assign(new Error("concurrent transition rejected"), { status: 409 });
  }
  return json({ ok: true, old_status: old, status: next });
}

async function runStatus(env, url) {
  const runId = requiredString(url.searchParams.get("run_id"), "run_id");
  const row = await env.DB.prepare("SELECT status FROM runs WHERE run_id=?1").bind(runId).first();
  return json({ ok: true, status: row?.status || null });
}

async function runPayload(env, url) {
  const runId = requiredString(url.searchParams.get("run_id"), "run_id");
  const row = await env.DB.prepare("SELECT payload FROM runs WHERE run_id=?1").bind(runId).first();
  if (!row) throw Object.assign(new Error("unknown run"), { status: 404 });
  return json({ ok: true, payload: decodePayload(row.payload) });
}

async function latestRun(env, url) {
  const status = url.searchParams.get("status");
  const query = status === "COMPLETE"
    ? "SELECT status,payload FROM runs WHERE status='COMPLETE' ORDER BY updated_at DESC LIMIT 1"
    : "SELECT status,payload FROM runs WHERE status IN ('COMPLETE','ATTENTION') ORDER BY updated_at DESC LIMIT 1";
  const row = await env.DB.prepare(query).first();
  if (!row) return json({ ok: true, payload: null });
  const payload = decodePayload(row.payload);
  payload._terminal_status = row.status;
  return json({ ok: true, payload });
}

async function errorEvent(env, request) {
  const body = await bodyObject(request);
  const now = new Date().toISOString();
  const payload = encodePayload({ error: body.error, ...(body.context || {}) });
  await env.DB.prepare(
    "INSERT INTO events(run_id,event,payload,created_at) VALUES(?1,'ERROR',?2,?3)"
  ).bind(body.run_id || null, payload, now).run();
  return json({ ok: true });
}

async function recordEquity(env, request) {
  const body = await bodyObject(request);
  const equity = Number(body.equity);
  const cashFlow = Number(body.cumulative_external_cash_flow || 0);
  const peakFloor = Number(body.peak_floor || 0);
  const observed = requiredString(body.observed_at, "observed_at", 64);
  const adjusted = equity - cashFlow;
  if (!(equity > 0) || !(adjusted > 0) || !Number.isFinite(peakFloor)) {
    throw Object.assign(new Error("equity inputs are invalid"), { status: 400 });
  }
  await env.DB.prepare(
    "INSERT OR REPLACE INTO equity_snapshots(observed_at,equity,adjusted_equity) VALUES(?1,?2,?3)"
  ).bind(observed, equity, adjusted).run();
  const row = await env.DB.prepare("SELECT MAX(adjusted_equity) AS peak FROM equity_snapshots").first();
  const peak = Math.max(Number(row?.peak || 0), peakFloor);
  if (!(peak > 0)) throw new Error("equity peak was not durable");
  return json({
    ok: true,
    risk: {
      equity,
      adjusted_equity: adjusted,
      cumulative_external_cash_flow: cashFlow,
      peak_equity: peak,
      drawdown_pct: (adjusted / peak - 1) * 100,
    },
  });
}

async function putStatus(env, request) {
  const payload = await bodyObject(request);
  const generated = requiredString(payload.generated_at, "generated_at", 64);
  const now = new Date().toISOString();
  const encoded = encodePayload(payload);
  await env.DB.prepare(
    "INSERT INTO latest_status(singleton,payload,generated_at,updated_at) VALUES(1,?1,?2,?3) "
      + "ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload,generated_at=excluded.generated_at,updated_at=excluded.updated_at"
  ).bind(encoded, generated, now).run();
  return json({ ok: true });
}

async function getStatus(env) {
  const row = await env.DB.prepare("SELECT payload FROM latest_status WHERE singleton=1").first();
  if (!row) throw Object.assign(new Error("status has never been published"), { status: 404 });
  const status = decodePayload(row.payload);
  const control = await env.DB.prepare(
    "SELECT key,value,updated_at FROM control WHERE key IN ('last_deadman_check','last_deadman_error','last_fallback_date')"
  ).all();
  const values = Object.fromEntries((control.results || []).map((x) => [x.key, x]));
  status.services = status.services || {};
  status.services.deadman_last_check_at = values.last_deadman_check?.updated_at || null;
  status.services.deadman_last_error = values.last_deadman_error?.value || null;
  status.services.last_fallback_date = values.last_fallback_date?.value || null;
  const problems = [...(status.health?.problems || [])];
  const checkedAt = Date.parse(values.last_deadman_check?.updated_at || "");
  const checkAgeHours = (Date.now() - checkedAt) / 3_600_000;
  if (!Number.isFinite(checkAgeHours) || checkAgeHours > 26) problems.push("independent scheduler check is stale or missing");
  const errorAt = Date.parse(values.last_deadman_error?.updated_at || "");
  if (Number.isFinite(errorAt) && (!Number.isFinite(checkedAt) || errorAt >= checkedAt)) {
    problems.push("independent scheduler reported an error");
  }
  status.health = { healthy: status.health?.healthy === true && problems.length === 0, problems };
  const alerts = await env.DB.prepare(
    "SELECT payload,created_at FROM events WHERE event='ALERT' ORDER BY id DESC LIMIT 10"
  ).all();
  status.alerts = (alerts.results || []).map((row) => ({
    created_at: row.created_at,
    message: decodePayload(row.payload)?.message || "Alert details unavailable",
  }));
  return json({ ok: true, status });
}

async function heartbeat(env, request, url) {
  const secret = url.pathname.slice("/v1/heartbeat/".length);
  if (!env.HEARTBEAT_SECRET || !safeEqual(secret, env.HEARTBEAT_SECRET)) {
    throw Object.assign(new Error("unauthorized"), { status: 401 });
  }
  const now = new Date().toISOString();
  if (request.method === "POST") {
    const body = await bodyObject(request);
    const message = requiredString(body.text || body.content, "alert", 2_000);
    await env.DB.prepare(
      "INSERT INTO events(run_id,event,payload,created_at) VALUES(NULL,'ALERT',?1,?2)"
    ).bind(JSON.stringify({ message }), now).run();
    return json({ ok: true });
  }
  await env.DB.prepare(
    "INSERT INTO control(key,value,updated_at) VALUES('last_heartbeat',?1,?1) "
      + "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at"
  ).bind(now).run();
  return json({ ok: true });
}

async function handle(request, env) {
  const url = new URL(request.url);
  if (request.method === "GET" && ["/dashboard", "/dashboard/"].includes(url.pathname)) {
    return dashboardPage();
  }
  if (request.method === "GET" && url.pathname === "/dashboard/status") {
    requireDashboardAuth(request, env);
    return getStatus(env);
  }
  if (request.method === "GET" && url.pathname === "/v1/health") {
    return json({ ok: true, service: "trend3-qqq20-state" });
  }
  if (["GET", "POST"].includes(request.method) && url.pathname.startsWith("/v1/heartbeat/")) {
    return heartbeat(env, request, url);
  }
  if (request.method === "GET" && url.pathname === "/v1/status") {
    requireToken(request, env.STATE_API_READ_TOKEN);
    return getStatus(env);
  }
  requireToken(request, env.STATE_API_WRITE_TOKEN);
  if (request.method === "POST" && url.pathname === "/v1/runs/start") return startRun(env, request);
  if (request.method === "POST" && url.pathname === "/v1/runs/transition") return transitionRun(env, request);
  if (request.method === "GET" && url.pathname === "/v1/runs/status") return runStatus(env, url);
  if (request.method === "GET" && url.pathname === "/v1/runs/payload") return runPayload(env, url);
  if (request.method === "GET" && url.pathname === "/v1/runs/latest") return latestRun(env, url);
  if (request.method === "POST" && url.pathname === "/v1/events/error") return errorEvent(env, request);
  if (request.method === "POST" && url.pathname === "/v1/equity") return recordEquity(env, request);
  if (request.method === "PUT" && url.pathname === "/v1/status") return putStatus(env, request);
  return json({ ok: false, error: "not found" }, 404);
}

function newYorkParts(now) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    weekday: "short",
  }).formatToParts(now);
  return Object.fromEntries(parts.map((p) => [p.type, p.value]));
}

function githubHeaders(env) {
  return {
    authorization: `Bearer ${env.GH_ACTIONS_DISPATCH_TOKEN}`,
    accept: "application/vnd.github+json",
    "x-github-api-version": "2022-11-28",
    "user-agent": "trend3-qqq20-cloudflare-deadman",
  };
}

async function verifyGithubWorkflow(env, now) {
  const endpoint = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}`
    + `/actions/workflows/${env.GH_WORKFLOW_FILE}`;
  const response = await fetch(endpoint, { headers: githubHeaders(env) });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`GitHub workflow verification failed: HTTP ${response.status}: ${detail}`);
  }
  const value = await response.json();
  if (!value?.id || !["active", "disabled_inactivity"].includes(value.state)) {
    throw new Error(`GitHub workflow is missing or unusable: ${JSON.stringify(value).slice(0, 500)}`);
  }
  const stamp = now.toISOString();
  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO control(key,value,updated_at) VALUES('last_deadman_check','ok',?1) "
        + "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at"
    ).bind(stamp),
    env.DB.prepare("DELETE FROM control WHERE key='last_deadman_error'"),
  ]);
}

async function dispatchFallback(env, now) {
  const local = newYorkParts(now);
  const minuteOfDay = Number(local.hour) * 60 + Number(local.minute);
  if (minuteOfDay < 610 || minuteOfDay > 629) return;
  const dateKey = `${local.year}-${local.month}-${local.day}`;
  const prior = await env.DB.prepare("SELECT value FROM control WHERE key='last_fallback_date'").first();
  if (prior?.value === dateKey) return;

  const statusRow = await env.DB.prepare("SELECT payload,generated_at FROM latest_status WHERE singleton=1").first();
  let needsFallback = true;
  if (statusRow) {
    const ageMinutes = (now.getTime() - Date.parse(statusRow.generated_at)) / 60000;
    const payload = decodePayload(statusRow.payload);
    needsFallback = !Number.isFinite(ageMinutes) || ageMinutes > 45 || payload?.health?.healthy !== true;
  }
  if (!needsFallback) return;

  const endpoint = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}`
    + `/actions/workflows/${env.GH_WORKFLOW_FILE}/dispatches`;
  const commonHeaders = githubHeaders(env);
  const enableEndpoint = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}`
    + `/actions/workflows/${env.GH_WORKFLOW_FILE}/enable`;
  const enabled = await fetch(enableEndpoint, { method: "PUT", headers: commonHeaders });
  if (enabled.status !== 204) {
    const detail = (await enabled.text()).slice(0, 500);
    throw new Error(`GitHub workflow enable failed: HTTP ${enabled.status}: ${detail}`);
  }
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      ...commonHeaders,
      "content-type": "application/json",
    },
    body: JSON.stringify({ ref: env.GH_REF || "main", inputs: { reason: "cloudflare-deadman" } }),
  });
  if (response.status !== 204) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`GitHub fallback dispatch failed: HTTP ${response.status}: ${detail}`);
  }
  const stamp = now.toISOString();
  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO control(key,value,updated_at) VALUES('last_fallback_date',?1,?2) "
        + "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at"
    ).bind(dateKey, stamp),
    env.DB.prepare(
      "INSERT INTO events(run_id,event,payload,created_at) VALUES(NULL,'FALLBACK_DISPATCH',?1,?2)"
    ).bind(JSON.stringify({ date: dateKey, source: "cloudflare-deadman" }), stamp),
  ]);
}

export default {
  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (error) {
      return json({ ok: false, error: error?.message || "internal error" }, error?.status || 500);
    }
  },
  async scheduled(_event, env, ctx) {
    ctx.waitUntil((async () => {
      const now = new Date();
      // Every configured trigger verifies the credential and workflow first.
      // dispatchFallback applies the 10:10-10:29 America/New_York gate itself,
      // so the 12:00 UTC credential check can never dispatch a trade run.
      await verifyGithubWorkflow(env, now);
      await dispatchFallback(env, now);
    })().catch(async (error) => {
      const now = new Date().toISOString();
      const encoded = JSON.stringify({ error: String(error) }).slice(0, 2000);
      await env.DB.batch([
        env.DB.prepare(
          "INSERT INTO events(run_id,event,payload,created_at) VALUES(NULL,'FALLBACK_ERROR',?1,?2)"
        ).bind(encoded, now),
        env.DB.prepare(
          "INSERT INTO control(key,value,updated_at) VALUES('last_deadman_error',?1,?2) "
            + "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at"
        ).bind(encoded, now),
      ]);
      throw error;
    }));
  },
};
