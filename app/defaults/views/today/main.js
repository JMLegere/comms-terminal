// Today (GLOSSARY), as the Console's TodayPage (ADR 0014): bordered panes stacked in order, each with its pill
// title — threads (a Threads list fixed to Todo: Status and Case headers), drafts, paused, tasks (OVERDUE / DUE TODAY /
// LATER TODAY, with a red "N overdue"), issues and unattended; an empty pane hides and the last one shown takes the spare
// height. ↓/↑ (j/k) run on from one pane into the next, tab jumps to the next pane, and the row under the cursor fills
// the right pane (shared "detail" picks its view). A key acts on the row's own kind: r replies on a Thread and
// resumes a Paused Enrollment; x ignores a Thread, skips a Task and deletes a Draft. Thread keys reach only a Thread
// row: any other row clears the shared Thread, so a key never reaches a Thread that is not on screen (D87).
// The drafts pane lists the Drafts page's Drafts (FR-UD-8); a Reply Draft shows in place, as its Thread with the
// Composer open on it (decision #144). An email Task stays a Task in the tasks pane until its Draft is sent, and
// shows its Draft in place as the Tasks page does (Jeremy, 2026-11-04). After a send, schedule or delete the Draft
// below is selected, else the one above (FR-UD-7). A selected row keeps its place while its Thread moves to another
// pane, as when a reply typed on a Thread makes it a Reply Draft; it takes its new place once the selection leaves
// it (FR-UD-9).
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, focusMain } from "comms";

const GLYPH = { // Status detail: glyph, colour token, label (as threads/main.js)
  CREATED: ["\u{f0674}", "status_needs_first", "Needs first response"], NEW_REPLY: ["\u{f173e}", "status_needs_next", "Needs next response"],
  IN_PROGRESS: ["\u{f0349}", "status_investigating", "Investigating"], REPLY_DRAFT: ["\u{f048a}", "status_reply_draft", "Reply Draft"],
};
const TODO = ["CREATED", "NEW_REPLY", "IN_PROGRESS", "REPLY_DRAFT"];
const I = { TODO: "\u{f0766}", tasks: "\u{f05c7}", edited: "\u{f0cb6}", sequence: "\u{f027b}", organization: "\u{f01d7}",
  today: "\u{f05a8}", done: "\u{f012c}", SNOOZED: "\u{f04b2}", warn: "\u{f002a}", case: "\u{f0814}",
  email: "\u{f01f0}", slack: "\u{f04b1}", sms: "\u{f011c}", time: "\u{f0150}",
  call: "\u{f0df0}", linkedin: "\u{f033b}", letter: "\u{f0d8c}", action: "\u{f027b}", if: "\u{f0641}", meeting: "\u{f0ed8}",
  issues: "\u{f0756}", pin: "\u{f0403}" };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");
const PANES = ["threads", "drafts", "paused", "tasks", "issues", "unattended"];
const DETAIL = { threads: null, tasks: "task", issues: "issue", drafts: "draft", paused: "paused", unattended: "unattended" };
// What each key does on each kind of row (the Console's Today hands a key to the list that holds the cursor).
const THREAD_KEYS = { r: "reply_sender", a: "reply_all", e: "done", g: "snooze", b: "block", m: "ignore", x: "ignore", l: "case",
  s: "status", p: "person", o: "link", y: "copy", f: "forward", v: "react", i: "respond", delete: "unstage" };
