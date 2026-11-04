// Phase 4: Drafts and Today as the Console's (AS-CONF-12, AS-CONF-13, AS-APP-11 replies), run in the shipped views.
// Usage: node --experimental-vm-modules app_phase4_view.mjs order|early|d75|keys|unattended|complete|pin|prompt|rowkeys
import { answers, calls, cx, input, load, ran, settle, state, tasks, toasts } from "./app_view_kit.mjs";

const [kase, arg] = (process.argv[2] ?? "").split(":");
const out = {};
const make = (View, fields) => Object.assign(Object.create(View.prototype), fields);

if (kase === "rowkeys") { // a Case header under two Statuses (here "No case") keeps one key each: a repeated key blanks the list
  const rows = [{ id: "a", detail: "CREATED", case_id: "c1" }, { id: "b", detail: "CREATED" },
    { id: "c", detail: "IN_PROGRESS", case_id: "c1" }, { id: "d", detail: "IN_PROGRESS" }];
  const cases = { c1: { title: "Deal", counts: {} } };
  const today = (await load("today")).ns.paneItems({ threads: rows }, cases).threads.map((it) => it.key);
  const threads = (await load("threads")).ns.grouped("Todo", rows, cases).map((it) => it.row ? `row-${it.row.id}` : it.caseHeader ? `case-${it.caseHeader.under}-${it.caseHeader.id}` : `status-${it.statusHeader.detail}`);
  out.today_dups = today.filter((k, i) => today.indexOf(k) !== i);
  out.threads_dups = threads.filter((k, i) => threads.indexOf(k) !== i);
  out.headers = today.filter((k) => k.includes("-case-")).length;
} else if (kase === "order") {
  const { ordered, status } = (await load("drafts")).ns;
  const rows = [{ id: "later", send_at: "2026-11-06T15:00:00" }, { id: "unsent" }, { id: "soon", send_at: "2026-11-03T15:00:00" },
    { id: "edit", needs_edit: 1 }, { id: "failed", send_at: "2026-11-01T15:00:00", send_error: "refused" }];
  out.order = ordered(rows).map((d) => d.id);
  out.words = ordered(rows).map((d) => status(d)[0]);
} else if (kase === "prompt") { // the Console's Ask: the prompt is the box's placeholder
  const Overlay = await load("overlay");
  const o = new Overlay();
  o.init({}, cx);
  state.picker = { title: "New case title", options: [], reply: "thread.case-named", prompt: "What is this about?" };
  const before = o.input;
  o.open(cx);                                             // overlay.open
  out.placeholder = o.input.placeholder;
  const open = before;
  out.fresh = o.input !== open;
  o.input.set_value("Seed Round");
  o.choose(undefined, cx);
  out.ran = ran.splice(0);
} else if (kase === "complete") { // → takes a To/Cc completion, as a shell's suggestion; Tab does not
  const { Composer } = (await load("thread")).deps["./composer.js"];
  const c = new Composer("thread");
  c.ghost = { cc: ["ana@example.test, ben@example.test", "ana@example.test, ben@example.test"] };
  const cc = input(); cc.set_value("ana@example.test, ben@exa");
  out.tab = c.accept("cc", cc, "tab", cx);
  out.right = c.accept("cc", cc, "right", cx);
  out.value = cc.value();
  out.again = c.accept("cc", cc, "right", cx);            // taken: → moves on as usual
} else if (kase === "pin") { // a Thread opens at its newest entries; Jeremy's own scroll ends that
  const Thread = await load("thread");
  const after = cx.timer.after;
  cx.timer.after = (_ms, f) => f(cx);                    // timers fire at once here
  state.thread = "thread-8";
  answers["thread.view"] = { view: { thread: { f: { id: "thread-8", status: "TODO" } }, messages: [] }, drafts: [] };
  const t = make(Thread, { bodies: {}, view: null, loaded: null });
  t.load(cx); await settle();
  out.opened = ran.filter((c) => c === "app.scroll_end").length;
  ran.length = 0;
  t.command("scroll_up", cx);
  t.pin("thread-8");
  out.after_scroll = ran.filter((c) => c === "app.scroll_end").length;
  ran.length = 0;
  t.load(cx); await settle();                             // a reload of the same Thread keeps Jeremy's place
  out.reload = ran.filter((c) => c === "app.scroll_end").length;
  cx.timer.after = after;
} else if (kase === "early") { // NFR-APP-1: a reply key pressed while the Thread still loads acts once it arrives
  const Thread = await load("thread");
  state.thread = "thread-7";
  answers["thread.view"] = { view: { thread: { f: { id: "thread-7", status: "TODO" } }, messages: [] }, drafts: [] };
  const t = make(Thread, { bodies: {}, view: { thread: { f: { id: "thread-6" } } }, loaded: "thread-6" });
  t.openDraft = (mode, key) => { out.opened = [mode, key]; };
  t.command("reply_sender", cx);
  out.before = out.opened ?? null;
  t.load(cx);
  await settle();
  out.after_load = out.opened ?? null;
  out.opened = undefined; // pressed on another Thread, then moved on before it loaded: nothing opens here
  t.wantReply = "thread-5"; t.wantMode = "all";
  t.load(cx);
  await settle();
  out.after_move = out.opened ?? null;
} else if (kase === "d75") {
  const Draft = await load("draft");
  const d = new Draft();
  d.init({}, cx); tasks.length = 0;
  d.draft = { id: "d1", revision: 1, thread_id: null, connection_id: "c1", kind: "email", sender: "me@example.test",
    to: "ana@example.test", cc: "", subject: "", body: "Hi", sending_at: null };
  d.info = { connection: "Gmail · me@example.test", sender: "me@example.test", draft: d.draft };
  d.composer.showDraft(d.info);
  answers["draft.problem"] = "Add a subject";
  d.command("send", cx);
  await settle();
  out.said = d.composer.said; out.picker = state.picker ?? null;
  d.composer.subject.set_value("Hello");
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
}
tasks.length = 0;
console.log(JSON.stringify(out));
