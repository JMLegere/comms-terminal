// The Action Queue, as the Console's ActionQueuePage (ADR 0022, PRD 2026-11-08-action-queue FR-AQT-9, -11): every
// Action that waits for a time, as one timeline: Upcoming furthest ahead first, In flight, then Sent from the last
// 7 days. Each day counts its Actions by kind. The detail pane shows the record behind the selected row (a Draft,
// Thread, Task or Person); Enter moves to it. It opens at now.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain } from "comms";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const SECTION = { upcoming: "Upcoming", in_flight: "In flight", sent: "Sent" };
const OUTCOME = { confirmed: "green", refused: "red", unknown: "yellow" };
const MARK = { confirmed: "✓", refused: "✗", unknown: "?" };
const pad = (n) => String(n).padStart(2, "0");

const EMPTY = { upcoming: "Nothing scheduled", in_flight: "Nothing in flight", sent: "Nothing sent in the last 7 days" };

/** The timeline's items (PRD 2026-11-08-action-queue, hierarchy): three sections, always shown, each a band with its
 * count; a muted header per day with its counts by kind and the rows under it; a NOW bar between the future
 * (Upcoming) and the rest. */
export function queueItems(rows, counts, now = new Date()) {
  const items = [];
  for (const section of Object.keys(SECTION)) {
    const mine = rows.filter((r) => r.section === section);
    items.push({ key: `s-${section}`, band: section,
      header: `${SECTION[section].toUpperCase()} · ${section === "sent" ? "last 7 days · " : ""}${mine.length}` });
    if (!mine.length) items.push({ key: `e-${section}`, empty: EMPTY[section] });
    let day = null;
    for (const r of mine) {
      if (r.day !== day) {
        day = r.day;
        const [y, m, d] = day.split("-").map(Number), at = new Date(y, m - 1, d);
        items.push({ key: `d-${section}-${day}`, header: `${DAYS[at.getDay()]} ${MONTHS[at.getMonth()]} ${d}`,
          counts: Object.entries(counts?.[day] ?? {}).map(([k, n]) => `${k} ${n}`).join(" · ") });
      }
      items.push({ key: r.key, row: r });
    }
    if (section === "upcoming") items.push({ key: "now", now: `NOW ${pad(now.getHours())}:${pad(now.getMinutes())}` });
  }
  return items;
}

/** Where the cursor starts: the first row at or after now (In flight, else the first Sent row). */
export function nowIndex(rows) {
  const i = rows.findIndex((r) => r.section !== "upcoming");
  return i < 0 ? Math.max(rows.length - 1, 0) : i;
}

export default class Actions extends View {
  init(_props, cx) {
    this.rows = [];
    this.counts = {};
    this.at = null; // null: open at now once rows arrive
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "actions") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const q = await call("actions.queue");
        const key = this.rows[this.at]?.key;
        this.rows = q.rows;
        this.counts = q.counts;
        const kept = key ? this.rows.findIndex((r) => r.key === key) : -1;
        this.at = kept >= 0 ? kept : nowIndex(this.rows);
        this.error = null;
        this.show();
        if (kept < 0 && this.rows.length) { // opens at now: the selected row in the middle of the pane
          const i = queueItems(this.rows, this.counts).findIndex((it) => it.key === this.rows[this.at].key);
          cx.timer.after(30, (cx) => { this.scroll.scroll_to_item(i, "center"); cx.notify(); });
        }
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  /** The detail pane shows the record behind the selected row. */
  show() {
    const r = this.rows[this.at];
    if (!r || shared("page") !== "actions") return;
    if (r.target_kind) setShared(r.target_kind, r.target_id);
    setShared("detail", r.target_kind ?? null);
    if (r.target_kind) run(`${r.target_kind}.show`); // the detail view loads the record now, as Today does
    setShared("target", { col: "list", label: `${r.what} · ${r.target ?? ""}`, keys: r.target_kind ? [["enter", "open"]] : [] });
  }

  command(c, cx) {
    const n = this.rows.length;
    switch (c) {
      case "show": return this.load(cx);
      case "focus_entry": return this.show();
      case "next": case "prev":
        this.at = Math.max(0, Math.min(n - 1, (this.at ?? 0) + (c === "next" ? 1 : -1)));
        this.show();
        return cx.notify();
      case "enter": {
        const r = this.rows[this.at];
        if (r?.target_kind) setShared("focus", "detail");
        return cx.notify();
      }
    }
  }

  render() {
    const t = theme();
    const items = queueItems(this.rows, this.counts);
    const selected = this.rows[this.at];
    const band = (it) => h_flex().h(26).px(8).gap(8).items_center().border_b(1).border_color(t.border)
      .child(div().text_size(12).font_semibold().text_color(t.foreground).child(it.header));
    const empty = (it) => h_flex().h(26).pl(24).items_center().child(div().text_color(t.dim).child(it.empty));
    const dayHeader = (it) => h_flex().h(26).pl(16).pr(8).items_center().text_size(11.5)
      .child(div().flex_1().text_color(t.muted).child(it.header))
      .child(div().flex_none().text_color(t.dim).child(it.counts ?? ""));
    const nowLine = (it) => h_flex().h(26).px(8).gap(8).items_center()
      .child(div().flex_none().text_size(11).font_semibold().text_color(t.accent).child(it.now))
      .child(div().flex_1().h(2).bg(t.accent));
    const row = (r) => {
      const at = new Date(r.at), past = r.section === "sent";
      return h_flex().h(26).pl(24).pr(8).gap(8).rounded(4).items_center()
        .border_l(3).border_color(r === selected ? t.accent : t.background).when(r === selected, (el) => el.bg(t.selected))
        .child(div().w(40).flex_none().text_color(t.muted).child(`${pad(at.getHours())}:${pad(at.getMinutes())}`))
        .child(div().w(12).flex_none().text_color(t[OUTCOME[r.outcome]] ?? t.muted).child(MARK[r.outcome] ?? ""))
        .child(div().flex_none().text_color(past ? t.muted : t.foreground).child(r.what)) // the target keeps the width
        .child(div().flex_1().min_w(0).truncate().text_color(past ? t.muted : t.strong ?? t.foreground).child(r.target ?? ""));
    };
    const item = (it) => it.now ? nowLine(it) : it.band ? band(it) : it.empty ? empty(it) : it.header ? dayHeader(it) : row(it.row);
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f048a}"))
        .child(div().font_medium().text_color(t.foreground).child("action queue")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("actions", items.length, 26, (i) => items[i].key,
          (range) => items.slice(range.start, range.end).map(item))
          .size_full().track_scroll(this.scroll).on_item_click((key, cx) => {
            const at = this.rows.findIndex((r) => r.key === key);
            if (at < 0) return;
            this.at = at;
            setShared("focus", "list");
            focusMain();
            this.show();
            cx.notify();
          })));
  }
}