const ROUTE = {
  threads: Object.fromEntries(Object.entries(THREAD_KEYS).map(([k, c]) => [k, `thread.${c}`])),
  tasks: { e: "tasks.done", x: "tasks.skip", n: "tasks.new_thread", p: "tasks.person", y: "tasks.copy", "1": "tasks.pick_first",
    "2": "tasks.pick_second" },
  drafts: { n: "drafts.new", x: "drafts.delete", y: "drafts.copy", "ctrl-s": "draft.save", "ctrl-enter": "draft.send" },
  paused: { "r": "person.resume", "f": "person.finish" },
  issues: { p: "issue.plan_all", w: "issue.plan", f: "issue.pin", s: "issue.status", i: "issue.priority", e: "issue.estimate",
    d: "issue.due", t: "issue.title", o: "issue.open", y: "issue.copy" }, // the Issues Today lists (FR-PL-12), as the Issues page
  unattended: { s: "organization.stage", v: "organization.value" }, // as its Organization page
  unattended_person: { p: "person.stage", v: "person.value" }, // a Person in Fundraising or Talent (ADR 0016)
};
const KEYS = [...new Set(Object.values(ROUTE).flatMap(Object.keys))];
const NAME = { threads: "Thread", tasks: "Task", issues: "Issue", drafts: "Draft", paused: "Paused", unattended: "Unattended",
  unattended_person: "Unattended" };
/** A row's key set: an Unattended row holds a Person or an Organization, which take different keys. */
const kindOf = (row) => (row.kind === "unattended" && row.r?.person_id ? "unattended_person" : row.kind);
/** "thread.reply_sender" → "reply": a command as the bar and the dead-key toast name it. */
export const verb = (command) => command.split(".")[1].replace("reply_sender", "reply").replace(/_/g, " ");
/** The keys a row kind takes, as [key, label] pairs, for the bar and the dead-key toast. */
export const keysOf = (kind) => Object.entries(ROUTE[kind] ?? {}).filter(([k]) => k.length === 1).map(([k, c]) => [k, verb(c)]);

/** Compact age, as the Console's when(): now, 5m, 3h, 2d, then a date. */
export function when(iso, now = Date.now()) {
  if (iso?.$dt) iso = iso.$dt; // the core tags datetimes ({ $dt }); a bare one read "undefined NaN"
  if (!iso) return "";
  const t = new Date(iso), secs = (now - t) / 1000;
  if (secs < 60) return "now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 7 * 86400) return `${Math.floor(secs / 86400)}d`;
  return `${MONTHS[t.getMonth()]} ${t.getDate()}`;
}

/** All clear's times: Mon 03 Nov 09:00 (the Console's %a %d %b %H:%M). */
export const wake = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${pad(d.getDate())} ${MONTHS[d.getMonth()]} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

/** An Unattended row's Stages, joined: "Proposal · Intro" (D86). */
export const stagesOf = (r) => Object.values(r.stages ?? {}).join(" · ");

const caseCounts = (counts) => Object.entries(counts ?? {}).filter(([, n]) => n).map(([v, n]) => `${n} ${v.toLowerCase()}`).join(" · ") || "no threads yet";

/** Todo's Status headers with Case headers inside them, as threads/main.js grouped("Todo"). */
export function threadItems(rows, cases) {
  const byCase = (rs, depth, under) => {
    if (!rs.some((r) => r.case_id)) return rs.map((r) => ({ row: r, depth }));
    const groups = new Map();
    for (const r of rs) { const c = r.case_id ?? null; if (!groups.has(c)) groups.set(c, []); groups.get(c).push(r); }
    const oldest = (c) => groups.get(c).map((r) => r.waiting_since ?? "").sort()[0];
    const order = [...groups.keys()].sort((a, b) => (a !== null) - (b !== null) || (oldest(a) < oldest(b) ? -1 : oldest(a) > oldest(b) ? 1 : 0));
    return order.flatMap((cid) => {
      const n = groups.get(cid).length, c = cases[cid];
      return [{ caseHeader: { id: cid, under, title: c?.title ?? "", summary: cid ? caseCounts(c?.counts) : `${n} thread${n === 1 ? "" : "s"}` }, depth },
        ...groups.get(cid).map((r) => ({ row: r, depth: depth + 1 }))];
    });
  };
  return TODO.flatMap((detail) => {
    const group = rows.filter((r) => r.detail === detail);
    return group.length ? [{ statusHeader: { detail, label: GLYPH[detail][2], count: group.length }, depth: 0 }, ...byCase(group, 1, detail)] : [];
  });
}

