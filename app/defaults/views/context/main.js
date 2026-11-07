// One Context source (FR-MOD-16), as the Console's ContextSourcePage: its name and state, what it adds context to,
// the last check and the problem. Read-only; esc goes back. The state and check time are this App's own (nav checks
// each source at start; shared "context_state" and "context_checked").
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared } from "comms";

const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };

export default class Context extends View {
  init(_props, cx) {
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "context") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        if (c === "show") this.load(cx);
        if (c === "focus_entry") { this.target(); cx.notify(); }
      }
    });
  }

  load(cx) {
    const source = shared("source");
    if (!source) return;
    cx.spawn(async (cx) => {
      try {
        this.page = await call("context.page", { source, state: (shared("context_state") ?? {})[source] ?? null,
          checked: (shared("context_checked") ?? {})[source] ?? null });
        setShared("crumb", (this.page.lines?.[0] ?? []).map((g) => g[0]).join("").trim() || null); // the Breadcrumb's name
        this.error = null;
        this.target();
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  target() {
    if (shared("page") !== "context" || shared("focus") !== "detail" || !this.page) return;
    setShared("target", { col: "detail", label: `Context source · ${(this.page.lines?.[0] ?? []).map((g) => g[0]).join("").trim()}`,
      keys: [["esc", "back"]] });
  }

  render() {
    const t = theme();
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!this.page) return pane.child(div().p(24).text_color(t.muted).child(this.error ?? "No Context source selected"));
    const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
    return pane.child(v_flex().id("context").flex_1().min_h(0).overflow_y_scroll().px(20).py(14).gap(2)
      .children(this.page.lines.map((segs) => (segs.length ? h_flex().w_full().flex_wrap()
        .children(segs.map(([text, hex, bold]) => div().min_w(0).text_color(color(hex)).when(Boolean(bold), (d) => d.font_semibold()).child(text)))
        : div().h(10)))));
  }
}
