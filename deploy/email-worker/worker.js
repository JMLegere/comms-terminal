// Cloudflare Email Worker for Comms Terminal (D3, decision #102).
// Gmail forwards jeremy@agentmaple.com to jeremy@inbox.agentmaple.com; Email Routing hands each message
// here, and this posts the raw RFC 822 bytes to POST /hooks/email, signed the way email.verify() checks:
//   X-Inbox-Timestamp: <unix seconds>
//   X-Inbox-To: <envelope recipient, e.g. fastmail@inbox.agentmaple.com>; it picks the Connection (#113)
//   X-Inbox-Signature: sha256=<hex HMAC-SHA256 over "<timestamp>.<recipient>.<raw message>", key EMAIL_HOOK_SECRET>
// EMAIL_HOOK_SECRET is a Worker secret (wrangler secret put); HOOK_URL is a plain variable (wrangler.toml).

const encoder = new TextEncoder();

export async function sign(secret, timestamp, to, raw) {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  const prefix = encoder.encode(`${timestamp}.${to}.`);
  const data = new Uint8Array(prefix.length + raw.length);
  data.set(prefix);
  data.set(raw, prefix.length);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, data));
  return "sha256=" + Array.from(mac, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function deliver(raw, headers, env, ctx, path) {
  const targets = [...new Set([env.HOOK_URL, env.MIRROR_HOOK_URL].filter(Boolean).map((url) =>
    path ? new URL(path, new URL(url).origin).href : new URL(url).href))];
  headers = new Headers(headers);
  for (const name of ["host", "content-length", "connection", "transfer-encoding", "keep-alive",
    "upgrade", "te", "trailer", "proxy-connection"]) headers.delete(name);
  const deliveries = targets.map(async (url) => {
    const host = new URL(url).hostname;
    const response = await fetch(url, {
      method: "POST", headers, body: raw, redirect: "manual", signal: AbortSignal.timeout(25000),
    }).catch((error) => {
      console.warn(host, "network-failure");
      throw error;
    });
    console.warn(host, response.status);
    if (!response.ok) throw response;
    return response;
  });
  // Keep the slower copy alive without delaying acknowledgment of the first success.
  ctx.waitUntil(Promise.allSettled(deliveries));
  try {
    return await Promise.any(deliveries);
  } catch (error) {
    const failure = error.errors[0];
    if (failure instanceof Response) return failure;
    throw failure;
  }
}

export default {
  async email(message, env, ctx) {
    const raw = new Uint8Array(await new Response(message.raw).arrayBuffer());
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const to = (message.to || "").toLowerCase();
    const response = await deliver(raw, {
      "Content-Type": "message/rfc822",
      "X-Inbox-Timestamp": timestamp,
      "X-Inbox-To": to,
      "X-Inbox-Signature": await sign(env.EMAIL_HOOK_SECRET, timestamp, to, raw),
    }, env, ctx);
    // ponytail: if every hook fails, bounce the forwarded copy (Gmail still has the original); a retry queue
    // would keep it, add one if bounces show up in practice.
    if (!response.ok) message.setReject(`comms-terminal hook returned HTTP ${response.status}`);
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (!["/hooks/email", "/hooks/slack", "/hooks/quo", "/hooks/resend"].includes(url.pathname)) {
      return new Response("Not found", { status: 404 });
    }
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "POST" } });
    }
    const raw = new Uint8Array(await request.arrayBuffer());
    try {
      return await deliver(raw, request.headers, env, ctx, url.pathname + url.search);
    } catch {
      return new Response("Bad gateway", { status: 502 });
    }
  },
};
