// Today (GLOSSARY), as the Console's TodayPage (ADR 0014): five bordered panes stacked in order, each with its pill
// title — threads (a Threads list fixed to Todo: Status and Case headers), tasks (OVERDUE / DUE TODAY / LATER TODAY,
// with a red "N overdue"), drafts, paused and unattended; an empty pane hides and the last one shown takes the spare
// height. ↓/↑ (j/k) run on from one pane into the next, tab jumps to the next pane, and the row under the cursor fills
// the right pane (shared "detail" picks its view). A key acts on the row's own kind: r replies on a Thread and
// resumes a Paused Enrollment; x ignores a Thread, skips a Task and deletes a Draft. Thread keys reach only a Thread
// row: any other row clears the shared Thread, so a key never reaches a Thread that is not on screen (D87).
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const GLYPH = { // Status detail: glyph, colour token, label (as threads/main.js)
  CREATED: ["\u{f0674}", "status_needs_first", "Needs first response"], NEW_REPLY: ["\u{f173e}", "status_needs_next", "Needs next response"],
  IN_PROGRESS: ["\u{f0349}", "status_investigating", "Investigating"], REPLY_DRAFT: ["\u{f048a}", "status_reply_draft", "Reply Draft"],
};
const TODO = ["CREATED", "NEW_REPLY", "IN_PROGRESS", "REPLY_DRAFT"];
const I = { TODO: "\u{f0766}", tasks: "\u{f05c7}", edited: "\u{f0cb6}", sequence: "\u{f027b}", organization: "\u{f01d7}",
  today: "\u{f05a8}", done: "\u{f012c}", SNOOZED: "\u{f04b2}", warn: "\u{f002a}", case: "\u{f0814}",
  email: "\u{f01f0}", slack: "\u{f04b1}", sms: "\u{f011c}", time: "\u{f0150}",
  call: "\u{f0df0}", linkedin: "\u{f033b}", letter: "\u{f0d8c}", action: "\u{f027b}", if: "\u{f0641}", meeting: "\u{f0ed8}" };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");
const PANES = ["threads", "tasks", "drafts", "paused", "unattended"];
const DETAIL = { threads: null, tasks: "task", drafts: "draft", paused: "paused", unattended: "unattended" };
// What each key does on each kind of row (the Console's Today hands a key to the list that holds the cursor).
const THREAD_KEYS = { r: "reply_sender", a: "reply_all", e: "done", g: "snooze", b: "block", m: "ignore", x: "ignore", l: "case",
  s: "status", p: "person", o: "link", y: "copy", f: "forward", v: "react", i: "respond", delete: "unstage" };
const ROUTE = {
  threads: Object.fromEntries(Object.entries(THREAD_KEYS).map(([k, c]) => [k, `thread.${c}`])),
  tasks: { e: "tasks.done", x: "tasks.skip", n: "tasks.new_thread", p: "tasks.person", y: "tasks.copy", "1": "tasks.pick_first",
    "2": "tasks.pick_second" },
  drafts: { n: "drafts.new", x: "drafts.delete", y: "drafts.copy", "ctrl-s": "draft.save", "ctrl-enter": "draft.send" },
  paused: { "r": "person.resume", "f": "person.finish" },
  unattended: {},
};
const KEYS = [...new Set(Object.values(ROUTE).flatMap(Object.keys))];

