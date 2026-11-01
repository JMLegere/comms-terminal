// The rail: Status views with their counts, then Cases. Keys 1-4 pick a view (keys.json: nav.todo … nav.ignored).
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run } from "comms";

const VIEWS = [["todo", "Todo", "square"], ["snoozed", "Snoozed", "clock"], ["done", "Done", "circle-check"], ["ignored", "Ignored", "archive"]];
const CASE = { Todo: "status_needs_next", Snoozed: "status_waiting", Done: "status_done", Ignored: "status_ignored" };

export default class Nav extends View {
  init(_props, cx) {
    this.counts = {};
    this.cases = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
    cx.spawn(async () => {
      while (true) {
        const c = await nextCommand();
        const pick = VIEWS.find(([key]) => key === c);
        if (pick) this.pick(pick[1]);
      }
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await call("rail.summary");
        this.counts = r.counts ?? {};
        this.cases = r.cases ?? [];
        this.error = null;
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  pick(view) {
    setShared("view", view);
    run("threads.reload");
  }

  section(t, label, count) {
    return h_flex().px(16).h(41).pt(14).pb(5).flex_none().items_center().justify_between()
      .child(div().text_size(11.5).font_medium().text_color(t.dim).child(label))
      .when(count != null, (el) => el.child(div().text_size(11).text_color(t.dim).child(String(count))));
  }

  render() {
    const t = theme();
    const current = shared("view") ?? "Todo";
    const row = (id, active, lead, label, tail, onClick) =>
      h_flex().id(id).h(30).px(8).flex_none().rounded(8).items_center().gap(9).cursor_pointer()
        .when(active, (el) => el.bg(t.selected))
        .hover((s) => s.bg(t.hover))
        .on_click(onClick)
        .child(lead)
        .child(div().flex_1().min_w(0).truncate().text_color(active ? t.strong ?? t.foreground : t.foreground).when(active, (el) => el.font_medium()).child(label))
        .child(div().flex_none().text_size(11).text_color(t.dim).child(tail));
    return v_flex().size_full().min_h(0).bg(t.chrome).text_size(13)
      .child(this.section(t, "Views"))
      .child(v_flex().px(8).flex_none().children(VIEWS.map(([key, label, icon]) =>
        row(`view-${key}`, label === current, svg(`icons/${icon}.svg`).size(14).flex_none().text_color(label === current ? t.accent : t.muted),
          label, String(this.counts[label] ?? ""), () => this.pick(label)))))
      .child(this.section(t, "Cases", this.cases.length))
      .child(v_flex().id("cases").flex_1().min_h(0).px(8).overflow_y_scroll().children(this.cases.map((c) =>
        row(`case-${c.id}`, false, div().size(7).rounded(4).flex_none().bg(t[CASE[c.status]] ?? t.dim),
          c.title ?? "", String(Object.values(c.counts ?? {}).reduce((a, b) => a + b, 0)), () => {}))))
      .when(Boolean(this.error), (el) => el.child(div().px(16).py(8).text_size(11.5).text_color(t.danger).child(this.error ?? "")));
  }
}
