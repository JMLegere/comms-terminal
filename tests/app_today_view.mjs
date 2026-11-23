// D87: on Today, a Thread key acts only on a selected Thread row.
// Usage: node --experimental-vm-modules app_today_view.mjs <row kind> <thread command>
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { calls, cx, load, mountedTexts, ran, settle, state } from "./app_view_kit.mjs";

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
if (kind === "panes") {
  // AS-PR-13: the kit observes pane rendering and cursor reachability, not native geometry.
  today.data = {
    promises: [
      { id: "a", what: "First Promise", section: "today", due_at: "2026-11-20T21:00:00Z" },
      { id: "b", what: "Second Promise", section: "later", due_at: "2026-11-28T21:00:00Z" },
    ],
    threads: today.data.threads,
    issues: [{ id: "issue-1", identifier: "ENG-1", title: "Review draft plan" }],
    drafts: [],
  };
  const Nav = await load("nav");
  const nav = Object.assign(Object.create(Nav.prototype), { summary: {}, shell: {} });
  const keys = JSON.parse(readFileSync(new URL("../app/defaults/keys.json", import.meta.url), "utf8"));
  const dispatch = (command) => {
    const [view, name] = command.split(".");
    ({ nav, today }[view])?.command(name, cx);
  };
  const press = (key) => {
    // Match the host's detail-over-page priority for this non-typing, non-rail scenario.
    dispatch((state.focus === "detail" ? keys.detail[key] : null) ?? keys[`page:${state.page}`]?.[key] ?? keys.global[key]);
    while (ran.length) dispatch(ran.shift());
  };
  const panes = () => mountedTexts(() => today.render())
    .filter((t) => / (promises|threads|issues|drafts|tasks|paused|unattended) · \d+$/.test(t))
    .map((t) => t.slice(t.indexOf(" ") + 1));
  today.scrolls = {};
  today.at = 0;
  Object.assign(state, { focus: "list", list_view: "today", detail_view: "side", stack: [{ page: "today" }] });
  today.select(cx);
  const before = panes();
  ran.length = 0;
  press("enter");
  const during = panes(), focus = state.focus;
  press("down");
  const detailRow = today.rows()[today.at]?.r?.id ?? null;
  press("escape");
  const focusAfterBack = state.focus;
  const moves = ["down", "down", "down", "tab", "tab"].map((key) => {
    press(key);
    const row = today.rows()[today.at];
    return [row.kind, row.r?.id ?? null];
  });
  console.log(JSON.stringify({ before, during, focus, detailRow, focusAfterBack, moves }));
  process.exit(0);
}

const at = (k) => today.rows().findIndex((x) => x.kind === k && x.r); // the first row of that kind (not a header)
today.at = at("threads");
today.select(cx);                       // a Thread row first, as when Jeremy arrives from it
assert.equal(state.thread, "thread-1");
today.at = at(kind);
today.select(cx);
thread.command(command, cx);
await settle();
console.log(JSON.stringify({ thread: state.thread ?? null, calls, picker: state.picker ?? null }));
