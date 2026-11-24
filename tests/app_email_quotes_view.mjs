// App acceptance scenario (features/app.feature), using the shipped Thread view.
import assert from "node:assert/strict";
import { answers, calls, clicks, cx, load, mountedTexts, settle, state } from "./app_view_kit.mjs";

const Thread = await load("thread");
state.thread = "test-thread";
const thread = Object.assign(new Thread(), { bodies: {} });
const m = { id: 1, native_id: "reply", raw: "fixture", shown: "Fallback" };
const toggle = " ••• ";
const replyHtml = "<p>New answer.</p><blockquote>Earlier history.</blockquote>";
const collapsed = { texts: ["New answer.", toggle], html: [] };
const visible = () => {
  clicks.length = 0;
  const html = [];
  return { texts: mountedTexts(() => thread.content(m, {}), { messageBodies: html }), html };
};
answers["message.body"] = { compact: "New answer.", html: replyHtml, remote: 1 };
thread.body(m, false, cx);
await settle();
assert.deepEqual(visible(), collapsed, "initial render must not mount a MessageBody");
clicks[0]({}, cx);
assert.deepEqual(visible(), {
  texts: [toggle, "1 remote image held back", "Load images"],
  html: [replyHtml],
}, "expanded render must mount the full original HTML");
clicks[0]({}, cx);
assert.deepEqual(visible(), collapsed, "second click must unmount the MessageBody");

thread.view = { messages: [m] };
answers["message.load_images"] = { ...answers["message.body"], remote: 0 };
thread.command("images", cx);
await settle();
assert.equal(calls.at(-1).method, "message.load_images");
assert.deepEqual(visible(), { texts: [toggle], html: [replyHtml] });
clicks[0]({}, cx);
assert.deepEqual(visible(), collapsed);

answers["message.body"] = { html: "<p>Newsletter</p>", remote: 1 };
thread.body(m, false, cx);
await settle();
assert.deepEqual(visible().texts, ["1 remote image held back", "Load images"]);

m.trimmed = "Earlier history.";
answers["message.body"] = null;
thread.body(m, false, cx);
await settle();
assert.deepEqual(visible().texts, ["Fallback", toggle]);
clicks[0]({}, cx);
assert.deepEqual(visible().texts, ["Fallback", toggle, "Earlier history."]);
