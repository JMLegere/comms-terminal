// The right pane for a Today Paused or Unattended row, as the Console's show_side: the Person with their Sequence
// section, or the Organization with its Pipeline section (D89). Shared "side" names the row: {kind, id}.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextCommand, theme, shared, setShared, run, open } from "comms";


/** Console segment lines ([text, colour, bold, link]) as rows; a blank line keeps its height. A person or org link
 * opens that page on the stack; a URL opens on Ctrl+click (Cmd+click on macOS), as message links do (D44). */
export function segLines(t, lines, key = "s", orgs = {}) {
  const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
  const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
  const follow = (link, ev) => {
    const push = (entry) => { setShared("push", entry); run("nav.push"); };
    if (link.startsWith("person:")) push({ page: "person", person: link.slice(7) });
    else if (link.startsWith("org:")) push({ page: "organization", organization: { domain: link.slice(4), id: orgs[link.slice(4)] ?? null } });
    else if (ev?.modifiers?.control || ev?.modifiers?.platform) open(link);
  };
  return (lines ?? []).map((segs, r) => (segs.length ? h_flex().w_full().gap(0).flex_wrap()
    .children(segs.map(([text, hex, bold, link], i) => div().min_w(0).text_color(link ? t.blue : color(hex)).when(Boolean(bold), (d) => d.font_semibold())
      .when(Boolean(link), (d) => d.id(`${key}-${r}-${i}`).cursor_pointer().on_click((ev) => follow(link, ev))).child(text)))
    : div().h(10)));
}

export default class Side extends View {
  init(_props, cx) {
    this.lines = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextCommand()) === "show") this.load(cx);
    });
  }

  load(cx) {
    const s = shared("side");
    if (!s) return;
    cx.spawn(async (cx) => {
      try {
        const lines = await call("today.side", { kind: s.kind, id: s.id });
        if (JSON.stringify(shared("side")) !== JSON.stringify(s)) return; // shared() hands back a copy
        this.lines = lines;
        this.error = null;
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  render() {
    const t = theme();
    return v_flex().id("side").size_full().min_h(0).overflow_y_scroll().p(20).gap(2).bg(t.background).text_size(13)
      .when(Boolean(this.error), (el) => el.child(div().text_color(t.danger).child(this.error ?? "")))
      .children(segLines(t, this.lines, "side"));
  }
}