/** Every pane's items in order; `pick` marks the rows the cursor may rest on (Task group headers are not). */
export function paneItems(data, cases) {
  const d = data ?? {};
  const out = { threads: threadItems(d.threads ?? [], cases ?? {}).map((it) => ({ ...it, pick: true })) };
  out.tasks = [];
  let group = null;
  for (const r of d.tasks ?? []) {
    if (r.group !== group) out.tasks.push({ groupHeader: (group = r.group).toUpperCase() });
    out.tasks.push({ row: r, pick: true });
  }
  for (const k of ["issues", "drafts", "paused", "unattended"]) out[k] = (d[k] ?? []).map((r) => ({ row: r, pick: true }));
  const rank = (r) => r.send_error ? "0" : r.send_at ? `2${r.send_at}` : "1"; // the Drafts order (drafts/main.js)
  out.drafts.sort((a, b) => (rank(a.row) < rank(b.row) ? -1 : rank(a.row) > rank(b.row) ? 1 : 0));
  for (const k of PANES) out[k].forEach((it, i) => { it.pane = k; it.key = keyOf(k, it, i); });
  return out;
}

/** The Thread or Task a row stands for, so a row that moved to another pane is known there (FR-UD-9). */
export function idsOf(pane, r) {
  if (!r) return [];
  if (pane === "threads") return [`t${r.id}`];
  if (pane === "tasks") return [`k${r.id}`];
  if (pane === "drafts") return r.thread_id ? [`t${r.thread_id}`] : [];
  return [];
}

/** `held` ({ item, index }), the selected row, kept where it was while its Thread or Task shows in another pane:
 * that pane's row for it is left out until the selection leaves (FR-UD-9). */
export function holdPlace(panes, held) {
  const { item, index } = held ?? {};
  if (!item || panes[item.pane].some((it) => it.key === item.key)) return panes;
  const ids = idsOf(item.pane, item.row);
  let moved = false;
  for (const k of PANES) {
    if (k === item.pane) continue;
    const kept = panes[k].filter((it) => !idsOf(k, it.row).some((x) => ids.includes(x)));
    moved ||= kept.length !== panes[k].length;
    panes[k] = kept;
  }
  if (moved) panes[item.pane].splice(Math.min(index, panes[item.pane].length), 0, item);
  return panes;
}

function keyOf(pane, it, i) {
  if (it.row) return `${pane}-${it.row.id}`;
  if (it.caseHeader) return `${pane}-case-${it.caseHeader.under ?? ""}-${it.caseHeader.id}`; // one Case may head rows under two Statuses
  if (it.statusHeader) return `${pane}-status-${it.statusHeader.detail}`;
  return `${pane}-group-${i}`;
}

export default class Today extends View {
  init(_props, cx) {
    this.data = null;
    this.cases = {};
    this.at = 0;
    this.scrolls = Object.fromEntries(PANES.map((k) => [k, VirtualListScrollHandle.new()]));
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "today") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  panes() {
    return holdPlace(paneItems(this.data, this.cases), this.held);
  }

