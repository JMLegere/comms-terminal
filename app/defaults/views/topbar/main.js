// The work area's top bar: the Status view shown, as a tab, and the keys that matter most.
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { call, nextEvent, theme, shared } from "comms";

const ICON = { Todo: "square", Snoozed: "clock", Done: "circle-check", Ignored: "archive" };

export default class Topbar extends View {
  init(_props, cx) {
    this.counts = {};
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.counts = (await call("rail.summary")).counts ?? {};
      } catch (_) {}
      cx.notify();
    });
  }

  render() {
    const t = theme();
    const view = shared("view") ?? "Todo";
    const key = (k, label) => h_flex().gap(5).items_center()
      .child(div().px(5).rounded(4).bg(t.raised).text_color(t.blue ?? t.accent).font_semibold().child(k))
      .child(div().text_color(t.dim).child(label));
    return h_flex().size_full().bg(t.chrome).border_b(1).border_color(t.border).px(10).gap(6).items_center()
      .child(h_flex().h(29).px(10).gap(7).rounded(7).items_center().flex_none().text_size(12.8)
        .border(1).border_color(t.border).bg(t.background).text_color(t.strong ?? t.foreground)
        .child(svg(`icons/${ICON[view] ?? "square"}.svg`).size(13).text_color(t.accent))
        .child(div().font_medium().child(view))
        .child(div().text_color(t.dim).child(String(this.counts[view] ?? ""))))
      .child(div().flex_1())
      .child(h_flex().gap(14).items_center().text_size(11.5)
        .child(key("j k", "move"))
        .child(key("1–4", "views")));
  }
}
