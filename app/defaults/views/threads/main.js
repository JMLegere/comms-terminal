// One Status view's Threads, as the Console's ThreadsPage (ADR 0014): Status header rows in the legend's order
// when the view holds more than one Status, Case header rows inside them, one-line rows (glyph, who · where,
// title with a pencil when a reply is saved, time), and the legend under the list. j/k move over every row;
// Enter on a Thread moves focus into it, Enter or a click on a Case header opens the Case.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain, keys } from "comms";

// Status detail: Nerd Font glyph (icons.py), colour token, label (inbox.DETAIL_LABEL).
const GLYPH = {
  CREATED: ["\u{f0674}", "status_needs_first", "Needs first response"],
  NEW_REPLY: ["\u{f173e}", "status_needs_next", "Needs next response"],
  IN_PROGRESS: ["\u{f0349}", "status_investigating", "Investigating"],
  REPLY_DRAFT: ["\u{f048a}", "status_reply_draft", "Reply Draft"],
  WAITING_FOR_CUSTOMER: ["\u{f051f}", "status_waiting", "Waiting for them"],
  WAITING_FOR_DURATION: ["\u{f03e6}", "status_waiting", "Paused for later"],
  WAITING_INDEFINITELY: ["\u{f03e6}", "status_waiting", "Paused for later"],
  DONE_MANUALLY_SET: ["\u{f012c}", "status_done", "Done"],
  IGNORED: ["\u{f06d1}", "status_ignored", "Ignored"],
};
const VIEWS = { // inbox.VIEWS
  Todo: ["CREATED", "NEW_REPLY", "IN_PROGRESS", "REPLY_DRAFT"],
  Snoozed: ["WAITING_FOR_CUSTOMER", "WAITING_FOR_DURATION", "WAITING_INDEFINITELY"],
  Done: ["DONE_MANUALLY_SET"],
  Ignored: ["IGNORED"],
};
VIEWS.All = Object.values(VIEWS).flat();
const VIEW_GLYPH = { Todo: "\u{f0766}", Snoozed: "\u{f04b2}", Done: "\u{f05e1}", Ignored: "\u{f06d1}", All: "\u{f0822}" };
const EMPTY = { // the Console's ThreadsPage.EMPTY
  Todo: "All caught up: nothing needs a reply.",
  Snoozed: "Nothing snoozed or waiting. Press g on a thread to snooze it.",
  Done: "Nothing done yet. Press e on a thread when it needs nothing more.",
  Ignored: "Nothing ignored. Press m on a thread to stop it coming back.",
  All: "No threads yet. They arrive with your next email or Slack message.",
};
const CASE = "\u{f0814}", EDITED = "\u{f0cb6}";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

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

/** Time left until a snooze ends, to the second, as the Console's left(): 5m24s, 3h0m5s; now; — with no end. */
export function left(iso, now = Date.now()) {
  if (!iso) return "—";
  const secs = Math.floor((new Date(iso) - now) / 1000);
  if (secs <= 0) return "now";
  const parts = [[Math.floor(secs / 86400), "d"], [Math.floor(secs % 86400 / 3600), "h"], [Math.floor(secs % 3600 / 60), "m"], [secs % 60, "s"]];
  while (parts[0][0] === 0) parts.shift();
  return parts.map(([n, u]) => `${n}${u}`).join("");
}

const caseCounts = (counts) => Object.entries(counts ?? {}).filter(([, n]) => n).map(([v, n]) => `${n} ${v.toLowerCase()}`).join(" · ") || "no threads yet";

/** Status headers, then Case headers inside each (the Console's grouped and by_case); each item has a depth. */
export function grouped(view, rows, cases) {
  const labels = [...new Set(VIEWS[view].map((d) => GLYPH[d][2]))];
  const byCase = (rs, depth, under) => {
    if (!rs.some((r) => r.case_id)) return rs.map((r) => ({ row: r, depth }));
    const groups = new Map();
    for (const r of rs) {
      const cid = r.case_id ?? null;
      if (!groups.has(cid)) groups.set(cid, []);
      groups.get(cid).push(r);
    }
    const oldest = (c) => groups.get(c).map((r) => r.waiting_since ?? "").sort()[0];
    const order = [...groups.keys()].sort((a, b) => (a !== null) - (b !== null) || (oldest(a) < oldest(b) ? -1 : oldest(a) > oldest(b) ? 1 : 0));
    return order.flatMap((cid) => {
      const n = groups.get(cid).length, c = cases[cid];
      return [{ caseHeader: { id: cid, under, title: c?.title ?? "", summary: cid ? caseCounts(c?.counts) : `${n} thread${n === 1 ? "" : "s"}` }, depth },
        ...groups.get(cid).map((r) => ({ row: r, depth: depth + 1 }))];
    });
  };
  if (labels.length === 1) return byCase(rows, 0);
  return labels.flatMap((label) => {
    const group = rows.filter((r) => GLYPH[r.detail]?.[2] === label);
    if (!group.length) return [];
    const detail = VIEWS[view].find((d) => GLYPH[d][2] === label);
    return [{ statusHeader: { detail, label, count: group.length }, depth: 0 }, ...byCase(group, 1, detail)];
  });
}

