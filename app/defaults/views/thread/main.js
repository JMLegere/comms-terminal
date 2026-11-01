// The selected Thread: a pane header (where it is), its title, and its messages as text. HTML bodies come in
// Phase 4 (litehtml).
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, mark } from "comms";

const PLATFORM = { gmail: "file-text", fastmail: "file-text", email: "file-text", slack: "layers", quo: "square-pen", gcal: "clock" };

export default class Thread extends View {
  init(_props, cx) {
    this.view = null;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) if ((await nextCommand()) === "show") this.load(cx);
    });
  }

  load(cx) {
    const id = shared("thread");
    if (!id) {
      this.view = null;
      return cx.notify();
    }
    cx.spawn(async (cx) => {
      try {
        const answer = await call("thread.view", { thread_id: id });
        if (shared("thread") === id) this.view = answer.view;
        this.error = null;
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  render() {
    const t = theme();
    const v = this.view;
    const thread = v?.thread?.f ?? v?.thread ?? {};
    if (v && thread.id !== this.shown) {
      this.shown = thread.id;
      mark("shown");
    }
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!v) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Thread selected"));
    const messages = v.messages ?? [];
    const person = v.person?.name;
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg(`icons/${PLATFORM[v.channel?.platform] ?? "file-text"}.svg`).size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child(person ?? v.channel?.name ?? ""))
        .child(div().truncate().text_color(t.dim).child(`· ${v.where ?? ""}`)))
      .child(v_flex().id("messages").flex_1().min_h(0).overflow_y_scroll()
        .child(v_flex().px(28).pt(22).pb(14).gap(6)
          .child(div().text_size(18).font_semibold().text_color(t.strong ?? t.foreground).child(thread.title ?? ""))
          .child(h_flex().gap(8).text_size(12).text_color(t.muted)
            .child(div().child(`${messages.length} message${messages.length === 1 ? "" : "s"}`))
            .when(Boolean(v.case?.title), (el) => el.child(div().text_color(t.dim).child(`· ${v.case?.title}`)))))
        .children(messages.map((m) =>
          v_flex().id(`m-${m.id}`).mx(20).mb(10).px(16).py(12).gap(8).rounded(10).border(1).border_color(t.border).bg(t.background)
            .child(h_flex().gap(8).items_center()
              .child(div().size(22).rounded(11).flex_none().flex().items_center().justify_center().bg(t.selected)
                .text_size(11).font_semibold().text_color(t.strong ?? t.foreground).child((m.who ?? "?").slice(0, 1).toUpperCase()))
              .child(div().flex_1().font_medium().text_color(t.foreground).child(m.who ?? ""))
              .child(div().text_size(11).text_color(t.dim).child((m.at ?? "").slice(0, 16).replace("T", " "))))
            .child(div().text_size(13.5).line_height(1.5).text_color(m.deleted ? t.dim : t.foreground)
              .child(m.deleted ? "(deleted)" : (m.content ?? ""))))));
  }
}
