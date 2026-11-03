// Phase 5: Calendar, Cases and Tasks as the Console's (AS-CONF-14..16), run in the shipped views.
// Usage: node --experimental-vm-modules app_phase5_view.mjs join|fold|reply|task-enter|undo|grid
import { answers, calls, cx, input, load, ran, settle, state, tasks, toasts } from "./app_view_kit.mjs";

const kase = process.argv[2];
const out = {};
const make = (View, fields) => Object.assign(Object.create(View.prototype), fields);

if (kase === "join") { // D95: o toasts the link; Ctrl+click opens it
  const Day = await load("day"), Event = await load("event");
  state.cal_day = { day: "2026-11-03", items: [{ kind: "event", join_url: "https://meet.example/abc", event: { uid: "u", id: "u" }, line: [] }] };
  make(Day, { at: 0 }).command("join", cx);
  await make(Event, { page: { available: true, join_url: "https://meet.example/xyz" } }).command("join", cx);
  out.toasts = toasts.splice(0);
  out.opened = ran.splice(0);
} else if (kase === "fold" || kase === "reply") {
  const Case = await load("case");
  state.page = "case";
  const field = (v) => { const f = input(); f.set_value(v); return f; };
  const c = make(Case, { sel: 0, toggled: new Set(), opened: {}, text: field(""), to: field(""), cc: field(""),
    c: { id: "case-1", cards: [{ id: "t1", messages: [] }, { id: "t2", messages: [] }], timeline: [] } });
  if (kase === "fold") {
    c.command("next", cx);
    out.thread = state.thread;
    c.command("toggle", cx); out.folded = [...c.toggled];
    c.command("toggle", cx); out.unfolded = [...c.toggled];
  } else {
    answers["reply.envelope"] = { takes_replies: true, email: true, to: ["ana@example.test"], cc: [], later: {} };
    answers["thread.view"] = { reply_draft: { body: "Saved words" } };
    c.toggled.add("t1");
    c.command("reply_all", cx);
    await settle();
    out.reply = c.reply?.thread; out.text = c.text.value(); out.to = c.to.value(); out.unfolded = !c.toggled.has("t1");
  }
} else if (kase === "task-enter") { // D110
  const Task = await load("task");
  answers["thread.view"] = { view: { thread: { f: { status: "TODO", status_detail: "NEW_REPLY" } } } };
  make(Task, { pane: { draft: { id: "d1", thread_id: "t9" } } }).open(cx);
  await settle();
  out.reply = ran.splice(0); out.thread = state.thread;
  make(Task, { pane: { draft: { id: "d2", thread_id: null } } }).open(cx);
  out.fresh = ran.splice(0);
} else if (kase === "undo") { // D111
  const Tasks = await load("tasks");
  const rows = [{ id: "k1", kind: "call", person: "Ben", day: 3, group: "overdue" }, { id: "k2", kind: "call", person: "Cy", day: 1, group: "overdue" }];
  state.task = "k1";
  answers["command.sequences.skip_task"] = { task_id: "k1", restore: true };
  make(Tasks, { rows, filter: null, items: [], scroll: { scroll_to_item() {} } }).command("skip", cx);
  await settle();
  out.undo = state.undo; out.toast = toasts.at(-1); out.task = state.task;
} else if (kase === "grid") { // D91: week rows split by month separators
  const { blocks, dateLine } = (await load("calendar")).ns;
  const b = blocks(new Date(2026, 9, 26), new Date(2026, 10, 9));
  out.rows = b.map((x) => x.sep ? `sep ${x.sep.getMonth() + 1}/${x.sep.getDate()}` : x.week.map((d) => (d ? d.getDate() : "-")).join(","));
  out.lines = [dateLine(new Date(2026, 10, 1), new Date(2026, 10, 3)), dateLine(new Date(2026, 10, 3), new Date(2026, 10, 3))];
}
tasks.length = 0;
console.log(JSON.stringify(out));
