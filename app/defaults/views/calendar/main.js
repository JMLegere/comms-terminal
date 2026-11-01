// Calendar: a month of days with their Event counts, and the chosen day's Events below. h/l move a day, j/k a
// week, { } a month, t today; n/p pick the day's next or previous Event, which fills the right pane.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run } from "comms";

const pad = (n) => String(n).padStart(2, "0");
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
// The script engine has no Intl, so dates are spelled out here, in local time.
const day = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const time = (iso) => new Date(iso).toTimeString().slice(0, 5);

export default class Calendar extends View {
  init(_props, cx) {
    this.day = new Date();
    this.events = [];
    this.at = 0;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "calendar") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  grid() {
    const first = new Date(this.day.getFullYear(), this.day.getMonth(), 1);
    const start = new Date(first);
    start.setDate(1 - ((first.getDay() + 6) % 7)); // Monday first
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
  }

  load(cx) {
    const cells = this.grid();
    const end = new Date(cells[41]);
    end.setDate(end.getDate() + 1);
    cx.spawn(async (cx) => {
      try {
        this.events = await call("calendar.events", { start: day(cells[0]), end: day(end) });
        this.error = null;
        this.pick(cx);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  today() {
    return this.events.filter((e) => day(new Date(e.start)) <= day(this.day) && day(this.day) <= day(new Date(new Date(e.end).getTime() - 1)));
  }

  pick() {
    const list = this.today();
    this.at = Math.max(0, Math.min(this.at, list.length - 1));
    if (shared("page") !== "calendar") return;
    setShared("event", list[this.at] ?? null);
    run("event.show");
  }

  command(c, cx) {
    const move = { prev_day: -1, next_day: 1, prev_week: -7, next_week: 7 }[c];
    const month = this.day.getMonth();
    if (c === "show") return this.load(cx);
    if (move) this.day.setDate(this.day.getDate() + move);
    else if (c === "prev_month" || c === "next_month") this.day = new Date(this.day.getFullYear(), month + (c === "next_month" ? 1 : -1), 1);
    else if (c === "today") this.day = new Date();
    else if (c === "next" || c === "prev") this.at += c === "next" ? 1 : -1;
    else return;
    this.day = new Date(this.day);
    if (c !== "next" && c !== "prev") this.at = 0;
    if (this.day.getMonth() !== month) return this.load(cx);
    this.pick();
    cx.notify();
  }

  render() {
    const t = theme();
    const cells = this.grid();
    const counts = {};
    for (const e of this.events) counts[day(new Date(e.start))] = (counts[day(new Date(e.start))] ?? 0) + 1;
    const chosen = day(this.day), now = day(new Date());
    const list = this.today();
    const selected = shared("event")?.id;
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/calendar.svg").size(13).text_color(t.accent))
        .child(div().flex_1().font_medium().text_color(t.foreground).child(`${MONTHS[this.day.getMonth()]} ${this.day.getFullYear()}`))
        .child(div().text_size(11).text_color(t.dim).child("h/l day · j/k week · { } month · t today")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .child(v_flex().px(10).pt(8).gap(2).flex_none()
        .child(h_flex().children(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) =>
          div().flex_1().text_size(10.5).text_color(t.dim).child(d))))
        .children([0, 1, 2, 3, 4, 5].map((w) => h_flex().gap(2).children(cells.slice(w * 7, w * 7 + 7).map((d) => {
          const key = day(d);
          return v_flex().id(`d-${key}`).flex_1().h(38).px(4).py(2).rounded(6).cursor_pointer()
            .when(key === chosen, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
            .on_click((_e, cx) => { this.day = d; this.at = 0; this.pick(); cx.notify(); })
            .child(div().text_size(11.5).text_color(d.getMonth() !== this.day.getMonth() ? t.dim : key === now ? t.accent : t.foreground)
              .when(key === now, (el) => el.font_semibold()).child(String(d.getDate())))
            .when(Boolean(counts[key]), (el) => el.child(div().text_size(10).text_color(t.muted).child(`${counts[key]} event${counts[key] === 1 ? "" : "s"}`)));
        })))))
      .child(div().px(14).pt(10).pb(4).text_size(11.5).font_medium().text_color(t.dim)
        .child(`${DAYS[this.day.getDay()]}, ${MONTHS[this.day.getMonth()]} ${this.day.getDate()}`))
      .child(v_flex().id("day").flex_1().min_h(0).overflow_y_scroll().px(6).pb(8)
        .when(!list.length, (el) => el.child(div().px(8).text_color(t.dim).child("No Events.")))
        .children(list.map((e, n) => h_flex().id(`e-${e.id}`).h(34).px(8).gap(9).rounded(8).items_center().cursor_pointer()
          .when(e.id === selected, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
          .on_click((_e, cx) => { this.at = n; this.pick(); cx.notify(); })
          .child(div().w(84).flex_none().text_size(11.5).text_color(t.muted).child(e.all_day ? "all day" : `${time(e.start)}–${time(e.end)}`))
          .child(div().flex_1().min_w(0).truncate().text_color(e.state === "DECLINED" ? t.dim : t.foreground).child(e.title))
          .child(div().flex_none().text_size(11).text_color(t.dim).child((e.state ?? "").toLowerCase().replace("_", " "))))));
  }
}
