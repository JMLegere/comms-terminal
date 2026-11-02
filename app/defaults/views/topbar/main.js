// The work area's top bar: the page shown, as a tab (a Status view with its count), and the keys that matter most.
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { call, nextEvent, theme, shared } from "comms";

const STATUS = { Todo: "square", Snoozed: "clock", Done: "circle-check", Ignored: "archive" };
const PAGES = { today: ["Today", "sun"], calendar: ["Calendar", "calendar"], drafts: ["Drafts", "square-pen"], cases: ["Cases", "folder"],
  tasks: ["Tasks", "list-checks"], sequences: ["Sequences", "workflow"], people: ["People", "users"],
  organizations: ["Organizations", "building-2"], channels: ["Channels", "hash"] };

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
    const page = shared("page") ?? "threads";
    const view = shared("view") ?? "Todo";
    const pipeline = shared("pipeline") ?? "revenue";
    const [label, icon, count] = page === "threads" ? [view, STATUS[view] ?? "square", this.counts[view]]
      : page === "pipeline" ? [pipeline[0].toUpperCase() + pipeline.slice(1), "trending-up"] : [...(PAGES[page] ?? [page, "square"])];
    const key = (k, text) => h_flex().gap(5).items_center()
      .child(div().px(5).rounded(4).bg(t.raised).text_color(t.blue ?? t.accent).font_semibold().child(k))
      .child(div().text_color(t.dim).child(text));
    return h_flex().size_full().bg(t.chrome).border_b(1).border_color(t.border).px(10).gap(6).items_center()
      .child(h_flex().h(29).px(10).gap(7).rounded(7).items_center().flex_none().text_size(12.8)
        .border(1).border_color(t.border).bg(t.background).text_color(t.strong ?? t.foreground)
        .child(svg(`icons/${icon}.svg`).size(13).text_color(t.accent))
        .child(div().font_medium().child(label))
        .when(count != null, (el) => el.child(div().text_color(t.dim).child(String(count)))))
      .child(div().flex_1())
      .child(h_flex().gap(14).items_center().text_size(11.5)
        .child(key("j k", "move"))
        .child(key("1–0", "pages"))
        .child(key("?", "keys")));
  }
}
