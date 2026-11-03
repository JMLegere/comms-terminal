// Phase 3: the Threads list as the Console's (AS-CONF-7, AS-CONF-8, D28, D32), run in the shipped view.
// Usage: node --experimental-vm-modules app_phase3_view.mjs groups|case|advance|today-advance|ages
import { answers, cx, load, ran, settle, state, tasks } from "./app_view_kit.mjs";

const Threads = await load("threads");
const view = new Threads();
view.init({}, cx);
tasks.length = 0; // init's event loops never end
const row = (id, detail, extra = {}) => ({ id, detail, status: "TODO", who: id, where: "email", title: `t ${id}`,
  last_at: "2026-11-02T10:00:00Z", waiting_since: "2026-11-01", case_id: null, drafted: 0, ...extra });
const out = {};
answers["cases.list"] = [{ id: "case-1", title: "Acme rollout", counts: { Todo: 2, Snoozed: 0, Done: 1, Ignored: 0 } }];
const keyOf = (it) => it.row ? `row:${it.row.id}` : it.caseHeader ? `case:${it.caseHeader.title || "No case"}:${it.caseHeader.summary}` : `status:${it.statusHeader.label}`;
const show = async (rows) => { answers["threads.queue"] = rows; view.load(cx); await settle(); return view.items.map(keyOf); };

if (process.argv[2] === "groups") {
  state.view = "Todo";
  out.items = await show([
    row("a", "NEW_REPLY", { drafted: 1 }),                 // a saved reply that is not ready: Needs next response, pencil
    row("b", "REPLY_DRAFT", { drafted: 1 }),              // a ready Reply Draft
    row("c", "CREATED", { case_id: "case-1" }),
    row("d", "CREATED"),
  ]);
} else if (process.argv[2] === "case") {
  state.view = "Todo";
  await show([row("c", "CREATED", { case_id: "case-1" }), row("d", "CREATED")]);
  state.page = "threads";
  view.pick(view.items.map((it) => it.caseHeader ? `case-${it.caseHeader.under ?? ""}-${it.caseHeader.id}` : null).find((k) => k?.endsWith("-case-1")));
  view.command("enter", cx);
  out.push = state.push; out.ran = ran.filter((c) => c.startsWith("nav."));
} else if (process.argv[2] === "advance") {
  state.view = "Todo"; state.page = "threads";
  await show([row("a", "CREATED"), row("b", "CREATED"), row("c", "CREATED")]);
  view.pick("row-a"); state.thread = "a";
  state.advance = { thread: "a", at: Date.now() };         // s on a (thread/main.js): the cursor moves on
  out.items = await show([row("b", "CREATED"), row("c", "CREATED"), row("a", "IN_PROGRESS")]);
  out.thread = state.thread;
} else if (process.argv[2] === "today-advance") { // D32 on Today: s moves on, as after Done
  const Today = await load("today");
  const today = Object.assign(Object.create(Today.prototype), { data: null, cases: {}, at: 0 });
  state.page = "today";
  const page = (threads) => ({ threads, tasks: [], drafts: [], paused: [], unattended: [] });
  answers["today.page"] = page([row("a", "CREATED"), row("b", "CREATED"), row("c", "CREATED")]);
  today.load(cx); await settle();
  out.first = state.thread;
  state.advance = { thread: "a", at: Date.now() };        // s on a (thread/main.js): the cursor moves on
  answers["today.page"] = page([row("b", "CREATED"), row("c", "CREATED"), row("a", "IN_PROGRESS")]);
  today.load(cx); await settle();
  out.thread = state.thread;
  const Thread = await load("thread"); // the hand-off is set before the write starts, so no reload can miss it
  const t = Object.assign(Object.create(Thread.prototype), { bodies: {}, view: { thread: { f: { id: "c", status_detail: "CREATED" } } } });
  state.thread = "c"; state.advance = null;
  t.command(`status-picked:${JSON.stringify({ value: "IN_PROGRESS" })}`, cx);
  out.handoff = state.advance?.thread ?? null;
  await settle();
} else { // D27, D28: the Console's when() and left()
  const { when, left } = Threads.ns, now = Date.parse("2026-11-03T12:00:00Z"), at = (secs) => new Date(now - secs * 1000).toISOString();
  out.when = [when(at(30), now), when(at(119), now), when(at(3 * 3600 + 59), now), when(at(2 * 86400 + 5), now), when("2026-10-12T12:00:00", now)];
  out.left = [left(new Date(now + 5 * 60e3 + 24e3).toISOString(), now), left(new Date(now + 3 * 3600e3 + 5e3).toISOString(), now),
    left(new Date(now - 1e3).toISOString(), now), left(null, now)];
}
console.log(JSON.stringify(out));
