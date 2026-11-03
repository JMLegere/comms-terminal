// The work area's top bar (FR-BAR-1, FR-BAR-2), as herdr-app's tabs bar: on the left the Breadcrumb, the rail page
// (with Today's pane or the Status view) and each full page opened on top of it, by name; earlier levels click back to
// that page. On the right the window controls, drawn by the App because niri draws no title bar. The empty bar drags
// the window; a double-click maximizes it.
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { call, nextEvent, theme, shared, run } from "comms";

const PAGES = { today: "Today", calendar: "Calendar", drafts: "Drafts", tasks: "Tasks", sequences: "Sequences",
  people: "People", organizations: "Organizations", channels: "Channels", connection: "Connection" };
const PANES = { task: "Tasks", draft: "Drafts", paused: "Paused", unattended: "Unattended" };
const TOP = { person: "Person", organization: "Organization", connection: "Connection", event: "Event", case: "Case" };
const FOLD = 70; // ponytail: characters before the middle levels fold; a measured width if the bar ever reports one

/** The Breadcrumb's levels: [{ label, at }], `at` the stack entry a click returns to (null: not clickable). */
export function crumbs(stack, page, detail, counts, crumb) {
  const base = stack[0] ?? { page: page ?? "today" };
  const levels = [];
  const cap = (s) => s[0].toUpperCase() + s.slice(1);
  if (base.page === "threads") levels.push({ label: base.view ?? "Todo", count: counts?.[base.view ?? "Todo"] });
  else if (base.page === "cases") levels.push({ label: "Cases" }, { label: base.case_view ?? "Todo" });
  else if (base.page === "pipeline") levels.push({ label: "Pipeline" }, { label: cap(base.pipeline ?? "revenue") });
  else levels.push({ label: PAGES[base.page] ?? cap(base.page ?? "today") });
  if (base.page === "today") levels.push({ label: PANES[(stack.length > 1 ? base.detail : detail) ?? ""] ?? "Threads" });
  levels.forEach((l) => { l.at = 0; });
  stack.slice(1).forEach((e, i) => {
    const top = i === stack.length - 2;
    levels.push({ label: (top ? crumb : null) ?? e.title ?? TOP[e.page] ?? e.page, at: i + 1 });
  });
  levels[levels.length - 1].at = null; // where you are: plain
  if (levels.length > 3 && levels.map((l) => l.label).join(" › ").length > FOLD)
    return [levels[0], { label: "…", at: null }, ...levels.slice(-2)];
  return levels;
}

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
    const levels = crumbs(shared("stack") ?? [], shared("page"), shared("detail"), this.counts, shared("crumb"));
    const stop = (_e, cx) => cx.stop_propagation();
    const button = (id, icon, command, danger) => div().id(id).size(28).flex_none().flex().items_center().justify_center()
      .rounded(6).text_color(t.muted).cursor_pointer()
      .hover((s) => danger ? s.bg(t.danger).text_color(t.background) : s.bg(t.hover).text_color(t.foreground))
      .on_mouse_down("left", stop).on_click(() => run(command)).child(svg(`icons/${icon}.svg`).size(16));
    const level = (l, i) => {
      const last = i === levels.length - 1, link = l.at != null;
      return h_flex().id(`crumb-${i}`).gap(5).items_center().min_w(0).when(last, (el) => el.flex_none())
        .when(i > 0, (el) => el.child(div().flex_none().text_color(t.dim).child("›")))
        .child(div().id(`crumb-label-${i}`).max_w(240).truncate().text_color(last ? (t.strong ?? t.foreground) : t.muted)
          .when(last, (d) => d.font_semibold())
          .when(link, (d) => d.cursor_pointer().hover((s) => s.text_color(t.accent)).on_mouse_down("left", stop)
            .on_click(() => run(`nav.crumb:${l.at}`)))
          .child(l.label))
        .when(l.count != null, (el) => el.child(div().flex_none().text_color(t.dim).child(String(l.count))));
    };
    return h_flex().id("topbar").size_full().min_w(0).bg(t.chrome).border_b(1).border_color(t.border).pl(16).pr(8).gap(4)
      .items_center().text_size(13)
      .on_mouse_down("left", (e) => run(e.click_count === 2 ? "app.maximize" : "app.move"))
      .child(h_flex().flex_1().min_w(0).gap(5).items_center().overflow_hidden().children(levels.map(level)))
      .child(button("minimize", "win-min", "app.minimize"))
      .child(button("maximize", "win-max", "app.maximize"))
      .child(button("close-window", "win-close", "app.close", true));
  }
}
