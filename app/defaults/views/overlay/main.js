// Pickers and prompts for every view, drawn over the whole window (the Console's Pick and Ask). A view opens one:
//   setShared("picker", { title, options: [{ value, label, hint }], reply: "thread.picked", prompt: "placeholder",
//     body: [{ text, dim, strong, label }] });  // body: lines shown above the options, as a Console Pick body; a label sits dim before its text
//   run("overlay.open");
// Typing filters the options (or is the answer, with `prompt` and no options; the prompt is the box's placeholder,
// as the Console's Ask); ↑/↓ move, Enter chooses, Escape cancels, Tab stays in the box (keys.json "view:overlay").
// The choice goes back as the command `<reply>:<JSON {value, text}>`.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { nextCommand, theme, shared, setShared, run, focusMain, keys } from "comms";

export default class Overlay extends View {
  init(_props, cx) {
    this.input = this.field("");
    this.at = 0;
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        if (c === "help") this.help();
        else if (c.startsWith("ran:")) run(JSON.parse(c.slice(4)).value);
        if (c === "open" || c === "help") this.open(cx);
      }
    });
  }

  open(cx) {
    this.at = 0;
    const placeholder = shared("picker")?.prompt ?? "";
    if (placeholder !== this.placeholder) this.input = this.field(placeholder); // no setter: a new box per prompt
    this.input.set_value(shared("picker")?.value ?? "");
    this.input.focus();
    cx.notify();
  }

  field(placeholder) {
    this.placeholder = placeholder;
    const input = InputState.new({ placeholder });
    input.on("change", (_e, cx) => { this.at = 0; cx.notify(); });
    input.on("submit", (_e, cx) => this.choose(this.shown()[this.at], cx));
    return input;
  }

  /** ?: this page's keys, then the global ones; choosing one runs it. */
  help() {
    const k = keys() ?? {};
    const page = shared("page") ?? "threads";
    const rows = (section) => Object.entries(k[section] ?? {}).map(([key, action]) => ({ value: action, label: action, hint: key }));
    setShared("picker", { title: `Keys · ${page} · type to find one, Enter runs it`, reply: "overlay.ran",
      options: [...rows(`page:${page}`), ...rows("global")] });
  }

  shown() {
    const p = shared("picker");
    const text = this.input.value().trim().toLowerCase();
    const options = p?.options ?? [];
    return text && !p?.prompt ? options.filter((o) => `${o.label} ${o.hint ?? ""}`.toLowerCase().includes(text)) : options;
  }

  choose(option, cx) {
    const p = shared("picker");
    if (!p) return;
    const text = this.input.value().trim();
    if (!option && !(p.prompt && text)) return;
    setShared("picker", null);
    focusMain();
    run(`${p.reply}:${JSON.stringify({ value: option?.value ?? null, text })}`);
    cx.notify();
  }

  cancel(cx) {
    setShared("picker", null);
    focusMain();
    cx.notify();
  }

  render() {
    const t = theme();
    const p = shared("picker");
    if (!p) return div();
    const options = this.shown();
    this.at = Math.max(0, Math.min(this.at, options.length - 1));
    return div().size_full().flex().justify_center().pt(90).bg("#00000033")
      .on_mouse_down("left", (_e, cx) => this.cancel(cx))
      .child(v_flex().id("picker").key_context("View view=overlay").w(520).max_h(460).rounded(10).border(1).border_color(t.border).bg(t.background)
        .shadow_lg().overflow_hidden().text_size(13)
        .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
        .on_key_down((e, cx) => {
          if (e.keystroke === "escape") this.cancel(cx);
          else if (e.keystroke === "down" || e.keystroke === "ctrl-n") { this.at += 1; cx.notify(); }
          else if (e.keystroke === "up" || e.keystroke === "ctrl-p") { this.at -= 1; cx.notify(); }
          else return;
          cx.stop_propagation();
        })
        .child(div().px(14).pt(12).pb(6).text_size(11.5).font_medium().text_color(t.dim).child(p.title ?? ""))
        .when(Boolean(p.body), (el) => el.child(v_flex().px(14).pb(8).gap(2).text_size(12.5)
          .children(p.body.map((line) => h_flex().gap(0)
            .when(Boolean(line.label), (r) => r.child(div().w(84).flex_none().text_color(t.muted).child(line.label ?? "")))
            .child(div().flex_1().min_w(0).text_color(line.dim ? t.dim : t.foreground).when(Boolean(line.strong), (d) => d.font_semibold()).child(line.text ?? ""))))))
        .child(div().px(10).pb(8).child(Input.new(this.input)))
        .child(v_flex().id("picker-options").flex_1().min_h(0).overflow_y_scroll().px(6).pb(6)
          .children(options.map((o, i) =>
            h_flex().id(`o-${i}`).h(30).px(8).gap(8).rounded(6).items_center().cursor_pointer()
              .when(i === this.at, (el) => el.bg(t.selected))
              .hover((s) => s.bg(t.hover))
              .on_click((_e, cx) => this.choose(o, cx))
              .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(o.label ?? ""))
              .when(Boolean(o.hint), (el) => el.child(div().flex_none().text_size(11.5).text_color(t.dim).child(o.hint)))))));
  }
}
