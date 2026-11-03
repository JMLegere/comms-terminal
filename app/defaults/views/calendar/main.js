// Calendar, as the Console's CalendarPage (ADR 0005, D91–D97): one scrolling grid of week rows (26 weeks back, 52
// ahead, widening when the cursor reaches an edge) with a separator row where each month starts, opening two weeks
// above this week. A cell shows its date (with "today", and the month on the 1st), up to four Events as start and
// title (? unanswered, ~ maybe, declined dim, all-day blue), "+N more", and a tally of Tasks, Snoozed waking,
// scheduled Draft sends and Enrollment starts. The cursor's day fills the right pane (the day view). h/l move a
// day, j/k a week, { } a month, t today; tab or Enter goes to the day pane, where j/k move and Enter opens. Element
// focus: the cursor's day is the list column's element; it names itself in the bar and carries the path's accent bar.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run } from "comms";

const pad = (n) => String(n).padStart(2, "0");
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const SHORT = MONTHS.map((m) => m.slice(0, 3));
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const LINES = 4; // CAL_LINES
const SEP_H = 24, ROW_H = SEP_H + (LINES + 2) * 17 + 8; // ponytail: one height for every row (each carries a month band)
export const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d, n) => { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); x.setDate(x.getDate() + n); return x; };
const monday = (d) => addDays(d, -((d.getDay() + 6) % 7));

/** Week rows from Monday `start` to `end`, split at each month's first day by a separator row (the Console's cal_blocks). */
export function blocks(start, end) {
  const out = [{ sep: start }];
  for (let day = start; day < end; day = addDays(day, 7)) {
    const week = Array.from({ length: 7 }, (_, i) => addDays(day, i));
    const first = week.find((d) => d.getDate() === 1);
    if (first && first !== week[0]) {
      out.push({ week: week.map((d) => (d < first ? d : null)) }, { sep: first }, { week: week.map((d) => (d >= first ? d : null)) });
    } else {
      if (first && !(out.at(-1).sep && iso(out.at(-1).sep) === iso(first))) out.push({ sep: first });
      out.push({ week });
    }
  }
  return out;
}

/** A cell's date line: "3", "1 Nov", "3 today". */
export const dateLine = (d, today) => `${d.getDate()}${d.getDate() === 1 ? ` ${SHORT[d.getMonth()]}` : ""}${iso(d) === iso(today) ? " today" : ""}`;

