// The bottom bar, as the Console's StatusLine (D9): the mode block, the page's keys grouped move · reply · triage ·
// organize · general (each one clickable), then the Event now and next, Todo and Snoozed counts, each Connection's
// health and the installed build. The App adds what Jeremy kept (D15, D22): online or read-only, the Service and its
// build, and the writes still saving. Toasts show bottom right (views/toasts).
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { call, nextEvent, status, theme, shared, keys, run } from "comms";

// The Console's binding descriptions, by App command. Commands not here show no key, as the Console's show=False.
const LABEL = {
  "thread.reply_sender": "reply", "thread.reply_all": "reply all", "thread.done": "done", "thread.snooze": "snooze",
  "thread.block": "block sender", "thread.ignore": "ignore", "thread.case": "case", "thread.status": "status",
  "thread.copy": "copy for agent", "thread.react": "react", "thread.respond": "respond",
  "drafts.new": "new draft", "drafts.delete": "delete draft", "drafts.copy": "copy for agent",
  "people.merge": "merge into…", "person.enroll": "enroll in Sequence", "person.block": "block / unblock",
  "person.rename": "rename", "person.add": "add identity", "person.remove": "remove identity", "person.resume": "resume",
  "person.finish": "finish", "person.organizations": "organizations",
  "organization.stage": "stage", "organization.value": "value", "organization.people": "people", "pipeline.copy": "copy for agent",
  "connection.add": "add rule", "connection.delete": "delete rule",
  "tasks.done": "done · next", "tasks.skip": "skip · next", "tasks.filter": "filter", "tasks.person": "person", "tasks.copy": "copy for agent",
  "sequence.edit": "Steps", "sequences.new": "new Sequence", "sequences.archive": "archive / restore", "sequences.delete": "delete",
  "sequences.rename": "rename", "sequences.describe": "use when", "sequences.sends_from": "sends from",
  "sequence.new_step": "new Step", "sequence.delete_step": "delete Step",
  "cases.new": "new case", "case.describe": "description",
  "calendar.today": "today", "event.join": "join link", "event.respond": "respond",
};
const PAIRS = { "calendar.next_day": ["h/l", "day"], "calendar.next_week": ["j/k", "week"], "calendar.next_month": ["{/}", "month"],
  "sequence.next": ["J/K", "move"] };
const GROUP = { j: 0, escape: 0, u: 0, r: 1, a: 1, i: 1, e: 2, g: 2, b: 2, m: 2, l: 3, s: 3, z: 4, "?": 4 };
const CONN = { gmail: "mail", fastmail: "mail", slack: "message-square", quo: "phone", gcal: "calendar" };
const SHOWN = { "shift-r": "R", "shift-n": "N", "shift-x": "X" };

const TITLE = { person: "Person", organization: "Organization", connection: "Connection", event: "Event", case: "Case" };

/** The Console's mode block: TODO, TODAY › TASKS, CASES · TODO, PIPELINE · REVENUE, … › PERSON. */
function mode() {
  const stack = shared("stack") ?? [];
  const base = stack[0] ?? {};
  const page = base.page ?? shared("page") ?? "today";
  let m = page === "threads" ? (base.view ?? "Todo").toUpperCase()
    : page === "cases" ? `CASES · ${(base.case_view ?? "Todo").toUpperCase()}`
    : page === "connection" ? "CONNECTION"
    : page === "pipeline" ? `PIPELINE · ${(base.pipeline ?? "revenue").toUpperCase()}` : page.toUpperCase();
  if (page === "today" && stack.length <= 1)
    m += ` › ${{ task: "TASKS", draft: "DRAFTS", paused: "PAUSED", unattended: "UNATTENDED" }[shared("detail")] ?? "THREADS"}`;
  if (stack.length > 1) m += ` › ${(TITLE[shared("page")] ?? shared("page") ?? "").toUpperCase()}`;
  return m;
}

/** The page's keys as the Console's bar groups them: [[key, label, command], …] per group. */
export function barKeys(k, page, detail, depth) {
  const groups = [];
  const add = (key, label, command) => (groups[GROUP[key] ?? 3] ??= []).push([key, label, command]);
  const map = page === "today" && detail ? {} : k[`page:${page}`] ?? {}; // Today's Thread keys act on Thread rows only
  const seen = new Set();
  if (Object.values(map).some((c) => c.endsWith(".next") && !PAIRS[c])) add("j", "move", null);
  if (depth > 1) add("escape", "back", "nav.back");
  for (const [key, command] of Object.entries(map)) {
    if (seen.has(command)) continue;
    seen.add(command);
    if (PAIRS[command]) add(PAIRS[command][0], PAIRS[command][1], command);
    else if (LABEL[command]) add(SHOWN[key] ?? key, LABEL[command], command);
  }
  add("z", "undo", "nav.undo");
  add("?", "help", "overlay.help");
  return groups.filter(Boolean);
}

