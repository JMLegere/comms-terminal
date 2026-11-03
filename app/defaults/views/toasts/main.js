// Toasts, as the Console's notifications: timed, stacked bottom right, newest last. Each stays `timeout` seconds
// (5 unless the toast says). shared("toasts") holds how many show, so the window draws this layer only then. A
// toast that is a link (the Console's join and link toasts) opens on Ctrl+click (Cmd+click on macOS), as in bodies.
import { View, div } from "gpui-kit";
import { v_flex } from "gpui-base";
import { nextEvent, theme, setShared, open } from "comms";

export default class Toasts extends View {
  init(_props, cx) {
    this.items = [];
    cx.spawn(async (cx) => {
      while (true) {
        const e = await nextEvent();
        if (e.kind === "toast") this.add(e.data, cx);
        if (e.kind === "disconnected") this.add({ message: "The Local core went away", severity: "error" }, cx);
      }
    });
  }

  add(data, cx) {
    const item = { ...data, key: Math.random() };
    this.items = [...this.items, item].slice(-5);
    setShared("toasts", this.items.length);
    cx.notify();
    cx.timer.after((data.timeout ?? 5) * 1000, (cx) => {
      this.items = this.items.filter((i) => i !== item);
      setShared("toasts", this.items.length);
      cx.notify();
    });
  }

  render() {
    const t = theme();
    return v_flex().size_full().gap(6).items_end().justify_end().children(this.items.map((i) =>
      div().max_w(420).px(12).py(8).rounded(8).bg(t.popover ?? t.background).border(1).border_l(4)
        .border_color(i.severity === "error" ? t.danger : i.severity === "warning" ? t.yellow : t.accent)
        .text_size(12.5).text_color(i.severity === "error" ? t.danger : /^(https?:|mailto:)\S+$/.test(i.message ?? "") ? t.blue : t.foreground)
        .when(/^(https?:|mailto:)\S+$/.test(i.message ?? ""), (el) => el.id(`toast-${i.key}`).cursor_pointer()
          .on_click((ev) => (ev?.modifiers?.control || ev?.modifiers?.platform) && open(i.message)))
        .child(i.message ?? "")));
  }
}
