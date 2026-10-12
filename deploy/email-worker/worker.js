// Cloudflare Email Worker for Universal Inbox (D3, decision #102).
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

export default {
  async email(message, env) {
    const raw = new Uint8Array(await new Response(message.raw).arrayBuffer());
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const to = (message.to || "").toLowerCase();
    const response = await fetch(env.HOOK_URL, {
      method: "POST",
      headers: {
        "Content-Type": "message/rfc822",
        "X-Inbox-Timestamp": timestamp,
        "X-Inbox-To": to,
        "X-Inbox-Signature": await sign(env.EMAIL_HOOK_SECRET, timestamp, to, raw),
      },
      body: raw,
    });
    // ponytail: a failed hook bounces the forwarded copy (Gmail still has the original); a retry queue
    // would keep it, add one if bounces show up in practice.
    if (!response.ok) message.setReject(`universal-inbox hook returned HTTP ${response.status}`);
  },
};