export default class Status extends View {
  init(_props, cx) {
    this.shell = {};
    this.counts = {};
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) {
        const e = await nextEvent();
        if (e.kind === "rows") this.load(cx);
        if (["status", "update", "disconnected"].includes(e.kind)) cx.notify();
      }
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const [rail, shell] = await Promise.all([call("rail.summary"), call("shell.summary")]);
        this.counts = rail.counts ?? {};
        this.shell = shell;
      } catch (_) {}
      cx.notify();
    });
  }

  render() {
    const t = theme();
    const s = status() ?? {};
    const h = this.shell;
    const depth = (shared("stack") ?? []).length;
    const sep = () => div().text_color(t.dim).child("/");
    const key = ([k, label, command]) => h_flex().id(`bar-${k}-${label}`).gap(4).items_center().flex_none().cursor_pointer()
      .when(Boolean(command), (el) => el.on_click(() => run(command)))
      .child(div().font_semibold().text_color(t.blue ?? t.accent).child(k === "escape" ? "esc" : k))
      .child(div().text_color(t.grey2 ?? t.muted).child(label));
    const groups = barKeys(keys() ?? {}, shared("page") ?? "today", shared("detail"), depth);
    const red = (text) => div().px(6).rounded(3).bg(t.danger).text_color(t.background).font_semibold().child(text);
    const right = [];
    if (h.now) right.push(div().child(`now ${h.now.title} · ${h.now.left} min left`));
    if (h.next) right.push(h_flex().id("bar-next").cursor_pointer().on_click(() => run("nav.calendar")).gap(4)
      .child(div().text_color(t.muted).child("next")).child(div().child(h.next.title)).child(div().text_color(t.muted).child(h.next.when)));
    right.push(h_flex().gap(4).child(svg("icons/square.svg").size(11).text_color(t.muted)).child(`${this.counts.Todo ?? 0} todo`));
    right.push(h_flex().gap(4).child(svg("icons/clock.svg").size(11).text_color(t.muted)).child(`${this.counts.Snoozed ?? 0} snoozed`));
    if (h.calendar_broken) right.push(red("Google Calendar broken"));
    for (const c of h.connections ?? [])
      right.push(c.ok ? h_flex().gap(3).child(svg(`icons/${CONN[c.platform] ?? "server"}.svg`).size(11).text_color(t.muted))
        .child(svg("icons/circle-check.svg").size(10).text_color(t.green))
        : red(`${c.name}${c.writes ? " writes" : ""} ${c.supported ? "broken" : "unavailable"}`));
    if (h.build) right.push(div().text_color(t.muted).child(h.build));
    const parts = [];
    right.forEach((p, i) => { if (i) parts.push(sep()); parts.push(p); });
    return h_flex().size_full().pl(0).pr(12).gap(12).items_center().bg(t.chrome).border_t(1).border_color(t.border).text_size(11.8).text_color(t.foreground)
      .child(div().h_full().px(10).flex_none().flex().items_center().bg(t.grey2 ?? t.muted).text_color(t.background).font_semibold().child(mode()))
      .child(h_flex().gap(6).items_center().flex_none()
        .child(div().size(6).rounded(3).bg(s.online ? t.status_done : t.danger))
        .child(div().text_color(s.online ? t.muted : t.danger).child(s.online ? "Online" : `Read-only · ${s.problem ?? "offline"}`)))
      .child(h_flex().flex_1().min_w(0).overflow_hidden().gap(14).items_center()
        .children(groups.flatMap((g, i) => [...(i ? [div().text_color(t.dim).child("·")] : []), ...g.map(key)])))
      .child(h_flex().flex_none().gap(8).items_center().children(parts))
      .child(h_flex().flex_none().gap(10).items_center().text_color(t.muted)
        .child(h_flex().gap(4).items_center().child(svg("icons/server.svg").size(12)).child(s.service ?? "This machine"))
        .child(h_flex().gap(4).items_center().child(svg("icons/git-branch.svg").size(12)).child(s.service_build ? `Service ${s.service_build}` : "Service ?"))
        .when(Boolean(s.pending), (el) => el.child(div().child(`${s.pending} saving`))));
  }
}