/** Compact age, as the Console's when(): now, 5m, 3h, 2d, then a date. */
export function when(iso, now = Date.now()) {
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
  for (const k of ["drafts", "paused", "unattended"]) out[k] = (d[k] ?? []).map((r) => ({ row: r, pick: true }));
  const rank = (r) => r.send_error ? "0" : r.send_at ? `2${r.send_at}` : "1"; // the Drafts order (drafts/main.js)
  out.drafts.sort((a, b) => (rank(a.row) < rank(b.row) ? -1 : rank(a.row) > rank(b.row) ? 1 : 0));
  for (const k of PANES) out[k].forEach((it, i) => { it.pane = k; it.key = keyOf(k, it, i); });
  return out;
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
    return paneItems(this.data, this.cases);
  }

  /** The rows the cursor visits, pane after pane. */
  rows() {
    const p = this.panes();
    return PANES.flatMap((k) => p[k].filter((it) => it.pick)).map((it) => ({ ...it, kind: it.pane, r: it.row }));
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const keep = this.rows()[this.at]?.key;
        const [data, cases] = await Promise.all([call("today.page"), call("cases.list").catch(() => [])]);
        this.data = data;
        this.cases = Object.fromEntries((cases ?? []).map((c) => [c.id, c]));
        const rows = this.rows();
        const again = rows.findIndex((x) => x.key === keep);
        const first = Math.max(0, rows.findIndex((x) => x.r)); // on arrival: the first Thread, not its header
        this.at = again >= 0 ? again : keep ? Math.min(this.at, Math.max(rows.length - 1, 0)) : first;
        this.error = null;
        this.select(cx);
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

  /** tab: the first row of the next pane that has one, round to the first. */
  nextPane(cx) {
    const rows = this.rows();
    const here = rows[this.at]?.kind;
    const at = rows.findIndex((x, i) => i > this.at && x.kind !== here);
    this.at = at >= 0 ? at : 0;
    this.select(cx);
    cx.notify();
  }

  select(cx) {
    const row = this.rows()[this.at];
    if (!row || shared("page") !== "today") return;
    const r = row.r;
    setShared("thread", row.kind === "threads" ? r?.id ?? null : null);
    setShared("detail", DETAIL[row.kind]);
    if (row.kind === "threads") run("thread.show");
    if (row.kind === "tasks") { setShared("task", r.id); run("task.show"); run("tasks.show"); }
    if (row.kind === "drafts") { setShared("draft", r.id); run("draft.show"); }
    if (row.kind === "paused") { setShared("person", r.person_id); setShared("side", { kind: "paused", id: r.person_id }); run("person.show"); run("side.show"); }
    if (row.kind === "unattended") {
      setShared("organization", { id: r.id, domain: r.domain });
      setShared("side", { kind: "unattended", id: r.id });
      run("organization.show"); run("side.show");
    }
    const items = this.panes()[row.kind];
    this.scrolls?.[row.kind]?.scroll_to_item(Math.max(0, items.findIndex((it) => it.key === row.key)), "center");
  }

  enter(cx) {
    const row = this.rows()[this.at];
    if (!row) return;
    const r = row.r;
    const push = (entry) => { setShared("push", entry); run("nav.push"); };
    switch (row.kind) {
      case "threads":
        if (row.caseHeader) return row.caseHeader.id && push({ page: "case", case: row.caseHeader.id });
        if (r) { setShared("focus", "detail"); cx.notify(); }
        return;
      case "tasks": return run("task.open");
      case "drafts":
        if (!r.thread_id) return run("draft.edit");
        return cx.spawn(async () => openReply(r.thread_id).catch((e) => toast(String(e), "error")));
      case "paused": return push({ page: "person", person: r.person_id });
      case "unattended": return push({ page: "organization", organization: { id: r.id, domain: r.domain } });
    }
  }

  command(c, cx) {
    if (c === "show") return this.load(cx);
    if (c === "next" || c === "prev") return this.move(c === "next" ? 1 : -1, cx);
    if (c === "tab") return this.nextPane(cx);
    if (c === "enter") return this.enter(cx);
    if (KEYS.includes(c)) { // a key goes to the row's own kind, else nowhere
      const kind = this.rows()[this.at]?.kind;
      const to = kind && ROUTE[kind][c];
      if (to) run(to);
    }
  }

  render() {
    const t = theme();
    const panes = this.panes();
    const counts = Object.fromEntries(PANES.map((k) => [k, panes[k].filter((it) => it.row).length]));
    const shown = PANES.filter((k) => counts[k]);
    const sel = this.rows()[this.at]?.key;
    const pill = (text, color) => div().flex_none().px(6).rounded(4).bg(color).text_color(t.background).font_semibold().child(text);
    const title = {
      threads: [pill(`${I.TODO} threads · ${counts.threads}`, t.blue)],
      tasks: [pill(`${I.tasks} tasks · ${counts.tasks}`, t.blue), ...(() => {
        const late = (this.data?.tasks ?? []).filter((x) => x.group === "overdue").length;
        return late ? [div().font_semibold().text_color(t.red).child(`${late} overdue`)] : [];
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
        .when(it.key === sel, (e) => e.bg(t.selected));
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
              if (at >= 0) { this.at = at; this.select(cx); cx.notify(); }
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

/** A Reply Draft's Thread where it lives, selected, with its reply box open (the Console's open_reply). */
async function openReply(threadId) {
  const th = (await call("thread.view", { thread_id: threadId })).view.thread, v = th?.f ?? th;
  const view = v.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[v.status] ?? "todo";
  setShared("thread", threadId);
  run(`nav.${view}`);
  run("thread.show");
  run("thread.open_reply");
}
