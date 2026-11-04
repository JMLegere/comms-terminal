// Phase 5: Calendar, Cases and Tasks as the Console's (AS-CONF-14..16), run in the shipped views.
// Usage: node --experimental-vm-modules app_phase5_view.mjs join|fold|reply|task-stale|task-enter|undo|grid
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
  const c = new Case();
  c.init({}, cx); tasks.length = 0;                       // init makes the card's Composer; its loops never end
  Object.assign(c, { sel: 0, toggled: new Set(), opened: {},
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
    out.reply = c.composer.reply?.thread; out.text = c.composer.body.value(); out.to = c.composer.to.value(); out.unfolded = !c.toggled.has("t1");
  }
} else if (kase === "task-stale") { // a Task's Draft never takes the pane from the row Jeremy selected
  const Today = await load("today"), Task = await load("task");
  state.page = "today";
  const today = make(Today, { data: null, cases: {}, at: 0 });
  answers["today.page"] = { threads: [], tasks: [{ id: "k1", kind: "email", person: "Ana", group: "overdue", day: 1 }],
    drafts: [{ id: "d-reply", thread_id: "t-reply", subject: "Re: Introduction" }], paused: [], unattended: [] };
  today.load(cx); await settle();                         // arrives on the Task
  answers["task.pane"] = { draft: { id: "d-task", thread_id: null } };
  const task = make(Task, { pane: null, context: [] });
  task.loadContext = () => {};
  task.load(cx, true); await settle();                    // task.show: its New Thread Draft takes the pane
  out.shown = [state.draft, state.detail];
  today.move(1, cx);                                      // then the Reply Draft row below it
  out.selected = [state.draft, state.detail, state.task ?? null];
  task.load(cx); await settle();                          // a change arrives: the Task view reloads
  out.after = [state.draft, state.detail];
} else if (kase === "task-enter") { // FR-UD-6: a Reply Draft in place, no page change
  const Task = await load("task");
  make(Task, { pane: { draft: { id: "d1", thread_id: "t9" } } }).open(cx);
  await settle();
  out.reply = ran.splice(0); out.thread = state.thread; out.detail = state.detail; out.focus = state.focus;
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
