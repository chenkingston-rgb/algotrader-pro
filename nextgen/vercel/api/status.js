import crypto from "node:crypto";

function equal(a, b) {
  const left = Buffer.from(String(a || ""));
  const right = Buffer.from(String(b || ""));
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store, max-age=0");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");
  if (req.method !== "GET") return res.status(405).json({ error: "method not allowed" });

  const expected = Buffer.from(
    `${process.env.DASHBOARD_USERNAME || ""}:${process.env.DASHBOARD_PASSWORD || ""}`
  ).toString("base64");
  const supplied = String(req.headers.authorization || "").replace(/^Basic\s+/i, "");
  if (!process.env.DASHBOARD_USERNAME || !process.env.DASHBOARD_PASSWORD || !equal(supplied, expected)) {
    res.setHeader("WWW-Authenticate", 'Basic realm="AlgoTrader status"');
    return res.status(401).json({ error: "unauthorized" });
  }

  const base = String(process.env.STATE_API_URL || "").replace(/\/$/, "");
  const token = process.env.STATE_API_READ_TOKEN || "";
  if (!base.startsWith("https://") || !token) {
    return res.status(503).json({ error: "status service is not configured" });
  }
  try {
    const upstream = await fetch(`${base}/v1/status`, {
      headers: { Authorization: `Bearer ${token}`, "User-Agent": "trend3-qqq20-vercel-dashboard" },
      cache: "no-store",
    });
    if (!upstream.ok) return res.status(502).json({ error: `status upstream returned ${upstream.status}` });
    const body = await upstream.json();
    if (body?.ok !== true || !body.status) return res.status(502).json({ error: "invalid status response" });
    return res.status(200).json(body.status);
  } catch (_error) {
    return res.status(502).json({ error: "status service unavailable" });
  }
}
