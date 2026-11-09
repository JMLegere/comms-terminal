// Unified Drafts in the App (.agents/prd/2026-11-03-unified-drafts.md, AS-UD-1, 3, 5–9): the one Composer, the Draft
// review list in place, next after an action and the keep-place rule, run in the shipped views.
// Usage: node --experimental-vm-modules app_composer_view.mjs send:<Reply Draft|New Thread Draft>[:To|Body]
//   | keys | typed-during-save | fields | unknown | inplace | next | today-next | keep | order
import { answers, calls, cx, focused, keyHandlers, load, ran, settle, state, tasks, texts, toasts } from "./app_view_kit.mjs";

const [kase, kind, empty] = (process.argv[2] ?? "").split(":");
const out = {};
const start = (v) => { v.init({}, cx); tasks.length = 0; return v; }; // init's event loops never end
const t = {}; // the theme: tokens are read, never checked
const LATER = { tomorrow: "2026-11-05T07:30:00", monday: "2026-11-09T07:30:00" };
const envelope = (fields = {}) => ({ email: true, takes_replies: true, sends_files: true, from: "me@example.test", where: "ana@example.test",
  to: ["ana@example.test"], cc: [], subject: "Re: Plans", later: LATER, ...fields });
const replyRow = { id: "draft-1", revision: 1, thread_id: "thread-1", kind: "email", to: "ana@example.test", cc: "", body: "Old text",
  sending_at: null, send_at: null };
const newRow = { id: "draft-2", revision: 1, thread_id: null, connection_id: "connection-1", kind: "email", sender: "me@example.test",
  to: "old@example.test", cc: "", subject: "Test", body: "Old text", sending_at: null, send_at: null, needs_edit: 0 };
const info = { connection: "Gmail · me@example.test", sender: "me@example.test", staged: [] };
const press = (key, composer) => { keyHandlers.length = 0; composer.editor(t); keyHandlers[0]({ keystroke: key }, cx); };

/** A Thread view with the Composer open on its Reply Draft, as r opens it. */
async function threadComposer(row = replyRow, env = envelope()) {
  const Thread = await load("thread");
  const view = start(new Thread());
  state.thread = row.thread_id;
  answers["reply.envelope"] = env;
  await view.composer.openReply(row.thread_id, row.thread_id, "all", row, [], true, cx);
  return view;
}

/** A Draft view with the Composer on a New Thread Draft, as draft.show loads it. */
async function draftComposer(row = newRow) {
  const Draft = await load("draft");
  const view = start(new Draft());
  view.draft = row;
  view.info = { ...info, draft: row };
  view.composer.showDraft({ ...info, draft: row });
  return view;
}

const type = (field, value) => { field.set_value(value); field.fire("change", cx); };