const keyOf = (it) => it.row ? `row-${it.row.id}` : it.caseHeader ? `case-${it.caseHeader.under ?? ""}-${it.caseHeader.id}` : `status-${it.statusHeader.detail}`;

export default class Threads extends View {
  init(_props, cx) {
    this.items = [];
    this.cases = {};
    this.hi = null; // the selected row's key; a header row selects no Thread
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    this.tick(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  /** Snoozed rows count down every second, as the Console's tick. */
  tick(cx) {
    cx.timer.after(1000, (cx) => {
      if ((shared("page") ?? "threads") === "threads" && ["Snoozed", "All"].includes(shared("view"))) cx.notify();
      this.tick(cx);
    });
  }

  load(cx) {
    const view = shared("view") ?? "Todo";
    cx.spawn(async (cx) => {
      try {
        const [rows, cases] = await Promise.all([call("threads.queue", { view }), call("cases.list")]);
        this.cases = Object.fromEntries((cases ?? []).map((c) => [c.id, c]));
        const was = this.items.findIndex((it) => keyOf(it) === this.hi);
        this.items = grouped(view, rows ?? [], this.cases);
        this.view = view;
        this.error = null;
        if ((shared("page") ?? "threads") !== "threads") return cx.notify();
        const keys = this.items.map(keyOf);
        const pending = this.pending && `row-${this.pending}`;
        this.pending = null;
        if (pending && keys.includes(pending)) this.pick(pending);
        else if (shared("thread") && keys.includes(`row-${shared("thread")}`)) this.hi = `row-${shared("thread")}`;
        else if (!keys.includes(this.hi)) { // the row left this view: the one that took its place
          const rows = this.items.map((it, i) => [it, i]).filter(([it]) => it.row);
          const at = rows.find(([, i]) => i >= was) ?? rows.at(-1);
          this.pick(at ? keyOf(at[0]) : null);
        }
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  pick(key) {
    this.hi = key;
    const it = this.items.find((x) => keyOf(x) === key);
    setShared("thread", it?.row?.id ?? null);
    run("thread.show");
    const at = this.items.findIndex((x) => keyOf(x) === key);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
    this.target();
  }

  /** The bar's line for the focused row (element focus): the Thread and the keys its page binds for it. */
  target() {
    if (shared("page") !== "threads" || shared("focus") === "detail") return;
    const it = this.items.find((x) => keyOf(x) === this.hi);
    const bound = Object.entries((keys() ?? {})["page:threads"] ?? {})
      .filter(([k, c]) => k.length === 1 && c.startsWith("thread.")).map(([k, c]) => [k, c.split(".")[1].replace("reply_sender", "reply").replace(/_/g, " ")]);
    const label = it?.row ? `Thread · ${it.row.title ?? ""}` : it?.caseHeader ? `Case · ${it.caseHeader.title || "No case"}` : it?.statusHeader?.label ?? "";
    setShared("target", { col: "list", label, keys: it?.row ? [["enter", "open"], ...bound] : [["enter", "open"]] });
  }

  /** The Thread below the selection, else the one above: where it lands once this one moves (next_thread_id). */
  nextThread() {
    const i = this.items.findIndex((x) => keyOf(x) === this.hi);
    return this.items.slice(i + 1).find((x) => x.row)?.row.id
      ?? this.items.slice(0, Math.max(i, 0)).reverse().find((x) => x.row)?.row.id ?? null;
  }

  openCase(c) {
    if (!c?.id) return;
    setShared("push", { page: "case", case: c.id });
    run("nav.push");
  }

  command(c, cx) {
    if (c === "reload" || c === "show") return this.load(cx);
    if (c === "advance") { // s: the cursor moves on, as after Done (D32); only on its own page, else it waits stale
      if ((shared("page") ?? "threads") === "threads") this.pending = this.nextThread();
      return;
    }
    const i = this.items.findIndex((x) => keyOf(x) === this.hi);
    if (c === "enter") {
      const it = this.items[i];
      if (it?.caseHeader) return this.openCase(it.caseHeader);
      if (it?.row) run("nav.focus_right"); // into the Thread: its newest message takes the focus
      return;
    }
    if (c === "focus_entry") return this.target();
    if (c !== "next" && c !== "prev") return;
    const it = this.items[Math.max(0, Math.min(this.items.length - 1, i + (c === "next" ? 1 : -1)))];
    if (it) this.pick(keyOf(it));
    cx.notify();
  }

  render() {
    const t = theme();
    const view = this.view ?? shared("view") ?? "Todo";
    const items = this.items;
    const count = items.filter((it) => it.row).length;
    const focusHere = (shared("focus") ?? "list") === "list";
    const glyph = (detail) => div().w(16).flex_none().text_color(t[GLYPH[detail]?.[1]] ?? t.dim).child(GLYPH[detail]?.[0] ?? "·");
    const line = (it) => {
      const key = keyOf(it);
      const el = h_flex().id(key).h(26).pl(8 + 14 * it.depth).pr(8).gap(8).rounded(4).items_center().cursor_pointer()
        .hover((s) => s.bg(t.hover))
        // The path (Logseq bullet threading): full bar and highlight on the focused row, a thin bar while the focus
        // is further in (the Thread).
        .border_l(3).border_color(key === this.hi ? t.accent : t.background)
        .when(key === this.hi && focusHere, (e) => e.bg(t.selected))
        .when(key === this.hi && !focusHere, (e) => e.border_l(1));
      if (it.statusHeader) {
        const h = it.statusHeader, color = t[GLYPH[h.detail][1]] ?? t.dim;
        return el.child(glyph(h.detail)).child(div().flex_1().font_semibold().text_color(color).child(h.label))
          .child(div().flex_none().text_color(t.dim).child(String(h.count)));
      }
      if (it.caseHeader) {
        const h = it.caseHeader;
        return el.child(div().w(16).flex_none().text_color(t.foreground).child(h.id ? CASE : ""))
          .child(div().flex_none().max_w(260).truncate().font_semibold().text_color(h.id ? t.blue ?? t.accent : t.dim).child(h.id ? h.title : "No case"))
          .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(h.summary));
      }
      const r = it.row;
      return el.child(glyph(r.detail))
        .child(div().flex_none().max_w(220).truncate().font_semibold().text_color(t.strong ?? t.foreground).child(r.who ?? ""))
        .when(Boolean(r.where), (e) => e.child(div().flex_shrink(1).min_w(0).max_w(160).truncate().text_color(t.dim).child(`· ${r.where}`)))
        .when(Boolean(r.drafted), (e) => e.child(div().flex_none().text_color(t.yellow ?? t.status_needs_next).child(EDITED)))
        .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(r.title ?? ""))
        .child(div().flex_none().text_color(t.dim).child(r.status === "SNOOZED" ? left(r.until) : when(r.last_at)));
    };
    const legend = [...new Map(VIEWS[view].map((d) => [GLYPH[d][2], d])).values()];
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child(VIEW_GLYPH[view] ?? ""))
        .child(div().font_medium().text_color(t.foreground).child(`threads · ${view.toLowerCase()}`))
        .child(div().text_color(t.dim).child(`· ${count}`)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && count === 0, (el) => el.child(div().p(16).text_color(t.dim).child(EMPTY[view] ?? "")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("rows", items.length, 26, (i) => keyOf(items[i]), (range) => items.slice(range.start, range.end).map(line))
          .size_full()
          .track_scroll(this.scroll)
          .on_item_click((key, cx) => {
            const it = items.find((x) => keyOf(x) === key);
            if (it?.caseHeader) return this.openCase(it.caseHeader); // a click on a Case header opens the Case
            setShared("focus", "list"); // a click focuses the row and takes the keys from any text field
            focusMain();
            this.pick(key);
            cx.notify();
          })))
      .child(h_flex().flex_none().px(12).py(6).gap(12).flex_wrap().border_t(1).border_color(t.border).text_size(11.5)
        .children(legend.map((d) => h_flex().gap(4).child(div().text_color(t[GLYPH[d][1]] ?? t.dim).child(GLYPH[d][0]))
          .child(div().text_color(t.dim).child(GLYPH[d][2].toLowerCase())))));
  }
}
