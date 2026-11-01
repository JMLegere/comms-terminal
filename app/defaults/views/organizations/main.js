// Organizations: each with its People, Todo Threads and Pipeline Stages. Type to filter; j/k move and the
// Organization fills the right pane, where s sets a Stage and v a Value.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle, InputState, Input } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain } from "comms";

export default class Organizations extends View {
  init(_props, cx) {
    this.rows = [];
    this.states = {};
    this.scroll = VirtualListScrollHandle.new();
    this.filter = InputState.new({ placeholder: "Filter organizations" });
    this.filter.on("change", (_e, cx) => cx.notify());
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "organizations") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  shown() {
    const f = this.filter.value().trim().toLowerCase();
    return f ? this.rows.filter((r) => (r.domain ?? "").includes(f)) : this.rows;
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const answer = await call("organizations.list");
        this.rows = answer.organizations.sort((a, b) => (b.todo - a.todo) || (b.threads - a.threads));
        this.states = answer.states ?? {};
        this.error = null;
        if (shared("page") === "organizations" && !shared("organization")) this.select(this.shown()[0]);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(r) {
    if (!r) return;
    setShared("organization", { id: r.id, domain: r.domain });
    run("organization.show");
    const at = this.shown().findIndex((x) => x.id === r.id);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
  }

  command(c, cx) {
    const rows = this.shown();
    const i = rows.findIndex((r) => r.id === shared("organization")?.id);
    if (c === "show") return this.load(cx);
    if (c === "filter") return this.filter.focus();
    if (c === "next" || c === "prev") this.select(rows[Math.max(0, Math.min(rows.length - 1, i + (c === "next" ? 1 : -1)))]);
  }

  render() {
    const t = theme();
    const selected = shared("organization")?.id;
    const rows = this.shown();
    const row = (r) => {
      const active = r.id === selected;
      const stages = Object.entries(this.states[r.id] ?? {}).map(([p, s]) => `${p[0]}${p.slice(1).toLowerCase()}: ${s.stage}`).join(" · ");
      return v_flex().h(46).px(8).justify_center().rounded(8).gap(1).cursor_pointer()
        .when(active, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
        .child(h_flex().gap(9).items_center()
          .child(div().flex_1().min_w(0).truncate().font_medium().text_color(t.foreground).child(r.domain ?? ""))
          .when(r.todo > 0, (el) => el.child(div().flex_none().text_size(11).text_color(t.status_needs_next).child(`${r.todo} todo`)))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(`${r.people} people`)))
        .child(div().truncate().text_size(12).text_color(t.muted).child(stages || `${r.threads} threads`));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/building-2.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Organizations"))
        .child(div().text_color(t.dim).child(`· ${rows.length}`)))
      .child(div().px(8).pt(8).on_key_down((ev, cx) => {
        if (ev.keystroke === "escape" || ev.keystroke === "enter") { focusMain(); cx.stop_propagation(); }
        else if (ev.keystroke === "down") { this.command("next", cx); cx.stop_propagation(); }
        else if (ev.keystroke === "up") { this.command("prev", cx); cx.stop_propagation(); }
      }).child(Input.new(this.filter)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .child(div().flex_1().min_h(0).px(6).py(6).child(
        v_virtual_list("orgs", rows.length, 46, (i) => rows[i].id, (range) => rows.slice(range.start, range.end).map(row))
          .size_full().track_scroll(this.scroll).on_item_click((key) => this.select(rows.find((r) => r.id === key)))));
  }
}
