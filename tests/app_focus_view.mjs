// Element focus (PRD 2026-11-03-app-element-focus, Phase 1), run in the shipped views.
// Usage: node --experimental-vm-modules app_focus_view.mjs
import assert from "node:assert/strict";
import { cx, load, ran, state, toasts } from "./app_view_kit.mjs";

const make = (View, fields) => Object.assign(Object.create(View.prototype), fields);
const out = {};
const Today = await load("today"), Thread = await load("thread"), Nav = await load("nav");

// AS-EF-6: a key the focused row does not take says what that row takes.
const today = make(Today, { scrolls: {}, data: {
  threads: [{ id: "thread-1", who: "Ana", title: "Hello", detail: "NEW_REPLY" }],
  paused: [{ id: "enrollment-1", person: "Cy", person_id: "person-1" }] } });
today.at = today.rows().findIndex((x) => x.kind === "paused");
today.select(cx);
today.command("e", cx);
out.dead = toasts.splice(0);
out.paused_target = state.target;

// AS-EF-7: the focused Thread row names itself and its keys in the bar.
today.at = today.rows().findIndex((x) => x.kind === "threads" && x.r);
state.focus = "list";
today.select(cx);
out.row_target = state.target?.label;
ran.splice(0);
today.command("e", cx); // AS-EF-5: from the row, e goes to its Thread
out.row_e = ran.splice(0).filter((c) => c.startsWith("thread."));

// AS-EF-4/5: in the Thread, the newest message takes the focus; j/k walk the messages; e still reaches the Thread.
const msgs = [{ id: 1, native_id: "a", who: "Ana", at: "2026-11-01T10:00:00Z" }, { id: 2, native_id: "b", who: "Me", at: "2026-11-02T10:00:00Z" }];
state.thread = "thread-1";
const thread = make(Thread, { bodies: {}, view: { thread: { f: { id: "thread-1", status_detail: "NEW_REPLY" } }, messages: msgs } });
thread.command("focus_entry", cx);
out.entry = thread.msgState().mi;
thread.command("prev", cx);
out.prev = [thread.msgState().mi, state.target?.label];

// AS-EF-4/8: columns. → from the list enters the detail column's view; esc steps back a column, then a page.
state.list_view = "today"; state.detail_view = "thread"; state.focus = "list"; state.stack = [{ page: "today" }];
const nav = make(Nav, { summary: {}, shell: {} });
nav.command("focus_right", cx);
out.right = [state.focus, ran.splice(0)];
nav.command("down", cx);
out.down = ran.splice(0);
nav.command("back", cx);
out.back = [state.focus, ran.splice(0)];
nav.command("focus_left", cx);
out.rail = [state.focus, state.target?.col];
nav.command("back", cx);
out.rail_back = state.focus;
console.log(JSON.stringify(out));
