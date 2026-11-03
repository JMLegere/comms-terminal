// The work area's top bar: the page shown, as a tab (a Status view with its count), and the full page opened on top
// of it (› Person). The page's keys are in the bottom bar, as the Console's (D9).
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { call, nextEvent, theme, shared } from "comms";

const STATUS = { Todo: "square", Snoozed: "clock", Done: "circle-check", Ignored: "archive" };
const PAGES = { today: ["Today", "sun"], calendar: ["Calendar", "calendar"], drafts: ["Drafts", "square-pen"], cases: ["Cases", "folder"],
  tasks: ["Tasks", "list-checks"], sequences: ["Sequences", "workflow"], people: ["People", "users"],
  organizations: ["Organizations", "building-2"], channels: ["Channels", "hash"] };

const TOP = { person: "Person", organization: "Organization", connection: "Connection", event: "Event", case: "Case" };

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
    const stack = shared("stack") ?? [];
    const base = stack[0] ?? {};
    const page = base.page ?? shared("page") ?? "today";
    const view = base.view ?? "Todo";
    const pipeline = base.pipeline ?? "revenue";
    const top = stack.length > 1 ? TOP[shared("page")] : null;
    const [label, icon, count] = page === "threads" ? [view, STATUS[view] ?? "square", this.counts[view]]
      : page === "cases" ? [`Cases · ${base.case_view ?? "Todo"}`, "folder"]
      : page === "connection" ? ["Connection", "server"]
      : page === "pipeline" ? [pipeline[0].toUpperCase() + pipeline.slice(1), "trending-up"] : [...(PAGES[page] ?? [page, "square"])];
    return h_flex().size_full().bg(t.chrome).border_b(1).border_color(t.border).px(10).gap(6).items_center()
      .child(h_flex().h(29).px(10).gap(7).rounded(7).items_center().flex_none().text_size(12.8)
        .border(1).border_color(t.border).bg(t.background).text_color(t.strong ?? t.foreground)
        .child(svg(`icons/${icon}.svg`).size(13).text_color(t.accent))
        .child(div().font_medium().child(label))
        .when(count != null, (el) => el.child(div().text_color(t.dim).child(String(count))))
        .when(Boolean(top), (el) => el.child(div().text_color(t.dim).child("›")).child(div().font_medium().child(top ?? ""))))
      .child(div().flex_1());
  }
}
