// litehouse-gate: one Worker in front of the Litehouse DeepTech demo sites.
// Every host below sits behind the access gate. Each site has its own six-digit code,
// read from the CODES store that the admin panel at admin.litehouseiq.com edits.
// Origin8 and MyVault are Python services on Railway. The gate signs every request it
// forwards (ECDSA P-256, key pair generated and kept by the gate itself), and those
// services check the signature against the public key at /__lh/pubkey, so nobody can
// reach them around the gate. No shared secret has to be typed in anywhere.
import { gate } from "./gate.js";

const SITES = {
  "origin8.litehouseiq.com": { site: "origin8",   title: "Origin8",   kind: "proxy", originEnv: "ORIGIN8_ORIGIN", signed: true },
  "jugaad.litehouseiq.com":  { site: "jugaad",    title: "Jugaad",    kind: "proxy", origin: "https://jugaad-7ro.pages.dev" },
  "myvault.litehouseiq.com": { site: "myvault",   title: "MyVault",   kind: "proxy", originEnv: "MYVAULT_ORIGIN", signed: true },
  "psl.litehouseco.com":     { site: "income360", title: "Income360", kind: "proxy", originEnv: "INCOME360_ORIGIN" },
};

// ---------- request signing ----------
let keyCache = null;
async function gateKeys(env) {
  if (keyCache) return keyCache;
  let stored = null;
  try { stored = JSON.parse(await env.CODES.get("gate:keypair") || "null"); } catch (_) {}
  if (!stored) {
    const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
    stored = { sk: await crypto.subtle.exportKey("jwk", kp.privateKey), pk: await crypto.subtle.exportKey("jwk", kp.publicKey) };
    // first writer wins; re-read so concurrent first requests agree on one key
    const again = await env.CODES.get("gate:keypair");
    if (again) stored = JSON.parse(again); else await env.CODES.put("gate:keypair", JSON.stringify(stored));
  }
  const sk = await crypto.subtle.importKey("jwk", stored.sk, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  keyCache = { sk, pk: { kty: "EC", crv: "P-256", x: stored.pk.x, y: stored.pk.y } };
  return keyCache;
}
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
async function sign(env, method, host, pathAndQuery) {
  const { sk } = await gateKeys(env);
  const ts = String(Math.floor(Date.now() / 1000));
  const msg = `${ts}\n${method}\n${host}\n${pathAndQuery}`;
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, sk, new TextEncoder().encode(msg));
  return { ts, sig: hex(sig) };
}

async function proxy(request, env, cfg) {
  const url = new URL(request.url);
  const originBase = cfg.origin || env[cfg.originEnv];
  if (!originBase || !/^https?:\/\//.test(originBase)) return new Response("This demonstration is being set up. Please try again shortly.", { status: 503 });
  const target = new URL(url.pathname + url.search, originBase);
  const headers = new Headers(request.headers);
  headers.delete("host");
  // keep our own gate cookie away from the application behind it
  const ck = (headers.get("cookie") || "").split(/;\s*/).filter(c => c && !c.startsWith("lh_")).join("; ");
  if (ck) headers.set("cookie", ck); else headers.delete("cookie");
  headers.set("x-forwarded-host", url.host);
  if (cfg.signed) {
    const { ts, sig } = await sign(env, request.method, url.host, url.pathname + url.search);
    headers.set("x-lh-ts", ts); headers.set("x-lh-sig", sig); headers.set("x-lh-host", url.host);
  }
  const init = { method: request.method, headers, redirect: "manual" };
  if (!["GET", "HEAD"].includes(request.method)) init.body = request.body;
  const res = await fetch(target.toString(), init);
  const out = new Response(res.body, res);
  // rewrite redirects that point at the origin back to the public host
  const loc = out.headers.get("location");
  if (loc) { try { const l = new URL(loc, target); if (l.host === target.host) { l.protocol = "https:"; l.host = url.host; out.headers.set("location", l.toString()); } } catch (_) {} }
  out.headers.set("x-robots-tag", "noindex");
  return out;
}

export default {
  async fetch(request, env) {
    const host = new URL(request.url).hostname.toLowerCase();
    const cfg = SITES[host] || (env.TEST_HOST && SITES[env.TEST_HOST]);
    if (!cfg) return new Response("Not found", { status: 404 });
    if (new URL(request.url).pathname === "/__lh/pubkey") {
      const { pk } = await gateKeys(env);
      return new Response(JSON.stringify(pk), { headers: { "content-type": "application/json", "cache-control": "public, max-age=300" } });
    }
    if (new URL(request.url).pathname === "/robots.txt") return new Response("User-agent: *\nDisallow: /\n", { headers: { "content-type": "text/plain" } });
    const blocked = await gate(request, env, cfg.site, cfg.title);
    if (blocked) return blocked;
    return proxy(request, env, cfg);
  },
};
