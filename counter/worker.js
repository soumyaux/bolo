// Bolo's public counter. Stores a hash of a random ID and the day, nothing else: no IP addresses.
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const KEEP_DAYS = 30;

const day = (ago = 0) => new Date(Date.now() - ago * 86400000).toISOString().slice(0, 10);

async function hash(id) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`bolo:${id}`));
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function seen(request, env) {
  const body = await request.json().catch(() => null);
  if (!body || typeof body.id !== "string" || !ID.test(body.id)) return new Response("bad request", { status: 400 });
  const id = await hash(body.id);
  await env.DB.batch([
    env.DB.prepare("INSERT OR IGNORE INTO installs (id) VALUES (?)").bind(id),
    env.DB.prepare("INSERT OR IGNORE INTO active (id, day) VALUES (?, ?)").bind(id, day()),
  ]);
  return new Response(null, { status: 204 });
}

async function badge(kind, env) {
  const row = kind === "installs"
    ? await env.DB.prepare("SELECT COUNT(*) AS n FROM installs").first()
    : await env.DB.prepare("SELECT COUNT(DISTINCT id) AS n FROM active WHERE day >= ?").bind(day(KEEP_DAYS - 1)).first();
  const label = kind === "installs" ? "installs" : "active users (30 days)";
  return Response.json(
    { schemaVersion: 1, label, message: String(row.n), color: "2563eb" },
    { headers: { "cache-control": "public, max-age=3600" } },
  );
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "POST" && pathname === "/v1/seen") return seen(request, env);
    if (request.method === "GET" && pathname === "/v1/badge/installs") return badge("installs", env);
    if (request.method === "GET" && pathname === "/v1/badge/active") return badge("active", env);
    return new Response("not found", { status: 404 });
  },
  async scheduled(_event, env) {
    await env.DB.prepare("DELETE FROM active WHERE day < ?").bind(day(KEEP_DAYS - 1)).run();
  },
};
