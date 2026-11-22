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

async function count(kind, env) {
  const row = kind === "installs"
    ? await env.DB.prepare("SELECT COUNT(*) AS n FROM installs").first()
    : await env.DB.prepare("SELECT COUNT(DISTINCT id) AS n FROM active WHERE day >= ?").bind(day(KEEP_DAYS - 1)).first();
  return Number(row.n);
}

async function page(env) {
  const [installs, active] = await Promise.all([count("installs", env), count("active", env)]);
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bolo user count</title>
<style>:root{color-scheme:light dark}body{font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:620px;margin:0 auto;padding:56px 24px}
h1{font-size:28px;margin:0 0 24px}.n{display:flex;gap:16px;margin-bottom:32px}.n div{flex:1;border:1px solid #8884;border-radius:12px;padding:18px}
b{display:block;font-size:36px;color:#2563eb;line-height:1.2}a{color:#2563eb}</style></head><body>
<h1>Bolo user count</h1><div class="n"><div><b>${installs}</b>Installs</div><div><b>${active}</b>Active users, last 30 days</div></div>
<p>Only people who say yes are counted, so the real numbers can be higher. Bolo sends one random ID at most once a day.
The counter stores a scrambled version of that ID and the day. It stores no name, IP address, text or audio.</p>
<p><a href="https://github.com/soumyaux/bolo">Bolo on GitHub</a> · <a href="https://github.com/soumyaux/bolo/blob/main/counter/worker.js">Counter code</a></p>
</body></html>`;
  return new Response(html, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=300" } });
}

async function badge(kind, env) {
  const n = await count(kind, env);
  const label = kind === "installs" ? "installs" : "active users (30 days)";
  return Response.json(
    { schemaVersion: 1, label, message: String(n), color: "2563eb" },
    { headers: { "cache-control": "public, max-age=3600" } },
  );
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "GET" && pathname === "/") return page(env);
    if (request.method === "POST" && pathname === "/v1/seen") return seen(request, env);
    if (request.method === "GET" && pathname === "/v1/badge/installs") return badge("installs", env);
    if (request.method === "GET" && pathname === "/v1/badge/active") return badge("active", env);
    return new Response("not found", { status: 404 });
  },
  async scheduled(_event, env) {
    await env.DB.prepare("DELETE FROM active WHERE day < ?").bind(day(KEEP_DAYS - 1)).run();
  },
};
