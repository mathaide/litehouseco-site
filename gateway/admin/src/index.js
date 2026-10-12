// litehouse-admin: the private admin panel at admin.litehouseiq.com.
// Cloudflare Access (one-time code emailed to the admin address) stands in front of
// this Worker. The Worker checks the Access token itself as a second lock, so it
// answers no one but ADMIN_EMAIL even if the Access rule were ever loosened.
import PAGE from "./admin.html";

const SITES = [
  { site: "origin8",   label: "Origin8 Revenue OS", url: "https://origin8.litehouseiq.com" },
  { site: "income360", label: "Income360",          url: "https://psl.litehouseco.com" },
  { site: "jugaad",    label: "Jugaad",             url: "https://jugaad.litehouseiq.com" },
  { site: "myvault",   label: "MyVault",            url: "https://myvault.litehouseiq.com" },
];

// ---------- Cloudflare Access token check ----------
let certCache = { at: 0, keys: [] };
const b64u = s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0));
async function accessEmail(request, env) {
  try { return await accessEmailInner(request, env); } catch (_) { return null; }
}
async function accessEmailInner(request, env) {
  if (env.DEV_BYPASS_EMAIL) return env.DEV_BYPASS_EMAIL; // local testing only, never set in production
  const jwt = request.headers.get("cf-access-jwt-assertion");
  if (!jwt || !env.ACCESS_TEAM || !env.ACCESS_AUD) return null;
  const [h, p, s] = jwt.split(".");
  if (!h || !p || !s) return null;
  const header = JSON.parse(new TextDecoder().decode(b64u(h)));
  const payload = JSON.parse(new TextDecoder().decode(b64u(p)));
  if (Date.now() - certCache.at > 3600_000 || !certCache.keys.length) {
    const r = await fetch(`https://${env.ACCESS_TEAM}/cdn-cgi/access/certs`);
    certCache = { at: Date.now(), keys: (await r.json()).keys || [] };
  }
  const jwk = certCache.keys.find(k => k.kid === header.kid);
  if (!jwk) return null;
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64u(s), new TextEncoder().encode(h + "." + p));
  if (!ok) return null;
  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!aud.includes(env.ACCESS_AUD)) return null;
  if (payload.iss !== `https://${env.ACCESS_TEAM}`) return null;
  if (!payload.exp || payload.exp * 1000 < Date.now()) return null;
  return String(payload.email || "").toLowerCase();
}

const json = (code, body) => new Response(JSON.stringify(body), { status: code, headers: {
  "content-type": "application/json", "cache-control": "no-store", "x-robots-tag": "noindex" } });

async function audit(env, entry) {
  let list = [];
  try { list = JSON.parse(await env.CODES.get("admin:log") || "[]"); } catch (_) {}
  list.push({ ...entry, at: new Date().toISOString() });
  await env.CODES.put("admin:log", JSON.stringify(list.slice(-300)));
}

async function ikigai(env, path, method, body) {
  if (!env.IKIGAI || !env.IKIGAI_BRIDGE_KEY) return { status: 503, data: { ok: false, error: "Ikigai is not connected to the admin panel yet." } };
  const r = await env.IKIGAI.fetch("https://ikigai-api.internal/api/admin/" + path, {
    method, headers: { "content-type": "application/json", "x-admin-bridge": env.IKIGAI_BRIDGE_KEY, origin: "https://litehouseiq.com" },
    body: body ? JSON.stringify(body) : undefined });
  let data; try { data = await r.json(); } catch (_) { data = { ok: false, error: "Ikigai returned an unexpected answer." }; }
  return { status: r.status, data };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/robots.txt") return new Response("User-agent: *\nDisallow: /\n");
    const email = await accessEmail(request, env);
    if (!email || email !== String(env.ADMIN_EMAIL || "").toLowerCase()) {
      return new Response("Not found", { status: 404, headers: { "cache-control": "no-store", "x-robots-tag": "noindex" } });
    }

    if (url.pathname === "/" || url.pathname === "/index.html") {
      return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
        "x-robots-tag": "noindex", "x-frame-options": "DENY", "referrer-policy": "no-referrer",
        "content-security-policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'" } });
    }

    if (url.pathname === "/api/me") return json(200, { email });

    if (url.pathname === "/api/codes" && request.method === "GET") {
      const out = [];
      for (const s of SITES) {
        const code = (await env.CODES.get("code:" + s.site)) || null;
        let meta = null; try { meta = JSON.parse(await env.CODES.get("meta:" + s.site) || "null"); } catch (_) {}
        out.push({ ...s, code, changed: meta });
      }
      return json(200, { sites: out });
    }

    if (url.pathname === "/api/codes" && request.method === "POST") {
      let b = {}; try { b = await request.json(); } catch (_) {}
      const s = SITES.find(x => x.site === b.site);
      const code = String(b.code || "").trim();
      if (!s) return json(400, { error: "Unknown site." });
      if (!/^[0-9]{6}$/.test(code)) return json(400, { error: "The code must be exactly six digits." });
      await env.CODES.put("code:" + s.site, code);
      await env.CODES.put("meta:" + s.site, JSON.stringify({ at: new Date().toISOString(), by: email }));
      await audit(env, { action: "code_changed", site: s.site, by: email });
      return json(200, { ok: true });
    }

    if (url.pathname === "/api/log") {
      let list = []; try { list = JSON.parse(await env.CODES.get("admin:log") || "[]"); } catch (_) {}
      return json(200, { log: list.reverse() });
    }

    if (url.pathname === "/api/health") {
      const checks = [
        ["Origin8", "https://origin8.litehouseiq.com/"], ["DocLens", "https://doclens.litehouseiq.com/"],
        ["Income360", "https://psl.litehouseco.com/"], ["TwinSim", "https://twinsim.litehouseiq.com/"],
        ["Jugaad", "https://jugaad.litehouseiq.com/"], ["MyVault", "https://myvault.litehouseiq.com/"],
        ["Ikigai", "https://litehouseiq.com/"], ["Website", "https://litehouseco.com/"]];
      const results = await Promise.all(checks.map(async ([n, u]) => {
        const t0 = Date.now();
        try { const r = await fetch(u, { redirect: "manual", signal: AbortSignal.timeout(15000) }); return { name: n, url: u, status: r.status, ms: Date.now() - t0 }; }
        catch (e) { return { name: n, url: u, status: 0, ms: Date.now() - t0, error: String(e.message || e) }; }
      }));
      return json(200, { results });
    }

    if (url.pathname.startsWith("/api/ikigai/")) {
      const path = url.pathname.slice("/api/ikigai/".length);
      const allowed = ["institutions", "institutions/contract", "workspace/view", "requests", "requests/close", "incidents", "incidents/update", "retention/run"];
      if (!allowed.includes(path)) return json(404, { error: "Not found" });
      let body = null; if (request.method === "POST") { try { body = await request.json(); } catch (_) { body = {}; } }
      const r = await ikigai(env, path, request.method, body);
      if (request.method === "POST") await audit(env, { action: "ikigai_" + path.replace("/", "_"), by: email });
      return json(r.status, r.data);
    }

    return json(404, { error: "Not found" });
  },

  // Keeps DocLens awake: its host sleeps after a period without visits.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(fetch("https://doclens.litehouseiq.com/", { headers: { "user-agent": "litehouse-keepwarm" } }).catch(() => {}));
  },
};
