// Runs deploy/email-worker/worker.js under Node against a live hook: node harness.mjs <hook-url> <secret> <eml> [to]
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import worker from "../deploy/email-worker/worker.js";

const context = () => {
  const pending = [];
  return { pending, waitUntil: (promise) => pending.push(promise) };
};

async function email(env, raw, to = "jeremy@inbox.agentmaple.com") {
  const ctx = context();
  let rejected = null;
  await worker.email({
    to, raw: new Blob([raw]).stream(), setReject: (reason) => { rejected = reason; },
  }, env, ctx);
  return { rejected, ctx };
}

async function promptly(promise) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("healthy delivery waited for the other host")), 1000);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

async function checks() {
  const source = "https://source.test/hooks/email";
  const mirror = "https://mirror.test/hooks/email";
  const secret = "test-only-email-secret";
  const env = { HOOK_URL: source, MIRROR_HOOK_URL: mirror, EMAIL_HOOK_SECRET: secret };
  const raw = Buffer.concat([Buffer.from("Subject: raw\r\n\r\n"), Buffer.from([0, 255, 128, 13, 10])]);
  const calls = [], logs = [], timeouts = [];
  const originalFetch = globalThis.fetch, originalWarn = console.warn, originalTimeout = AbortSignal.timeout;
  let reply;
  const reset = (handler) => { calls.length = 0; reply = handler; };
  globalThis.fetch = async (url, options) => {
    assert.equal(options.method, "POST");
    assert.equal(options.redirect, "manual");
    assert.ok(options.signal instanceof AbortSignal);
    const call = { url: new URL(url), headers: new Headers(options.headers), body: Buffer.from(options.body) };
    assert.equal(call.headers.has("host"), false);
    calls.push(call);
    return reply(call, options);
  };
  console.warn = (...entry) => logs.push(entry);
  AbortSignal.timeout = (milliseconds) => {
    timeouts.push(milliseconds);
    return originalTimeout(milliseconds);
  };
  const http = async (path = "/hooks/slack", method = "POST", headers = {}, body = raw, config = env) => {
    const ctx = context();
    const request = new Request("https://public.test" + path, {
      method, headers, ...(method === "POST" ? { body } : {}),
    });
    return { response: await worker.fetch(request, config, ctx), ctx };
  };
  try {
    reset(() => new Response(null, { status: 202 }));
    const delivered = await email(env, raw, "FASTMAIL@INBOX.AGENTMAPLE.COM");
    await Promise.all(delivered.ctx.pending);
    assert.equal(delivered.rejected, null);
    assert.deepEqual(calls.map((call) => call.url.href), [source, mirror]);
    assert.deepEqual([...calls[0].headers], [...calls[1].headers]);
    for (const call of calls) {
      assert.deepEqual(call.body, raw);
      assert.equal(call.headers.get("content-type"), "message/rfc822");
      assert.equal(call.headers.get("x-inbox-to"), "fastmail@inbox.agentmaple.com");
      const prefix = `${call.headers.get("x-inbox-timestamp")}.fastmail@inbox.agentmaple.com.`;
      const signature = createHmac("sha256", secret).update(prefix).update(raw).digest("hex");
      assert.equal(call.headers.get("x-inbox-signature"), "sha256=" + signature);
    }

    // Either destination can fail at HTTP or network level without rejecting a healthy copy.
    for (const failed of ["source.test", "mirror.test"]) {
      for (const network of [false, true]) {
        reset((call) => {
          if (call.url.hostname !== failed) return new Response(null, { status: 204 });
          if (network) throw new Error("private network diagnostic");
          return new Response("private failure body", { status: 503 });
        });
        const result = await email(env, raw);
        assert.equal(result.rejected, null);
        await Promise.all(result.ctx.pending);
        assert.equal(calls.length, 2);
      }
    }

    // A never-resolving peer must not hold up acknowledgment; waitUntil retains it.
    for (const stalled of ["source.test", "mirror.test"]) {
      let finish, settled = false;
      const hanging = new Promise((resolve) => { finish = resolve; });
      reset((call) => call.url.hostname === stalled ? hanging : new Response(null, { status: 202 }));
      const result = await promptly(stalled === "source.test" ? http() : email(env, raw));
      assert.equal(result.response?.status ?? (result.rejected === null ? 202 : 500), 202);
      assert.equal(calls.length, 2);
      assert.equal(result.ctx.pending.length, 1);
      result.ctx.pending[0].then(() => { settled = true; });
      await Promise.resolve();
      assert.equal(settled, false);
      finish(new Response(null, { status: 503 }));
      const outcomes = await result.ctx.pending[0];
      assert.equal(outcomes.filter((outcome) => outcome.status === "rejected").length, 1);
    }

    for (const config of [{ ...env, MIRROR_HOOK_URL: source }, { HOOK_URL: source, EMAIL_HOOK_SECRET: secret }]) {
      reset(() => new Response(null, { status: 204 }));
      assert.equal((await email(config, raw)).rejected, null);
      assert.equal(calls.length, 1);
    }
    reset((call) => new Response(null, { status: call.url.hostname === "source.test" ? 401 : 503 }));
    assert.equal((await email(env, raw)).rejected, "universal-inbox hook returned HTTP 401");
    const networkError = new Error("private source network diagnostic");
    reset(() => { throw networkError; });
    await assert.rejects(email({ HOOK_URL: source, EMAIL_HOOK_SECRET: secret }, raw), (error) => error === networkError);
    reset((call) => { throw call.url.hostname === "source.test" ? networkError : new Error("private mirror diagnostic"); });
    await assert.rejects(email(env, raw), (error) => error === networkError);
    assert.equal((await http()).response.status, 502);
    reset((call) => {
      if (call.url.hostname === "source.test") throw networkError;
      return new Response(null, { status: 503 });
    });
    await assert.rejects(email(env, raw), (error) => error === networkError);
    assert.equal((await http()).response.status, 502);

    const challengeBody = Buffer.from('{\n "type": "url_verification", "challenge": "héllo\\r\\n" }\n');
    const challenge = Buffer.from("héllo\r\n"), timestamp = "1792152000", slackSecret = "test-only-slack-secret";
    const slackSignature = "v0=" + createHmac("sha256", slackSecret)
      .update(`v0:${timestamp}:`).update(challengeBody).digest("hex");
    const incoming = {
      Host: "original.test", "Content-Type": "application/json; charset=utf-8",
      "Content-Length": String(challengeBody.length), Connection: "keep-alive",
      "X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": slackSignature,
      Authorization: "Bearer test-only", "X-Custom": "unchanged",
    };
    const forwarded = new Headers(incoming);
    for (const name of ["host", "content-length", "connection"]) forwarded.delete(name);
    reset((call) => {
      assert.deepEqual(call.body, challengeBody);
      assert.deepEqual([...call.headers], [...forwarded]);
      assert.equal(call.headers.has("x-inbox-signature"), false);
      assert.equal("v0=" + createHmac("sha256", slackSecret)
        .update(`v0:${call.headers.get("x-slack-request-timestamp")}:`).update(call.body).digest("hex"), slackSignature);
      return new Response(challenge, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
    });
    const verified = await http("/hooks/slack?next=https://untrusted.test", "POST", incoming, challengeBody);
    assert.deepEqual((await verified.ctx.pending[0]).map((outcome) => outcome.status), ["fulfilled", "fulfilled"]);
    assert.equal(verified.response.status, 200);
    assert.equal(verified.response.headers.get("content-type"), "text/plain; charset=utf-8");
    assert.deepEqual(Buffer.from(await verified.response.arrayBuffer()), challenge);
    assert.deepEqual(calls.map((call) => call.url.href), [
      "https://source.test/hooks/slack?next=https://untrusted.test",
      "https://mirror.test/hooks/slack?next=https://untrusted.test",
    ]);

    const svixID = "msg_test_only", svixKey = Buffer.from("test-only-svix-secret");
    const svixSignature = "v1," + createHmac("sha256", svixKey)
      .update(`${svixID}.${timestamp}.`).update(raw).digest("base64");
    const signed = { "X-Inbox-Signature": "sha256=upstream", "X-Inbox-To": "UPSTREAM@EXAMPLE.COM",
      "X-Inbox-Timestamp": timestamp, "webhook-id": "event-1", "webhook-timestamp": timestamp,
      "webhook-signature": "v1,upstream", "Content-Type": "application/octet-stream",
      "svix-id": svixID, "svix-timestamp": timestamp, "svix-signature": svixSignature };
    for (const path of ["/hooks/email", "/hooks/quo", "/hooks/resend"]) {
      reset((call) => {
        assert.deepEqual(call.body, raw);
        assert.deepEqual([...call.headers], [...new Headers(signed)]);
        return new Response(null, { status: 204 });
      });
      const result = await http(path, "POST", signed);
      assert.equal(result.response.status, 204);
      assert.deepEqual((await result.ctx.pending[0]).map((outcome) => outcome.status), ["fulfilled", "fulfilled"]);
      assert.deepEqual(calls.map((call) => call.url.href), ["https://source.test" + path, "https://mirror.test" + path]);
    }
    reset(() => new Response(null, { status: 204 }));
    await http("/hooks/quo", "POST", {}, raw, { ...env, MIRROR_HOOK_URL: "https://source.test/other" });
    assert.equal(calls.length, 1);

    const sourceFailure = new Response("source refusal\r\n", { status: 401, headers: { "Content-Type": "text/custom" } });
    reset((call) => call.url.hostname === "source.test" ? sourceFailure : new Response(null, { status: 503 }));
    const refused = (await http()).response;
    assert.equal(refused, sourceFailure);
    assert.equal(refused.status, 401);
    assert.equal(refused.headers.get("content-type"), "text/custom");
    assert.equal(await refused.text(), "source refusal\r\n");
    for (const [path, method, status] of [
      ["/hooks/slack", "GET", 405], ["/hooks/email", "PUT", 405], ["/hooks/quo", "HEAD", 405],
      ["/hooks/resend", "GET", 405], ["/hooks/resend/extra", "POST", 404],
      ["/hooks/send", "POST", 404], ["/hooks/slack/extra", "POST", 404], ["/context", "POST", 404],
    ]) {
      reset(() => assert.fail("rejected route was forwarded"));
      const result = await http(path, method);
      assert.equal(result.response.status, status);
      if (status === 405) assert.equal(result.response.headers.get("allow"), "POST");
      assert.equal(calls.length, 0);
      assert.equal(result.ctx.pending.length, 0);
    }
    assert.ok(timeouts.length > 0 && timeouts.every((milliseconds) => milliseconds > 0 && milliseconds <= 25000));
    assert.ok(logs.length > 0);
    for (const [host, status, ...extra] of logs) {
      assert.ok(["source.test", "mirror.test"].includes(host));
      assert.ok(Number.isInteger(status) || status === "network-failure");
      assert.deepEqual(extra, []);
    }
  } finally {
    globalThis.fetch = originalFetch;
    console.warn = originalWarn;
    AbortSignal.timeout = originalTimeout;
  }
}

const [hookUrl, secret, emlPath, to = "jeremy@inbox.agentmaple.com"] = process.argv.slice(2);
if (hookUrl === "--check") {
  await checks();
  console.log("checked");
} else {
  const result = await email({ HOOK_URL: hookUrl, EMAIL_HOOK_SECRET: secret }, readFileSync(emlPath), to);
  await Promise.all(result.ctx.pending);
  console.log(result.rejected === null ? "accepted" : `rejected: ${result.rejected}`);
}
