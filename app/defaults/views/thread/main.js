// The selected Thread: a pane header (where it is), its title, and its messages. An email with an HTML part shows
// it through MessageBody (litehtml; nothing is fetched until "Load images"); other messages show their text.
// Attachments and links open with one click; files dropped on the window go on this Thread's reply.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { MessageBody } from "gpui-component";
import { call, nextEvent, nextCommand, theme, shared, mark, open } from "comms";

const PLATFORM = { gmail: "file-text", fastmail: "file-text", email: "file-text", slack: "layers", quo: "square-pen", gcal: "clock" };

export default class Thread extends View {
  init(_props, cx) {
    this.view = null;
    this.bodies = {}; // message id -> { html, remote, loaded } from message.body
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        if (c === "show") this.load(cx);
        if (c === "images") for (const m of this.view?.messages ?? []) if (this.bodies[m.id]?.remote > 0) this.body(m, true, cx);
      }
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
        if (shared("thread") !== id) return;
        this.view = answer.view;
        this.staged = answer.staged ?? [];
        this.error = null;
        // Bodies wait until the selection rests, so holding j or k never waits on them.
        cx.timer.after(150, (cx) => {
          if (shared("thread") !== id) return;
          for (const m of this.view?.messages ?? []) if (m.raw && !this.bodies[m.id]) this.body(m, false, cx);
        });
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  /** The message's HTML: safe (no remote images), or with them fetched by the core ("Load images"). */
  body(m, images, cx) {
    this.bodies[m.id] = { ...(this.bodies[m.id] ?? {}), loading: true };
    cx.spawn(async (cx) => {
      try {
        const answer = await call(images ? "message.load_images" : "message.body", { raw: m.raw });
        this.bodies[m.id] = answer ? { html: answer.html, remote: answer.remote, loaded: images } : { none: true };
      } catch (e) {
        this.bodies[m.id] = { none: true, error: String(e) };
      }
      cx.notify();
    });
  }

  content(m, t) {
    if (m.deleted) return div().text_color(t.dim).child("(deleted)");
    const b = this.bodies[m.id];
    const text = div().text_size(13.5).line_height(1.5).text_color(t.foreground).child(m.content ?? "");
    if (!m.raw || !b || b.none) return text;
    if (!b.html) return text.text_color(t.dim);
    return v_flex().gap(6)
      .when(b.remote > 0 && !b.loaded, (el) => el.child(h_flex().gap(8).text_size(11.5).text_color(t.muted)
        .child(div().child(`${b.remote} remote image${b.remote === 1 ? "" : "s"} held back`))
        .child(div().id(`load-${m.id}`).px(6).rounded(4).bg(t.selected).text_color(t.accent).cursor_pointer()
          .on_click((_, cx) => this.body(m, true, cx)).child(b.loading ? "Loading…" : "Load images"))))
      .child(div().rounded(6).bg("#ffffff").p(8).child(MessageBody(`${m.id}:${b.loaded ? 1 : 0}`, b.html)));
  }

  files(list, t, label) {
    if (!list?.length) return null;
    return h_flex().flex_wrap().gap(6).items_center().text_size(12)
      .child(div().text_color(t.dim).child(label))
      .children(list.map((a, i) =>
        h_flex().id(`${label}-${i}-${a.name}`).gap(4).px(8).py(2).rounded(6).border(1).border_color(t.border)
          .text_color(a.url ? t.accent : t.muted).when(Boolean(a.url), (el) => el.cursor_pointer().on_click(() => open(a.url)))
          .child(svg("icons/file-text.svg").size(12))
          .child(div().child(a.name ?? "file"))));
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
            .child(this.content(m, t))
            .when(Boolean(m.attachments?.filter((a) => !a.inline).length), (el) =>
              el.child(this.files(m.attachments.filter((a) => !a.inline), t, "Attachments")))))
        .when(Boolean(this.staged?.length), (el) =>
          el.child(div().mx(20).mb(14).child(this.files(this.staged, t, "Reply files")))));
  }
}
