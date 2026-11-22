// Bolo's anonymous analytics. Keeps two public totals and nothing else: no IDs, IP addresses or text.
const MAX_WORDS = 200000;
const LABELS = { installs: "installs", words: "words dictated" };
const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

const add = (env, name, amount) =>
  env.DB.prepare("INSERT INTO totals (name, value) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET value = value + excluded.value")
    .bind(name, amount)
    .run();

async function words(request, env) {
  const body = await request.json().catch(() => null);
  const valid = body && Object.keys(body).length === 1 && Number.isSafeInteger(body.words)
    && body.words >= 1 && body.words <= MAX_WORDS;
  if (!valid) return new Response("bad request", { status: 400 });
  await add(env, "words", body.words);
  return new Response(null, { status: 204 });
}

async function badge(name, env) {
  const row = await env.DB.prepare("SELECT value FROM totals WHERE name = ?").bind(name).first();
  return Response.json(
    { schemaVersion: 1, label: LABELS[name], message: compact.format(row?.value ?? 0), color: "2563eb" },
    { headers: { "cache-control": "public, max-age=3600" } },
  );
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "POST" && pathname === "/v1/install") {
      await add(env, "installs", 1);
      return new Response(null, { status: 204 });
    }
    if (request.method === "POST" && pathname === "/v1/words") return words(request, env);
    if (request.method === "GET" && pathname === "/v1/badge/installs") return badge("installs", env);
    if (request.method === "GET" && pathname === "/v1/badge/words") return badge("words", env);
    return new Response("not found", { status: 404 });
  },
};
