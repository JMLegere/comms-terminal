// Channels: each Connection with its health. j/k move and the Connection's channel rules fill the right pane.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run } from "comms";

export const label = (c) => (c.platform === "slack" || c.platform === "gcal" ? c.label || c.platform : c.external_id);
export const broken = (c) => !c.auth_ok || Boolean(c.probe_error || c.last_error);
const checked = (c) => `checked ${String(c.probed_at ?? "").slice(0, 16).replace("T", " ")}`;
const health = (c) => c.probe_error || c.last_error || (c.auth_ok ? checked(c) : "signed out");

export default class Channels extends View {
  init(_props, cx) {
    this.rows = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "channels") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        const i = this.rows.findIndex((r) => r.id === shared("connection"));
        if (c === "show") this.load(cx);
        else if (c === "next" || c === "prev") this.select(this.rows[Math.max(0, Math.min(this.rows.length - 1, i + (c === "next" ? 1 : -1)))]?.id);
      }
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.rows = await call("connections.list");
        this.error = null;
        if (shared("page") === "channels" && !this.rows.some((r) => r.id === shared("connection"))) this.select(this.rows[0]?.id);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(id) {
    if (!id) return;
    setShared("connection", id);
    run("connection.show");
  }

  render() {
    const t = theme();
    const selected = shared("connection");
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/hash.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Connections")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .child(v_flex().id("connections").flex_1().min_h(0).overflow_y_scroll().px(6).py(6).children(this.rows.map((c) =>
        v_flex().id(`c-${c.id}`).h(46).px(8).justify_center().rounded(8).gap(1).cursor_pointer()
          .when(c.id === selected, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
          .on_click(() => this.select(c.id))
          .child(h_flex().gap(9).items_center()
            .child(div().size(7).rounded(4).flex_none().bg(broken(c) ? t.danger : t.status_done))
            .child(div().flex_1().min_w(0).truncate().font_medium().text_color(t.foreground).child(label(c)))
            .child(div().text_size(11).text_color(t.dim).child(c.platform)))
          .child(div().pl(16).truncate().text_size(12).text_color(broken(c) ? t.danger : t.muted)
            .child(health(c))))));
  }
}
