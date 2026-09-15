// Supabase Edge Function: browser -> here -> Modal.
//
// The dashboard needs live projections, but an API key shipped to a browser is a
// public API key: anyone can read it out of the page and spend the Modal credits it
// unlocks. So the page calls this function, and this function - running on Supabase,
// not in the browser - is the only thing that holds MODAL_API_KEY.
//
// Deploy:
//   supabase secrets set MODAL_API_KEY=<the key in .env>
//   supabase secrets set MODAL_API_URL=https://<workspace>--ufa-mvp-api-fastapi-app.modal.run
//   supabase functions deploy mvp-proxy --no-verify-jwt
//
// --no-verify-jwt makes it publicly callable, which is the point: the dashboard is a
// public page with no login. The protections that matter are below - a strict route
// allowlist, a body-size cap, and a per-IP rate limit - because this endpoint is
// effectively a spend button for someone else's money.

const MODAL_API_URL = Deno.env.get("MODAL_API_URL") ?? "";
const MODAL_API_KEY = Deno.env.get("MODAL_API_KEY") ?? "";

// Which origins may call this. Set ALLOWED_ORIGINS to your deployed domain(s);
// "*" is the fallback so a local file:// page can still talk to it during testing.
const ALLOWED_ORIGINS = (Deno.env.get("ALLOWED_ORIGINS") ?? "*")
  .split(",").map((s) => s.trim()).filter(Boolean);

// Only these reach Modal. /players is not here: the page already ships the roster,
// and proxying a 451-row list on every load would burn container time for nothing.
const ALLOWED_ROUTES = new Set(["/predict", "/compare", "/health"]);

const MAX_BODY_BYTES = 8 * 1024;
const RATE_LIMIT = 30;                 // requests per window per IP
const RATE_WINDOW_MS = 60_000;

// Per-instance, so it resets when the function scales. Not a real quota - a cheap
// brake on a single abusive client. Modal's max_containers=2 is the actual ceiling.
const hits = new Map<string, number[]>();

function rateLimited(ip: string): boolean {
  const now = Date.now();
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < RATE_WINDOW_MS);
  recent.push(now);
  hits.set(ip, recent);
  if (hits.size > 5000) hits.clear();   // bound the memory, bluntly
  return recent.length > RATE_LIMIT;
}

function corsHeaders(origin: string | null): Record<string, string> {
  const allow = ALLOWED_ORIGINS.includes("*")
    ? "*"
    : (origin && ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0] ?? "");
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function json(body: unknown, status: number, origin: string | null): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders(origin), "Content-Type": "application/json" },
  });
}

Deno.serve(async (req) => {
  const origin = req.headers.get("origin");

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(origin) });
  }
  if (req.method !== "POST") {
    return json({ error: "POST only" }, 405, origin);
  }
  if (!MODAL_API_URL || !MODAL_API_KEY) {
    return json({ error: "proxy is not configured" }, 503, origin);
  }

  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
  if (rateLimited(ip)) {
    return json({ error: "rate limited - try again in a minute" }, 429, origin);
  }

  const raw = await req.text();
  if (raw.length > MAX_BODY_BYTES) {
    return json({ error: "body too large" }, 413, origin);
  }

  let payload: { route?: string; body?: unknown };
  try {
    payload = JSON.parse(raw || "{}");
  } catch {
    return json({ error: "body must be JSON" }, 400, origin);
  }

  const route = payload.route ?? "/compare";
  if (!ALLOWED_ROUTES.has(route)) {
    return json({ error: `route ${route} is not proxied` }, 403, origin);
  }

  try {
    const upstream = await fetch(MODAL_API_URL.replace(/\/$/, "") + route, {
      method: route === "/health" ? "GET" : "POST",
      headers: {
        "X-API-Key": MODAL_API_KEY,          // never leaves this function
        "Content-Type": "application/json",
      },
      body: route === "/health" ? undefined : JSON.stringify(payload.body ?? {}),
      signal: AbortSignal.timeout(25_000),   // Modal cold starts, but not forever
    });
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { ...corsHeaders(origin), "Content-Type": "application/json" },
    });
  } catch (e) {
    // The page falls back to its embedded projections when this happens, so a cold
    // start that times out degrades to stale-but-correct numbers rather than an error.
    return json({ error: "upstream unavailable", detail: String(e) }, 502, origin);
  }
});
