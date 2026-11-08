// Issues, the Issue pane, the Action Queue and Today's issues pane (PRD 2026-11-07 FR-UI-1, FR-UI-2, FR-UI-4), run
// in the shipped views with the kit's doubles. Usage: node --experimental-vm-modules app_issues_view.mjs
import { answers, calls, cx, load, ran, settle, state, texts, toasts } from "./app_view_kit.mjs";

const out = {};
const make = (View, fields) => Object.assign(Object.create(View.prototype), fields);
const issue = (id, extra = {}) => ({ id, identifier: `BIZ-${id}`, title: `Title ${id}`, team_id: "team", state_name: "Todo",
  priority: 1, estimate: 2, due_date: "2026-11-12", planned_day: "2026-11-09", data: { team_key: "BIZ", labels: ["Bug"] },
  warnings: [], not_saved: null, ...extra });

// The list: loads the view with the stored options, selects the first Issue, draws group headers with capacity.
const Issues = await load("issues");
answers["issues.view"] = { options: { grouping: "planned_day", subgrouping: "none", ordering: "urgency", completed: "day",
  sub_issues: true, empty_groups: false, properties: ["status", "priority", "estimate"] },
  rows: [{ kind: "group", title: "Today · Mon Nov 9", count: 1, capacity: { capacity: 6, used: 7, left: -1 } },
    { kind: "issue", issue: issue("1", { warnings: ["Larger than a day"] }) }] };
state.page = "issues";
const list = make(Issues, { rows: [], scroll: { scroll_to_item() {} } });
list.load(cx);
await settle();
list.render();
out.list = { asked: calls.find((c) => c.method === "issues.view").params, selected: state.issue, ran: [...ran],
  texts: texts.filter((t) => /TODAY|7 of 6 h|BIZ-1|Title 1|urgent|2p|grouped by/.test(t)) };
out.choices = Issues.ns.displayChoices(answers["issues.view"].options).map((c) => c.value).slice(0, 3);
out.applied = Issues.ns.applyChoice(answers["issues.view"].options, "property=labels").properties;
out.uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(Issues.ns.uuid4());
list.command("plan", cx);
out.forwarded = ran.at(-1);

// The Issue pane: its keys change the Issue through the Action Queue, and offer undo.
const Issue = await load("issue");
answers["issue.detail"] = { lines: [[["BIZ-1", null, true, null]]], issue: issue("1"), statuses: [{ id: "s-done", name: "Done" }] };
const pane = make(Issue, {});
pane.load(cx);
await settle();
pane.command("status", cx);
out.statusPicker = state.picker.options.map((o) => o.label);
answers["command.issues.update"] = "action-1";
pane.command(`status_set:${JSON.stringify({ value: "s-done", text: "" })}`, cx);
await settle();
out.update = calls.find((c) => c.method === "command.issues.update").params;
out.undo = state.undo ?? null;  // z undoes the newest Command on the Service (ADR 0021)
pane.command(`planned:${JSON.stringify({ value: null, text: "2026-11-10" })}`, cx);
await settle();
out.plan = calls.find((c) => c.method === "command.issues.plan").params;
pane.command(`estimate_set:${JSON.stringify({ value: null, text: "1.5" })}`, cx);
out.estimateRefused = toasts.at(-1);
out.planned = ["today", "tomorrow", "fri", "none"].map((t) => Issue.ns.plannedDay(t, new Date(2026, 10, 9)));

// The Action Queue: a header per day with its counts per Connection, then each row.
const Actions = await load("actions");
out.queue = Actions.ns.queueItems([
  { kind: "action", id: "a1", day: "2026-11-09", at: "2026-11-09T14:00:00Z", connection: "Linear" },
  { kind: "task", id: "t1", day: "2026-11-09", at: "2026-11-09T15:00:00Z", connection: null },
  { kind: "draft", id: "d1", day: "2026-11-10", at: "2026-11-10T13:00:00Z", connection: "Gmail" }])
  .map((it) => it.header ? `${it.header} | ${it.counts}` : it.key);
// History (ADR 0021): the Command log below, a header per local day, newest first.
out.history = Actions.ns.historyItems([
  { id: "c2", at: "2026-11-10T15:00:00", label: "Undo: Done: Intro call", source_name: "Console", undoes: "c1", undone: false },
  { id: "c1", at: "2026-11-10T14:00:00", label: "Done: Intro call", source_name: "App", undoes: null, undone: true }])
  .map((it) => it.header ?? `${it.command.label} | ${it.command.source_name}${it.command.undone ? " | undone" : ""}`);

// Today: the issues pane holds today's Planned Issues, and its keys reach the Issue pane.
const Today = await load("today");
const panes = Today.ns.paneItems({ issues: [issue("7")] }, {});
out.today = panes.issues.map((it) => it.key);

// The rail: Connections grouped under one header per kind (ADR 0018); Context connections are not among them.
const Nav = await load("nav");
out.rail = Nav.ns.connectionRows([
  { id: 3, platform: "linear", kind: "issue", name: "Linear" }, { id: 1, platform: "gmail", kind: "email", name: "Gmail" },
  { id: 4, platform: "linear", kind: "context", name: "Linear" }, { id: 2, platform: "slack", kind: "slack", name: "Slack" },
  { id: 5, platform: "newer", kind: null, name: "Newer" }, { id: 6, platform: "fastmail", kind: "email", name: "Fastmail" }])
  .map((r) => r.header ?? r.id);
console.log(JSON.stringify(out));