export default class Calendar extends View {
  init(_props, cx) {
    this.today = new Date();
    this.cursor = this.today;
    this.start = addDays(monday(this.today), -26 * 7);
    this.end = addDays(monday(this.today), 52 * 7);
    this.data = { days: {} };
    this.opened = false;
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "calendar") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    // the Console's separator rows ride on the week row below them, so every row is one height
    this.blocks = blocks(this.start, this.end).reduce((rows, b) => (b.sep ? rows.push({ label: b.sep, week: null }) && rows
      : (rows.at(-1)?.week === null ? (rows.at(-1).week = b.week) : rows.push({ week: b.week })) && rows), []);
    cx.spawn(async (cx) => {
      try {
        this.data = await call("calendar.page", { start: iso(this.start), end: iso(this.end) });
        this.error = null;
        if (!this.opened && shared("page") === "calendar") { // open on two weeks before this week, once drawn
          this.opened = true;
          cx.timer.after(50, (cx) => this.toTop(this.blockOf(addDays(this.today, -14)), cx));
        }
        this.showDay();
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  /** A row at the top of the grid. The list only scrolls a row into view (from above it lands at the bottom), so
   * this passes the end first and comes back up to it. */
  toTop(index, cx) {
    this.scroll.scroll_to_item((this.blocks ?? []).length - 1, "top");
    cx.notify();
    cx.timer.after(30, (cx) => { this.scroll.scroll_to_item(index, "top"); cx.notify(); });
  }

  blockOf(day) {
    return Math.max(0, this.blocks.findIndex((b) => b.week?.some((d) => d && iso(d) === iso(day))));
  }

  showDay() {
    if (shared("page") !== "calendar") return;
    setShared("cal_day", { day: iso(this.cursor), items: this.data.days?.[iso(this.cursor)]?.items ?? [] });
    run("day.show");
    this.target();
  }

  /** The bar's line for the cursor's day (element focus): which day and the keys it takes. */
  target() {
    if (shared("page") !== "calendar" || shared("focus") === "detail") return;
    const d = this.cursor;
    setShared("target", { col: "list", label: `Day · ${DAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}`,
      keys: [["enter", "its Calendar Items"], ["h/l", "day"], ["j/k", "week"], ["{ }", "month"], ["t", "today"]] });
  }

  setCursor(day, cx) {
    this.cursor = day;
    if (day < this.start || day >= this.end) { // the edge of the drawn window: widen it and draw again
      this.start = monday(addDays(day < this.start ? day : this.start, day < this.start ? -26 * 7 : 0));
      this.end = day >= this.end ? addDays(day, 26 * 7) : this.end;
      this.load(cx);
    } else this.showDay();
    this.scroll.scroll_to_item(this.blockOf(day), "center");
    cx.notify();
  }

  command(c, cx) {
    const inDay = shared("focus") === "detail";
    const move = { "prev_day": -1, "next_day": 1, "prev_week": -7, "next_week": 7 }[c];
    if (inDay && (c === "next_week" || c === "prev_week")) return run(c === "next_week" ? "day.next" : "day.prev"); // j/k in the day pane
    if (move) return this.setCursor(addDays(this.cursor, move), cx);
    const first = new Date(this.cursor.getFullYear(), this.cursor.getMonth(), 1);
    switch (c) {
      case "show": // each visit opens as the Console's new MonthGrid: cursor on today, two weeks above this week on top
        this.today = this.cursor = new Date();
        this.opened = false;
        return this.load(cx);
      case "prev_month": return this.setCursor(new Date(first.getFullYear(), first.getMonth() - 1, 1), cx);
      case "next_month": return this.setCursor(new Date(first.getFullYear(), first.getMonth() + 1, 1), cx);
      case "today":
        this.setCursor(new Date(), cx);
        return this.toTop(this.blockOf(addDays(new Date(), -14)), cx);
      case "focus_entry": this.target(); return cx.notify();
      // into the day's Calendar Items and back: nav moves the focus, so the entered column names its element
      case "tab": return run(inDay ? "nav.focus_left" : "nav.focus_right");
      case "enter": return run(inDay ? "day.open" : "nav.focus_right");
      case "escape": return run("nav.back"); // the day pane back to the grid, then back a page
    }
  }

  render() {
    const t = theme();
    const today = this.today, cursor = iso(this.cursor), days = this.data.days ?? {};
    const focusHere = (shared("focus") ?? "list") === "list";
    const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
    const color = (hex) => (hex ? t[GREY[hex]] ?? (hex === "#45707a" ? t.blue : hex === "#654735" ? t.foreground : hex) : t.foreground);
    const cell = (d) => {
      if (!d) return div().flex_1().min_w(0);
      const k = iso(d), info = days[k], on = k === cursor;
      const line = (text, c, bold) => div().h(17).truncate().text_color(c).when(Boolean(bold), (e) => e.font_semibold()).child(text);
      const ev = (info?.events ?? []).map((e) => line(e.text, color(e.color)));
      // The path (Logseq bullet threading): the cursor's day has a full bar and highlight; while the focus is further
      // in (the day's Calendar Items), it keeps a thin bar. No handlers in a virtual list's rows.
      return v_flex().flex_1().min_w(0).h_full().px(4).py(2).rounded(4).border_l(3).border_color(on ? t.accent : t.background)
        .when(on && focusHere, (e) => e.bg(t.selected)).when(on && !focusHere, (e) => e.border_l(1))
        .child(line(dateLine(d, today), k === iso(today) ? t.blue : on ? t.strong ?? t.foreground : t.muted, true))
        .children(ev).when((info?.more ?? 0) > 0, (e) => e.child(line(`+${info.more} more`, t.muted)))
        .child(div().flex_1()).child(line(info?.tally ?? "", t.muted));
    };
    const row = (b) => v_flex().w_full().h(ROW_H)
      .child(h_flex().h(SEP_H).px(4).items_center().gap(6).when(Boolean(b.label), (e) => e
        .child(div().font_semibold().text_color(t.blue).child(`${MONTHS[b.label.getMonth()]} ${b.label.getFullYear()}`))
        .child(div().flex_1().h(1).bg(t.blue))))
      .child(h_flex().w_full().flex_1().items_start().gap(2).py(2).border_b(1).border_color(t.border).children((b.week ?? []).map(cell)));
    const list = this.blocks ?? [];
    const banner = this.data.banner;
    return v_flex().size_full().min_h(0).bg(t.background).text_size(12)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border)
        .child(div().text_color(t.accent).child("\u{f0b67}"))
        .child(div().font_medium().text_color(t.foreground).child("calendar")))
      .when(Boolean(banner), (el) => el.child(v_flex().px(12).py(6).border_b(1).border_color(t.border)
        .children((banner ?? []).map((segs) => h_flex().flex_wrap()
          .children(segs.map(([text, hex, bold]) => div().min_w(0).text_color(color(hex)).when(Boolean(bold), (d) => d.font_semibold()).child(text)))))))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_color(t.danger).child(this.error ?? "")))
      .child(div().flex_1().min_h(0).px(4).child(
        v_virtual_list("cal", list.length, ROW_H, (i) => `w-${i}`,
          (range) => list.slice(range.start, range.end).map(row)).size_full().track_scroll(this.scroll)));
  }
}
