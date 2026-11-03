// Phase 4: Drafts and Today as the Console's (AS-CONF-12, AS-CONF-13, AS-APP-11 replies), run in the shipped views.
// Usage: node --experimental-vm-modules app_phase4_view.mjs order|d69|d75|keys|unattended|reply:<To|Body|>
import { answers, calls, cx, input, load, ran, settle, state, tasks, toasts } from "./app_view_kit.mjs";

const [kase, arg] = (process.argv[2] ?? "").split(":");
const out = {};
const make = (View, fields) => Object.assign(Object.create(View.prototype), fields);

if (kase === "order") {
  const { ordered, status } = (await load("drafts")).ns;
  const rows = [{ id: "later", send_at: "2026-11-06T15:00:00" }, { id: "unsent" }, { id: "soon", send_at: "2026-11-03T15:00:00" },
    { id: "edit", needs_edit: 1 }, { id: "failed", send_at: "2026-11-01T15:00:00", send_error: "refused" }];
  out.order = ordered(rows).map((d) => d.id);
  out.words = ordered(rows).map((d) => status(d)[0]);
} else if (kase === "d69") {
  const Drafts = await load("drafts"), Thread = await load("thread"), Draft = await load("draft");
  answers["thread.view"] = { view: { thread: { f: { id: "thread-9", status: "SNOOZED", status_detail: "WAITING_FOR_CUSTOMER" } } } };
  state.draft = "draft-9";
  make(Drafts, { rows: [{ id: "draft-9", thread_id: "thread-9" }] }).command("enter", cx);
  await settle();
  out.ran = ran.splice(0);
  out.thread = state.thread;
  make(Thread, { view: { thread: { f: { id: "thread-9" } } }, loaded: "thread-9", open(mode) { out.opened = mode; } }).command("open_reply", cx);
  const pane = make(Draft, { draft: { id: "draft-9", thread_id: "thread-9", body: "Hi" }, info: {} });
  const before = calls.length;
  pane.command("send", cx); pane.command("save", cx);
  await settle();
  out.pane_calls = calls.slice(before);
} else if (kase === "d75") {
  const Draft = await load("draft");
  const field = (v) => { const f = input(); f.set_value(v); return f; };
  const d = make(Draft, { to: field("ana@example.test"), toSlack: field(""), cc: field(""), subject: field(""), body: field("Hi"),
    info: { connection: "Gmail · me@example.test", sender: "me@example.test" }, unsaved: false,
    draft: { id: "d1", revision: 1, thread_id: null, connection_id: "c1", kind: "email", sender: "me@example.test",
      to: "ana@example.test", cc: "", subject: "", body: "Hi", sending_at: null } });
  answers["draft.problem"] = "Add a subject";
  d.command("send", cx);
  await settle();
  out.said = d.said; out.picker = state.picker ?? null;
  d.subject.set_value("Hello");
  answers["draft.problem"] = null;
  d.command("send", cx);
  await settle();
  out.menu = { title: state.picker.title, body: state.picker.body };
} else if (kase === "keys") {
  const Today = await load("today");
  const today = make(Today, { data: { threads: [{ id: "t1", detail: "NEW_REPLY", who: "Ana" }],
    tasks: [{ id: "k1", group: "overdue", person: "Ben" }], drafts: [{ id: "d1" }], paused: [{ id: "e1", person_id: "p1" }], unattended: [] } });
  const at = (k) => today.rows().findIndex((x) => x.kind === k && x.r);
  const press = (key) => Object.fromEntries(["threads", "tasks", "drafts", "paused"].map((k) => {
    today.at = at(k); today.select(cx); ran.length = 0;
    today.command(key, cx);
    return [k, ran.filter((c) => !c.endsWith(".show"))];
  }));
  out.x = press("x"); out.r = press("r");
  today.at = at("threads"); today.command("tab", cx);
  out.tab = today.rows()[today.at].kind;
} else if (kase === "unattended") {
  const { stagesOf, wake } = (await load("today")).ns;
  out.stages = stagesOf({ stages: { revenue: "Proposal", talent: "Intro" } });
  out.wake = `Next wake · ${wake("2026-11-02T09:00:00")}`;
} else if (kase === "reply") { // the reply box: a Reply Draft sends here (D69)
  const Thread = await load("thread");
  const field = (v) => { const f = input(); f.set_value(v); return f; };
  const t = make(Thread, { reply: { thread: "thread-1", envelope: { email: true, later: { tomorrow: "2026-11-04T07:30:00", monday: "2026-11-09T07:30:00" } } },
    text: field(arg === "Body" ? "  " : "Edited text"), to: field(arg === "To" ? "  " : " ana@example.test, ben@example.test "),
    cc: field(" copy@example.test "), replyDraft: null });
  t.send(cx);
  if (state.picker) t.sendPicked("send", cx);
  await settle();
  Object.assign(out, { calls, toasts });
}
tasks.length = 0;
console.log(JSON.stringify(out));