  /** The rows the cursor visits, pane after pane. */
  rows() {
    const p = this.panes();
    return PANES.flatMap((k) => p[k].filter((it) => it.pick)).map((it) => ({ ...it, kind: it.pane, r: it.row }));
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const here = this.rows()[this.at], moved = this.advanced();
        const keep = moved ?? here?.key;
        const index = here ? this.panes()[here.kind].findIndex((it) => it.key === here.key) : -1;
        const [data, cases] = await Promise.all([call("today.page"), call("cases.list").catch(() => [])]);
        this.data = data;
        this.cases = Object.fromEntries((cases ?? []).map((c) => [c.id, c]));
        // FR-UD-9: the selected row is held where it is; panes() places it there only while its Thread or Task
        // shows in another pane. An action that moves on (advance) holds nothing.
        this.held = !moved && here?.row ? { item: { pane: here.pane, key: here.key, row: here.row, depth: here.depth, pick: true }, index } : null;
        const rows = this.rows();
        const again = rows.findIndex((x) => x.key === keep);
        const first = Math.max(0, rows.findIndex((x) => x.r)); // on arrival: the first Thread, not its header
        this.at = again >= 0 ? again : keep ? Math.min(this.at, Math.max(rows.length - 1, 0)) : first;
        this.error = null;
        this.select(cx, true);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  move(by, cx) {
    const n = this.rows().length;
    if (!n) return;
    this.at = Math.max(0, Math.min(n - 1, this.at + by));
    this.select(cx);
    cx.notify();
  }

  /** s just changed the selected Thread's Status detail (shared "advance", set before its write): the key of the
   * Thread below the cursor, else the one above, so the cursor moves on as after Done (D32). Read once, at reload. */
  advanced() {
    const a = shared("advance"), rows = this.rows(), here = rows[this.at];
    if (!a || Date.now() - a.at > 5000 || shared("page") !== "today" || !here?.r) return null;
    // s on a Thread (D32), or a send, schedule or delete of a Draft (FR-UD-7): the next of its kind, else the one above.
    const kind = here.kind === "threads" && here.r.id === a.thread ? "threads" : here.kind === "drafts" && here.r.id === a.draft ? "drafts" : null;
    if (!kind) return null;
    setShared("advance", null);
    const same = (x) => x.kind === kind && x.r;
    return (rows.slice(this.at + 1).find(same) ?? rows.slice(0, this.at).reverse().find(same))?.key ?? null;
  }

  /** tab: the first row of the next pane that has one, round to the first. */
  nextPane(cx) {
    const rows = this.rows();
    const here = rows[this.at]?.kind;
    const at = rows.findIndex((x, i) => i > this.at && x.kind !== here);
    this.at = at >= 0 ? at : 0;
    this.select(cx);
    cx.notify();
  }

  select(cx, reload = false) {
    let row = this.rows()[this.at];
    if (this.held && row?.key !== this.held.item.key) { // FR-UD-9: the selection left the held row; it takes its new place
      this.held = null;
      this.at = Math.max(0, this.rows().findIndex((x) => x.key === row?.key));
      row = this.rows()[this.at];
    }
    if (!row || shared("page") !== "today") return;
    const r = row.r;
    // A reload on the same Task leaves the pane alone: the Task may have swapped in its Draft or Thread, and the task
    // view reloads itself on rows. Showing it again put the Task over the Draft on every autosave (the flicker).
    if (reload && row.kind === "tasks" && shared("task") === r?.id) return this.target();
    const inPlace = row.kind === "drafts" && Boolean(r?.thread_id); // a Reply Draft shows as its Thread (decision #144)
    setShared("thread", row.kind === "threads" ? r?.id ?? null : inPlace ? r.thread_id : null);
    setShared("detail", inPlace ? "thread" : DETAIL[row.kind]);
    if (row.kind === "threads") run("thread.show");
    setShared("task", row.kind === "tasks" ? r.id : null); // as the Thread: a Task off screen never acts or reloads (D87)
    if (row.kind === "tasks") { run("task.show"); run("tasks.show"); }
    if (row.kind === "drafts") { setShared("draft", r.id); run(inPlace ? "thread.show" : "draft.show"); if (inPlace) run("thread.show_reply"); }
    if (row.kind === "issues") { setShared("issue", r.id); run("issue.show"); }
    if (row.kind === "paused") { setShared("person", r.person_id); setShared("side", { kind: "paused", id: r.person_id }); run("person.show"); run("side.show"); }
    if (row.kind === "unattended" && r.person_id) { // a Person in Fundraising or Talent (ADR 0016)
      setShared("person", r.person_id);
      setShared("side", { kind: "unattended_person", id: r.person_id });
      run("person.show"); run("side.show");
    } else if (row.kind === "unattended") {
      setShared("organization", { id: r.id, domain: r.domain });
      setShared("side", { kind: "unattended", id: r.id });
      run("organization.show"); run("side.show");
    }
    const items = this.panes()[row.kind];
    this.scrolls?.[row.kind]?.scroll_to_item(Math.max(0, items.findIndex((it) => it.key === row.key)), "center");
    this.target();
  }

  /** The bar's line for the focused row (element focus): what it is and the keys it takes. */
  target() {
    const row = this.rows()[this.at];
    if (!row || shared("page") !== "today" || shared("focus") === "detail") return;
    const r = row.r ?? {};
    const what = { threads: r.title, tasks: r.person ?? r.title, issues: `${r.identifier} ${r.title}`, drafts: r.subject ?? r.title, paused: r.person,
      unattended: r.name ?? r.domain }[row.kind];
    setShared("target", { col: "list", label: `${NAME[row.kind]} · ${what ?? ""}`, keys: [["enter", "open"], ...keysOf(kindOf(row))] });
  }

  enter(cx) {
    const row = this.rows()[this.at];
    if (!row) return;
    const r = row.r;
    const push = (entry) => { setShared("push", entry); run("nav.push"); };
    switch (row.kind) {
      case "threads":
        if (row.caseHeader) return row.caseHeader.id && push({ page: "case", case: row.caseHeader.id });
        if (r) run("nav.focus_right"); // into the Thread: its newest message takes the focus
        return;
      case "tasks": return run("task.open");
      case "issues": return run("nav.focus_right"); // into the Issue: its keys act there
      case "drafts":
        setShared("focus", "detail"); // the Draft's Composer takes the keyboard, where it is shown
        return run(r.thread_id ? "thread.open_reply" : "draft.edit");
      case "paused": return push({ page: "person", person: r.person_id });
      case "unattended": return push(r.person_id ? { page: "person", person: r.person_id }
        : { page: "organization", organization: { id: r.id, domain: r.domain } });
    }
  }

  command(c, cx) {
    if (c === "show") return this.load(cx);
    if (c === "next" || c === "prev") return this.move(c === "next" ? 1 : -1, cx);
    if (c === "tab") return this.nextPane(cx);
    if (c === "enter") return this.enter(cx);
    if (c === "focus_entry") return this.target();
    if (KEYS.includes(c)) { // a key goes to the row's own kind; one it does not take says what that row takes
      const row = this.rows()[this.at];
      const kind = row && kindOf(row);
      const to = kind && ROUTE[kind][c];
      if (to) return run(to);
      if (kind) toast(`${c}: ${NAME[kind]} rows take ${keysOf(kind).map(([k, l]) => `${k} ${l}`).join(" · ") || "enter"}`);
    }
  }

  render() {
    const t = theme();
    const panes = this.panes();
    const counts = Object.fromEntries(PANES.map((k) => [k, panes[k].filter((it) => it.row).length]));
    const shown = PANES.filter((k) => counts[k]);
    const sel = this.rows()[this.at]?.key;
    const focusHere = (shared("focus") ?? "list") === "list";
    const pill = (text, color) => div().flex_none().px(6).rounded(4).bg(color).text_color(t.background).font_semibold().child(text);
    const title = {
      threads: [pill(`${I.TODO} threads · ${counts.threads}`, t.blue)],
      tasks: [pill(`${I.tasks} tasks · ${counts.tasks}`, t.blue), ...(() => {
        const late = (this.data?.tasks ?? []).filter((x) => x.group === "overdue").length;
        return late ? [div().font_semibold().text_color(t.red).child(`${late} overdue`)] : [];
      })()],
      issues: [pill(`${I.issues} issues · ${counts.issues}`, t.blue), ...(() => { // today's capacity, Overdue hours apart (FR-PL-13)
        const c = this.data?.issues_capacity;
        return c ? [div().text_color(c.used > c.capacity ? t.red : t.muted).child(`${c.used} of ${c.capacity} h`),
          ...(c.overdue ? [div().font_semibold().text_color(t.red).child(`${c.overdue} h overdue`)] : [])] : [];
      })()],
      drafts: [pill(`${I.edited} drafts · ${counts.drafts}`, t.blue)],
      paused: [pill(`${I.sequence} paused · ${counts.paused}`, t.yellow)],
      unattended: [pill(`${I.organization} unattended · ${counts.unattended}`, t.red)],
    };
    const glyph = (detail) => div().w(16).flex_none().text_color(t[GLYPH[detail]?.[1]] ?? t.muted).child(GLYPH[detail]?.[0] ?? "·");
    const name = (text, w = 160) => div().w(w).flex_none().truncate().font_semibold().text_color(t.strong ?? t.foreground).child(text ?? "");
    const line = (it) => {
      const el = h_flex().id(it.key).h(26).pl(8 + 14 * (it.depth ?? 0)).pr(8).gap(8).rounded(4).items_center()
        .when(Boolean(it.pick), (e) => e.cursor_pointer().hover((s) => s.bg(t.hover)))
        // The path (Logseq bullet threading): the focused row has a full bar and highlight; while the focus is
        // further in (the Thread), the row keeps a thin bar as its ancestor.
        .border_l(3).border_color(it.key === sel ? t.accent : t.background)
        .when(it.key === sel && focusHere, (e) => e.bg(t.selected))
        .when(it.key === sel && !focusHere, (e) => e.border_l(1));
      if (it.groupHeader) return el.child(div().w(16)).child(div().font_semibold().text_color(t.muted).child(it.groupHeader));
      if (it.statusHeader) {
        const h = it.statusHeader;
        return el.child(glyph(h.detail)).child(div().flex_1().font_semibold().text_color(t[GLYPH[h.detail][1]]).child(h.label))
          .child(div().flex_none().text_color(t.muted).child(String(h.count)));
      }
      if (it.caseHeader) {
        const h = it.caseHeader;
        return el.child(div().w(16).flex_none().text_color(t.foreground).child(h.id ? I.case : ""))
          .child(div().flex_none().max_w(260).truncate().font_semibold().text_color(h.id ? t.blue : t.muted).child(h.id ? h.title : "No case"))
          .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(h.summary));
      }
      const r = it.row;
      switch (it.pane) {
        case "threads":
          return el.child(glyph(r.detail))
            .child(div().flex_none().max_w(200).truncate().font_semibold().text_color(t.strong ?? t.foreground).child(r.who ?? ""))
            .when(Boolean(r.where), (e) => e.child(div().flex_shrink(1).min_w(0).max_w(140).truncate().text_color(t.muted).child(`· ${r.where}`)))
            .when(Boolean(r.drafted), (e) => e.child(div().flex_none().text_color(t.yellow).child(I.edited)))
            .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(r.title ?? ""))
            .child(div().flex_none().text_color(t.muted).child(when(r.last_at)));
        case "tasks": {
          const due = r.group === "overdue" ? `${r.age}d` : r.group === "later today"
            ? (() => { const d = new Date(r.due_at); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; })() : "";
          return el.child(div().w(16).flex_none().text_color(t.foreground).child(I[r.kind] ?? ""))
            .when(Boolean(r.flag), (e) => e.child(div().flex_none().text_color(t.red).child(I.warn)))
            .child(name(r.person)).child(div().w(52).flex_none().text_color(t.foreground).child(`Day ${r.day}`))
            .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(r.sequence ?? ""))
            .child(div().flex_none().text_color(r.group === "overdue" ? t.red : t.muted).child(due));
        }
        case "drafts": {
          const word = r.send_error ? ["failed", "red"] : r.needs_edit ? ["edit me", "yellow"]
            : r.send_at ? [`${I.time} ${DAYS[new Date(r.send_at).getDay()]}`, "muted"] : ["saved", "muted"];
          return el.child(div().w(16).flex_none().text_color(t.muted).child(I[r.kind] ?? ""))
            .child(name(r.recipient || "No recipient", 140))
            .child(h_flex().flex_1().min_w(0)
              .when(Boolean(r.thread_id), (e) => e.child(div().flex_none().text_color(t.blue).child("Reply Draft · ")))
              .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child((r.subject || r.body || "New draft").replace(/\r?\n/g, " "))))
            .child(div().w(56).flex_none().text_color(t[word[1]]).child(word[0]));
        }
        case "issues":
          return el.child(div().w(16).flex_none().text_color((r.warnings ?? []).length || r.not_saved ? t.red : t.muted)
            .child((r.warnings ?? []).length || r.not_saved ? I.warn : I.issues))
            .child(div().w(80).flex_none().text_color(t.muted).child(r.identifier ?? ""))
            .when(Boolean(r.overdue), (e) => e.child(div().flex_none().font_semibold().text_color(t.red).child("overdue")))
            .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(r.title ?? ""))
            .child(div().flex_none().text_color(t.muted).child(`${r.pinned ? `${I.pin} ` : ""}${r.state_name ?? ""}`));
        case "paused":
          return el.child(div().w(16).flex_none().text_color(t.yellow).child(I.sequence)).child(name(r.person))
            .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(`${r.sequence ?? ""}${r.finishing ? ` · finishing as ${r.finishing}` : ""}`))
            .child(div().flex_none().text_color(t.muted).child(`${r.pause_reason || "paused"} ${when(r.paused_at)}`));
        default:
          return el.child(div().w(16).flex_none().text_color(t.red).child(I.organization)).child(name(r.name))
            .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(stagesOf(r)))
            .child(div().flex_none().text_color(t.muted).child(r.last ? when(r.last) : "never"));
      }
    };
    const pane = (k) => {
      const items = panes[k];
      // Each pane asks for all its rows; when they do not all fit, each gives up room in proportion to its size, so
      // the window never has empty space while a pane scrolls. Three rows always show.
      return v_flex().id(`pane-${k}`).rounded(6).border(1).border_color(t.border).bg(t.background)
        .flex_none().flex_shrink(1).h(34 + 26 * items.length).min_h(34 + 26 * Math.min(items.length, 3))
        .child(h_flex().h(26).flex_none().px(8).gap(8).items_center().text_size(12).children(title[k]))
        .child(div().flex_1().min_h(0).px(2).pb(4).child(
          v_virtual_list(`today-${k}`, items.length, 26, (i) => items[i].key, (range) => items.slice(range.start, range.end).map(line))
            .size_full().track_scroll(this.scrolls[k])
            .on_item_click((key, cx) => {
              const it = items.find((x) => x.key === key);
              if (it?.caseHeader?.id) { setShared("push", { page: "case", case: it.caseHeader.id }); return run("nav.push"); }
              const at = this.rows().findIndex((x) => x.key === key);
              if (at < 0) return;
              this.at = at;
              setShared("focus", "list"); // a click focuses the row and takes the keys from any text field
              focusMain();
              this.select(cx);
              cx.notify();
            })));
    };
    const d = this.data ?? {};
    const root = v_flex().size_full().min_h(0).gap(6).p(2).bg(t.background).text_size(13);
    if (this.error) return root.child(div().p(12).text_size(12).text_color(t.danger).child(this.error));
    if (this.data && !shown.length) { // All clear, as the Console's #allclear
      return root.child(v_flex().rounded(6).border(1).border_color(t.border).p(10).gap(4)
        .child(h_flex().child(pill(`${I.today} today`, t.blue)))
        .child(h_flex().gap(0).child(div().font_semibold().text_color(t.green).child(`${I.done} All clear`))
          .child(div().text_color(t.foreground).child(": nothing needs you now.")))
        .when(Boolean(d.next_wake), (e) => e.child(div().text_color(t.muted).child(`${I.SNOOZED} Next wake · ${wake(d.next_wake)}`)))
        .when(Boolean(d.next_task), (e) => e.child(div().text_color(t.muted).child(`${I.tasks} Next Task · ${wake(d.next_task)}`))));
    }
    return root.children(shown.map(pane));
  }
}
