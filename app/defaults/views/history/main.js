// History, as the Console's HistoryPage (ADR 0021, 0022): the Command log, newest first, a header per local day,
// each change with its time, words, Source and whether it was undone; v shows or hides the Service's own work.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, focusMain, toast } from "comms";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");

/** History rows (commands.history) with a header per local day. */
export function historyItems(rows) {
  const items = [];
  let day = null;
  for (const c of rows) {
    const at = new Date(c.at), key = `${at.getFullYear()}-${at.getMonth()}-${at.getDate()}`;
    if (key !== day) {
      day = key;
      items.push({ key: `h-${key}`, header: `${DAYS[at.getDay()]} ${MONTHS[at.getMonth()]} ${at.getDate()}` });
    }
    items.push({ key: `command-${c.id}`, command: c });
  }
  return items;
}

export default class History extends View {
  init(_props, cx) {
    this.rows = [];
    this.service = true; // the Service's own Commands show (FR-UN-19)
    this.at = 0;
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "history") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.rows = await call("history.page", { service: this.service });
        this.at = Math.min(this.at, Math.max(this.rows.length - 1, 0));
        this.error = null;
        this.target();
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  target() {
    const c = this.rows[this.at];
    if (c && shared("page") === "history") {
      setShared("target", { col: "list", label: c.label, keys: [["v", this.service ? "hide Service work" : "show Service work"]] });
    }
  }

  command(c, cx) {
    switch (c) {
      case "show": return this.load(cx);
      case "focus_entry": return this.target();
      case "toggle_service":
        this.service = !this.service;
        toast(this.service ? "Service work shown" : "Service work hidden");
        return this.load(cx);
      case "next": case "prev":
        this.at = Math.max(0, Math.min(this.rows.length - 1, this.at + (c === "next" ? 1 : -1)));
        this.target();
        return cx.notify();
    }
  }

  render() {
    const t = theme();
    const items = historyItems(this.rows);
    const selected = this.rows[this.at];
    const row = (c) => {
      const at = new Date(c.at);
      return h_flex().h(26).px(8).gap(8).rounded(4).items_center()
        .border_l(3).border_color(c === selected ? t.accent : t.background).when(c === selected, (el) => el.bg(t.selected))
        .child(div().w(44).flex_none().text_color(t.muted).child(`${pad(at.getHours())}:${pad(at.getMinutes())}`))
        .child(div().flex_1().min_w(0).truncate().text_color(c.undone ? t.muted : t.foreground).child(c.label ?? ""))
        .child(div().w(60).flex_none().text_color(t.muted).child(c.undone ? "undone" : c.undoes ? "undo" : ""))
        .child(div().w(80).flex_none().truncate().text_color(t.muted).child(c.source_name ?? ""));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f02da}"))
        .child(div().font_medium().text_color(t.foreground).child("history"))
        .child(div().text_color(t.dim).child(`every change · z undoes the newest from a Client · v Service work ${this.service ? "shown" : "hidden"}`)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !this.rows.length, (el) => el.child(div().p(16).text_color(t.muted).child("No changes yet.")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("history", items.length, 26, (i) => items[i].key,
          (range) => items.slice(range.start, range.end).map((it) => (it.header
            ? h_flex().h(26).px(8).items_center().child(div().font_semibold().text_color(t.muted).child(it.header))
            : row(it.command))))
          .size_full().track_scroll(this.scroll).on_item_click((key, cx) => {
            const at = this.rows.findIndex((c) => `command-${c.id}` === key);
            if (at < 0) return;
            this.at = at;
            setShared("focus", "list");
            focusMain();
            this.target();
            cx.notify();
          })));
  }
}
