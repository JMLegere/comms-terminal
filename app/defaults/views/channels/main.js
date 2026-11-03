// Channels: each Connection with its health. j/k move and the Connection's channel rules fill the right pane; Enter
// (or →) moves the focus into them.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain, keys } from "comms";

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
        else if (c === "focus_entry") this.target();
        else if (c === "enter") run("nav.focus_right"); // into the Connection: its first channel rule takes the focus
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
    this.target();
  }

  /** The bar's line for the focused row (element focus): the Connection and the single-letter keys its page binds. */
  target() {
    if (shared("page") !== "channels" || shared("focus") === "detail") return;
    const c = this.rows.find((r) => r.id === shared("connection"));
    const bound = Object.entries((keys() ?? {})["page:channels"] ?? {})
      .filter(([k, cmd]) => k.length === 1 && !/\.(next|prev)$/.test(cmd)).map(([k, cmd]) => [k, cmd.split(".")[1]]);
    if (c) setShared("target", { col: "list", label: `Connection · ${label(c)}`, keys: [["→", "rules"], ...bound] }); // page:channels binds no enter
  }

  render() {
    const t = theme();
    const selected = shared("connection");
    const focusHere = (shared("focus") ?? "list") === "list";
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/hash.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Connections")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .child(v_flex().id("connections").flex_1().min_h(0).overflow_y_scroll().px(6).py(6).children(this.rows.map((c) =>
        v_flex().id(`c-${c.id}`).h(46).px(8).justify_center().rounded(8).gap(1).cursor_pointer()
          .hover((s) => s.bg(t.hover))
          // The path (Logseq bullet threading): full bar and highlight on the focused row, a thin bar while the focus
          // is further in (the Connection).
          .border_l(3).border_color(c.id === selected ? t.accent : t.background)
          .when(c.id === selected && focusHere, (el) => el.bg(t.selected))
          .when(c.id === selected && !focusHere, (el) => el.border_l(1))
          .on_click((_e, cx) => {
            setShared("focus", "list"); // a click focuses the row and takes the keys from any text field
            focusMain();
            this.select(c.id);
            cx.notify();
          })
          .child(h_flex().gap(9).items_center()
            .child(div().size(7).rounded(4).flex_none().bg(broken(c) ? t.danger : t.status_done))
            .child(div().flex_1().min_w(0).truncate().font_medium().text_color(t.foreground).child(label(c)))
            .child(div().text_size(11).text_color(t.dim).child(c.platform)))
          .child(div().pl(16).truncate().text_size(12).text_color(broken(c) ? t.danger : t.muted)
            .child(health(c))))));
  }
}
