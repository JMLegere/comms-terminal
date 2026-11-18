// The App's Promises page and Today's promises pane (FR-PR-8, FR-PR-9), run in the shipped views with the kit's
// doubles. Usage: node --experimental-vm-modules app_promises_view.mjs
import { answers, calls, cx, load, ran, settle, state, texts } from "./app_view_kit.mjs";

const out = {};
const promise = (id, extra = {}) => ({ id, what: `Promise ${id}`, due_at: "2026-11-20T21:00:00+00:00", slack: 90,
  risk: null, state: "OPEN", draft_ready: false, people: [{ id: "p1", display_name: "Anna Ciprick" }], links: [], ...extra });
answers["promises.page"] = { count: 2, captured: { at: "2026-11-18T14:00:00+00:00" }, sections: {
  overdue: [promise("a", { risk: "overdue", slack: -30, links: [{ kind: "thread", target: "thread-9" }] })],
  at_risk: [], today: [], this_week: [promise("b")], later: [], tentative: [promise("c", { state: "TENTATIVE" })] } };
answers["thread.view"] = { view: { thread: { f: { status: "TODO", status_detail: "NEW_REPLY" } } } };

const Promises = await load("promises");
out.rows = Promises.ns.promiseRows(answers["promises.page"]).map((r) => r.header ?? r.p.id);
out.slackTime = [Promises.ns.slackTimeText(-30), Promises.ns.slackTimeText(90), Promises.ns.slackTimeText(45), Promises.ns.slackTimeText(69289)];
state.page = "promises";
const page = Object.create(Promises.prototype);
page.load(cx);
await settle();
out.selected = state.promise;
out.side = state.side;
out.ranAfterLoad = [...ran];
page.render();
out.foot = texts.find((t) => t.startsWith("Captured up to")) ?? null;
page.open(answers["promises.page"].sections.overdue[0]);
await settle();
out.opened = { thread: state.thread, ran: ran.slice(-2) };

// Today: the promises pane comes first, and its row shows the Promise in the side pane.
const Today = await load("today");
const today = Object.create(Today.prototype);
today.data = { promises: [promise("a", { risk: "at_risk", section: "at_risk" })], threads: [{ id: "thread-1", who: "Ana", title: "Hello", detail: "NEW_REPLY" }] };
state.page = "today";
out.todayKinds = [...new Set(today.rows().filter((x) => x.r).map((x) => x.kind))];
out.todayHeader = Today.ns.promiseItems(today.data.promises).map((x) => x.promiseHeader?.key ?? x.row.id);
out.due = [Promises.ns.dueIn("2026-11-18T10:00:00Z", Date.parse("2026-11-18T12:00:00Z")), Promises.ns.dueIn("2026-11-20T12:00:00Z", Date.parse("2026-11-18T12:00:00Z"))];
today.at = today.rows().findIndex((x) => x.kind === "promises" && x.r);
today.select(cx);
out.todaySide = state.side;
out.todayDetail = state.detail;
out.calls = calls.map((c) => c.method);
console.log(JSON.stringify(out));
