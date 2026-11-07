// The Action Queue, as the Console's ActionQueuePage (ADR 0020, PRD FR-UI-4): Actions not yet confirmed (or confirmed
// today) and what is scheduled to leave this week (scheduled Drafts, coming Tasks, Enrollment starts), by day, each
// day headed by its count per Connection, as Lemlist's Outreach queue. j/k move; Enter opens what an Action changes
// (an Issue on the Issues page, a Task, a Draft).
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain } from "comms";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const STATE = { scheduled: "muted", running: "blue", confirmed: "green", refused: "red", unknown: "yellow" };

/** Rows with a header per day: "Sat Nov 7" and "Linear 2 · console 1". */
export function queueItems(rows) {
  const items = [];
  let day = null;
  for (const r of rows) {
    if (r.day !== day) {
      day = r.day;
      const counts = {};
      for (const x of rows.filter((y) => y.day === day)) counts[x.connection ?? "console"] = (counts[x.connection ?? "console"] ?? 0) + 1;
      const [y, m, d] = day.split("-").map(Number), at = new Date(y, m - 1, d);
      items.push({ key: `d-${day}`, header: `${DAYS[at.getDay()]} ${MONTHS[at.getMonth()]} ${d}`,
        counts: Object.entries(counts).map(([k, n]) => `${k} ${n}`).join(" · ") });
    }
    items.push({ key: `${r.kind}-${r.id}`, row: r });
  }
  return items;
}

export default class Actions extends View {
  init(_props, cx) {
    this.rows = [];
    this.at = 0;
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
        this.rows = await call("actions.queue");
        this.error = null;
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  command(c, cx) {
    const n = this.rows.length;
    switch (c) {
      case "show": return this.load(cx);
      case "focus_entry": return this.target();
      case "next": case "prev":
        this.at = Math.max(0, Math.min(n - 1, this.at + (c === "next" ? 1 : -1)));
        this.target();
        return cx.notify();
      case "enter": {
        const r = this.rows[this.at];
        if (!r) return;
        if (r.kind === "action") { setShared("issue", r.target_id); return run("nav.issues"); }
        if (r.kind === "task") { setShared("task", r.target_id); return run("nav.tasks"); }
        if (r.kind === "draft") { setShared("draft", r.target_id); return run("nav.drafts"); }
      }
    }
  }

  target() {
    const r = this.rows[this.at];
    if (r && shared("page") === "actions") setShared("target", { col: "list", label: `${r.what} · ${r.target ?? ""}`, keys: [["enter", "open"]] });
  }

  render() {
    const t = theme();
    const items = queueItems(this.rows);
    const selected = this.rows[this.at];
    const header = (it) => h_flex().h(26).px(8).gap(8).items_center()
      .child(div().font_semibold().text_color(t.muted).child(it.header))
      .child(div().text_color(t.dim).child(it.counts));
    const row = (r) => {
      const at = new Date(r.at);
      return h_flex().h(26).px(8).gap(8).rounded(4).items_center()
        .border_l(3).border_color(r === selected ? t.accent : t.background).when(r === selected, (el) => el.bg(t.selected))
        .child(div().w(44).flex_none().text_color(t.muted).child(`${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`))
        .child(div().w(120).flex_none().text_color(t.foreground).child(r.what ?? ""))
        .child(div().flex_1().min_w(0).truncate().text_color(t.strong ?? t.foreground).child(r.target ?? ""))
        .child(div().w(110).flex_none().truncate().text_color(t.muted).child(r.source ?? ""))
        .child(div().w(90).flex_none().truncate().text_color(t.muted).child(r.connection ?? ""))
        .child(div().w(76).flex_none().text_color(t[STATE[r.state]] ?? t.muted).child(r.state ?? ""));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f048a}"))
        .child(div().font_medium().text_color(t.foreground).child("action queue")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !this.rows.length, (el) => el.child(div().p(16).text_color(t.muted).child("Nothing is waiting to leave.")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("actions", items.length, 26, (i) => items[i].key,
          (range) => items.slice(range.start, range.end).map((it) => (it.header ? header(it) : row(it.row))))
          .size_full().track_scroll(this.scroll).on_item_click((key, cx) => {
            const at = this.rows.findIndex((r) => `${r.kind}-${r.id}` === key);
            if (at < 0) return;
            this.at = at;
            setShared("focus", "list");
            focusMain();
            this.target();
            cx.notify();
          })));
  }
}
