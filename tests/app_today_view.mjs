// D87: on Today, a Thread key acts only on a selected Thread row.
// Usage: node --experimental-vm-modules app_today_view.mjs <row kind> <thread command>
import assert from "node:assert/strict";
import { calls, cx, load, settle, state } from "./app_view_kit.mjs";

const Today = await load("today"), Thread = await load("thread");
const today = Object.create(Today.prototype), thread = Object.create(Thread.prototype);
Object.assign(thread, { bodies: {}, view: { thread: { f: { status_detail: "NEW_REPLY" } }, messages: [] } });
today.data = {
  threads: [{ id: "thread-1", who: "Ana", title: "Hello", detail: "NEW_REPLY" }],
  tasks: [{ id: "task-1", person: "Ben", group: "due today" }], drafts: [{ id: "draft-1", subject: "Note" }],
  paused: [{ id: "enrollment-1", person: "Cy", person_id: "person-1" }],
  unattended: [{ id: "org-1", name: "Acme", domain: "acme.test" }],
};
const [kind, command] = process.argv.slice(2);
const at = (k) => today.rows().findIndex((x) => x.kind === k && x.r); // the first row of that kind (not a header)
today.at = at("threads");
today.select(cx);                       // a Thread row first, as when Jeremy arrives from it
assert.equal(state.thread, "thread-1");
today.at = at(kind);
today.select(cx);
thread.command(command, cx);
await settle();
console.log(JSON.stringify({ thread: state.thread ?? null, calls, picker: state.picker ?? null }));