if (kase === "send") { // AS-UD-1/3: ctrl+enter saves the typed text as a revision, then sends that revision
  const reply = kind === "Reply Draft";
  const view = reply ? await threadComposer() : await draftComposer();
  const c = view.composer;
  answers["command.inbox.save_reply_draft"] = { ...replyRow, revision: 2, body: "Edited text" };
  answers["command.inbox.save_draft"] = { revision: 2 };
  type(c.body, empty === "Body" ? "  " : "Edited text");
  type(c.to, empty === "To" ? "  " : " ana@example.test, ben@example.test ");
  type(c.cc, " copy@example.test ");
  if (!reply) type(c.subject, "Edited subject");
  calls.length = 0;
  press("ctrl-enter", c);
  await settle();
  if (state.picker?.reply?.endsWith("send-picked")) view.command(`send-picked:${JSON.stringify({ value: "send" })}`, cx);
  await settle();
  Object.assign(out, { calls, toasts, saved: calls.find((x) => x.method === "command.inbox.save_draft")?.params.values ?? null });
} else if (kase === "keys") { // AS-UD-1: a Reply Draft and a New Thread Draft open in the same Composer, with the same keys
  const views = { reply: await threadComposer(), new: await draftComposer() };
  for (const [name, view] of Object.entries(views)) {
    const got = {};
    view.composer.opts.command = (c) => { got.last = c; };
    for (const key of ["ctrl-enter", "ctrl-s", "escape", "ctrl-o"]) {
      got.last = null; ran.length = 0;
      press(key, view.composer);
      got[key] = got.last ?? ran[0] ?? null;
    }
    delete got.last;
    texts.length = 0;
    view.composer.editor(t);
    out[name] = { keys: got, editor: view.composer.constructor.name, fields: texts.filter((x) => ["Conn.", "From", "To", "Cc", "Subject", "Re: Plans"].includes(x) || /reply/.test(x)) };
  }
  // esc keeps the Draft: the reply's text is saved, nothing is deleted.
  const view = views.reply;
  view.composer.opts.command = (c, cx) => view.command(c, cx);
  type(view.composer.body, "Kept words");
  calls.length = 0;
  press("escape", view.composer);
  await settle();
  out.esc = { open: Boolean(view.composer.reply), calls: calls.map((x) => [x.method, x.params.body ?? null]) };
} else if (kase === "fields") { // AS-UD-9: a Slack and an SMS Draft show their destination and no Subject
  const slack = await draftComposer({ ...newRow, kind: "slack", to: "#general <C0123>", subject: null });
  texts.length = 0; slack.composer.editor(t);
  out.slack = { to: slack.composer.toField() === slack.composer.toSlack, labels: texts.filter((x) => ["To", "Cc", "Subject", "Conn."].includes(x)) };
  const sms = await threadComposer({ ...replyRow, kind: "sms", to: "+15550100" }, envelope({ email: false, where: "+1 555 0100", to: [], cc: [] }));
  texts.length = 0; sms.composer.editor(t);
  out.sms = { labels: texts.filter((x) => ["To", "Cc", "Subject", "From"].includes(x)), where: texts.find((x) => x.includes("in thread")) ?? null };
} else if (kase === "unknown") { // AS-UD-3: a lost answer locks the Reply Draft; Send again sends with `again`
  const view = await threadComposer();
  const c = view.composer;
  // A send whose answer was lost: the core answers 504 Send outcome unknown.
  Object.defineProperty(answers, "send.draft", { configurable: true, get: () => Promise.reject(new Error("Send outcome unknown")) });
  view.command(`send-picked:${JSON.stringify({ value: "send" })}`, cx);
  await settle();
  out.said = c.said;
  Object.defineProperty(answers, "send.draft", { configurable: true, writable: true, value: null });
  c.refresh({ ...replyRow, sending_at: "2026-11-04T10:00:00" }, []); // the Thread reloads with the lock
  type(c.body, "typed while locked");
  out.locked = { body: c.body.value(), unsaved: c.unsaved };
  press("ctrl-enter", c);
  await settle();
  out.menu = { title: state.picker.title, options: state.picker.options.map((o) => o.value) };
  calls.length = 0;
  view.command(`unknown-picked:${JSON.stringify({ value: "again" })}`, cx);
  await settle();
  out.again = calls.filter((x) => x.method === "send.draft").map((x) => x.params);
} else if (kase === "inplace") { // AS-UD-6: a Reply Draft shows as its Thread with the Composer open, no page change
  const Drafts = await load("drafts"), Today = await load("today"), Thread = await load("thread");
  state.page = "drafts";
  const drafts = Object.assign(Object.create(Drafts.prototype), { rows: [{ ...replyRow }, { ...newRow }], scroll: { scroll_to_item() {} } });
  ran.length = 0;
  drafts.select("draft-1");
  out.drafts_reply = { page: state.page, detail: state.detail, thread: state.thread, ran: ran.splice(0) };
  drafts.select("draft-2");
  out.drafts_new = { detail: state.detail ?? null, ran: ran.splice(0) };
  state.page = "today";
  const today = Object.assign(Object.create(Today.prototype), { scrolls: {}, at: 0,
    data: { threads: [], tasks: [], drafts: [{ ...replyRow }, { ...newRow }], paused: [], unattended: [] } });
  today.at = today.rows().findIndex((x) => x.r?.id === "draft-1");
  today.select(cx);
  out.today_reply = { page: state.page, detail: state.detail, thread: state.thread, draft: state.draft, ran: ran.splice(0).filter((c) => c.startsWith("thread.")) };
  today.enter(cx);
  out.today_enter = ran.splice(0);
  const Draft = await load("draft");                      // ctrl+enter from the list: keys.json sends draft.send
  Object.assign(Object.create(Draft.prototype), { draft: null, info: {} }).command("send", cx);
  out.list_send = ran.splice(0);
  // The Thread view, told show_reply, opens the Composer on the Reply Draft without taking the keyboard.
  const view = start(new Thread());
  state.thread = "thread-1";
  answers["thread.view"] = { view: { thread: { f: { id: "thread-1", status: "TODO", title: "Plans" } }, channel: { platform: "gmail" }, messages: [] },
    reply_draft: { ...replyRow }, drafts: [{ ...replyRow }], staged: [] };
  answers["reply.envelope"] = envelope();
  focused.length = 0;
  view.command("show", cx); view.command("show_reply", cx);
  await settle(); await settle();
  out.thread = { open: view.composer.reply?.thread ?? null, body: view.composer.body.value(), focused: focused.length };
  view.command("open_reply", cx); // Enter: the same Composer takes the keyboard
  out.enter_focused = focused.length;
} else if (kase === "next") { // AS-UD-7: after send, schedule or delete, the Draft below is selected; on the last, the one above
  const Drafts = await load("drafts");
  state.page = "drafts";
  const row = (id, extra = {}) => ({ ...newRow, id, ...extra });
  const drafts = Object.assign(Object.create(Drafts.prototype), { rows: [row("a"), row("b"), row("c")], scroll: { scroll_to_item() {} } });
  const view = await draftComposer(row("b"));
  state.draft = "b";
  answers["send.draft"] = null;
  await view.composer.dispatch(false, cx);                // sent
  answers["drafts.list"] = [row("a"), row("c")];
  drafts.load(cx); await settle();
  out.sent = state.draft;
  state.draft = "a"; view.composer.showDraft({ ...info, draft: row("a") });
  answers["command.inbox.schedule_draft"] = {};
  await view.composer.sendPicked(LATER.tomorrow, cx);     // scheduled: it stays, among the scheduled
  answers["drafts.list"] = [row("c"), row("a", { send_at: LATER.tomorrow })];
  drafts.load(cx); await settle();
  out.scheduled = state.draft;
  answers["command.inbox.delete_draft"] = row("a");
  state.draft = "a";
  drafts.command("delete", cx); await settle();           // the last one: the one above takes its place
  answers["drafts.list"] = [row("c")];
  drafts.load(cx); await settle();
  out.deleted_last = state.draft;
} else if (kase === "today-next") { // AS-UD-7 on Today's drafts pane
  const Today = await load("today"), Drafts = await load("drafts");
  state.page = "today";
  const page = (ids) => ({ threads: [], tasks: [], paused: [], unattended: [], drafts: ids.map((id) => ({ ...newRow, id })) });
  const today = Object.assign(Object.create(Today.prototype), { scrolls: {}, at: 0, data: page(["a", "b", "c"]), cases: {} });
  const drafts = Object.assign(Object.create(Drafts.prototype), { rows: [] });
  answers["command.inbox.delete_draft"] = { id: "b" };
  today.at = 1; today.select(cx);
  drafts.command("delete", cx); await settle();           // x on the drafts row
  answers["today.page"] = page(["a", "c"]);
  today.load(cx); await settle();
  out.after_middle = today.rows()[today.at].key;
  drafts.command("delete", cx); await settle();           // x on the last one
  answers["today.page"] = page(["a"]);
  today.load(cx); await settle();
  out.after_last = today.rows()[today.at].key;
} else if (kase === "typed-during-save") { // a keystroke during a save is not marked Saved by that save
  const view = await draftComposer();
  const c = view.composer;
  let answer;
  answers["command.inbox.save_draft"] = () => new Promise((resolve) => { answer = resolve; });
  type(c.body, "First words");
  const saving = c.save(cx);                              // the autosave goes out
  type(c.body, "First words and more");                   // Jeremy keeps typing
  answer({ revision: 2 });
  await saving;
  out.unsaved = Boolean(c.unsaved);
  answers["command.inbox.save_draft"] = { revision: 3 };
  view.command("close", cx);
  await settle();
  out.saved = calls.filter((x) => x.method === "command.inbox.save_draft").map((x) => [x.params.values.body, x.params.expected_revision]);
} else if (kase === "keep") { // AS-UD-8: a reply typed on a Thread selected in the threads pane keeps it there
  const Today = await load("today");
  state.page = "today";
  const thread = (id, who) => ({ id, who, title: `About ${who}`, detail: "NEW_REPLY" });
  const today = Object.assign(Object.create(Today.prototype), { scrolls: {}, at: 0, cases: {},
    data: { threads: [thread("t1", "Ana"), thread("t2", "Ben")], tasks: [], drafts: [], paused: [], unattended: [] } });
  today.at = today.rows().findIndex((x) => x.r?.id === "t1");
  today.select(cx);
  // The Composer saved the typed reply: today.page now lists the Thread's Reply Draft and leaves the Thread out.
  answers["today.page"] = { threads: [thread("t2", "Ben")], tasks: [], drafts: [{ ...replyRow, id: "d9", thread_id: "t1" }], paused: [], unattended: [] };
  answers["cases.list"] = [];
  today.load(cx); await settle();
  const keys = () => Object.fromEntries(Object.entries(today.panes()).map(([k, v]) => [k, v.filter((it) => it.row).map((it) => it.key)]));
  out.typing = { selected: today.rows()[today.at].key, thread: state.thread, panes: keys() };
  today.load(cx); await settle();                         // another save while it is still selected
  out.again = today.rows()[today.at].key;
  today.move(1, cx);                                      // the selection leaves it
  out.left = { selected: today.rows()[today.at].key, panes: keys() };
} else if (kase === "order") { // AS-UD-5: the Drafts page and Today's drafts pane list every Draft in one order
  const { ordered } = (await load("drafts")).ns, { paneItems } = (await load("today")).ns;
  const rows = [{ id: "later", send_at: "2026-11-06T15:00:00" }, { id: "reply", thread_id: "t1" }, { id: "new" },
    { id: "failed", send_error: "refused", thread_id: "t2" }, { id: "soon", send_at: "2026-11-05T15:00:00" }];
  out.drafts = ordered(rows).map((d) => d.id);
  out.today = paneItems({ drafts: rows }).drafts.map((it) => it.row.id);
}
tasks.length = 0;
console.log(JSON.stringify(out));
