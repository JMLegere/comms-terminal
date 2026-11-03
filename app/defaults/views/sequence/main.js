// The Sequences page's right pane, as the Console's side pane (D113, D118): the selected Sequence (size, edited, use
// when, sends from, Enrollments, keys), or, while the Steps pane has the keys, the selected Step's editor: "Step N ·
// Kind", what would stop it running, a preview filled for an enrolled Person, then its fields for this kind of Step
// (Gap, Time, Reply to, Subject, Move, Pipeline, Stage, Value, Question, Path 1, Path 2, Is, Sequence), its body
// (script, note or letter) and a call's voicemail. ctrl-s saves; leaving a field saves (FR-CONF-3); esc saves and
// goes back to the Steps. Element focus: in the Step editor each field is an element (j/k move between them, Enter
// gives one the keyboard); the Sequence's properties are one element that j/k scroll.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, TextareaState, Textarea } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain } from "comms";

const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
export const HINT = "ctrl+s save · edits apply to Tasks not yet due · esc back to Steps";
const LABELS = ["Gap", "Time", "Reply to", "Subject", "Move", "Pipeline", "Stage", "Value", "Question", "Path 1", "Path 2", "Is", "Sequence"];

export default class Sequence extends View {
  init(_props, cx) {
    this.page = null;
    this.ed = null;
    this.fi = 0; // the focused field in the Step editor
    this.inputs = Object.fromEntries(LABELS.map((l) => [l, this.wire(InputState.new({}), l)]));
    this.body = this.wire(TextareaState.new({ rows: 8 }), "Step body");
    this.body.set_auto_grow(6, 24);
    this.voice = this.wire(TextareaState.new({ rows: 3 }), "voicemail");
    this.voice.set_auto_grow(2, 10);
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "sequences" && !this.dirty) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  /** Leaving a field saves (FR-CONF-3); while one has the keyboard, the bar says so (typing). */
  wire(f, name) {
    f.on("blur", (_e, cx) => {
      if (shared("typing") === name) setShared("typing", null);
      if (this.ed) cx.spawn(async (cx) => this.persist(cx));
    });
    f.on("focus", () => setShared("typing", name));
    return f;
  }

  /** The Step editor's fields, as [label, state]: its inputs, the body, and a call's voicemail. */
  fields() {
    const ed = this.ed;
    if (!ed) return [];
    return [...ed.fields.map(([label]) => [label, this.inputs[label]]), [ed.body_label ?? "Body", this.body],
      ...(ed.voicemail !== null ? [["Voicemail", this.voice]] : [])];
  }

  /** The bar's line for the focused field, or the Sequence's properties (element focus), and the keys they take. */
  target() {
    if (shared("page") !== "sequences" || shared("focus") !== "detail" || !this.page) return;
    const f = this.fields()[this.fi];
    setShared("target", f
      ? { col: "detail", label: `Step ${this.ed.step.position} · ${f[0]}`, keys: [["enter", "edit"], ["j/k", "field"], ["esc", "Steps"]] }
      : { col: "detail", label: `Sequence · ${this.page.sequence?.name ?? ""}`, keys: [["j/k", "scroll"], ["esc", "list"]] });
  }

  load(cx) {
    const id = shared("sequence"), step = shared("step");
    if (!id) { this.page = null; this.ed = null; return cx.notify(); }
    cx.spawn(async (cx) => {
      try {
        const [page, ed] = await Promise.all([call("sequence.page", { sequence_id: id }), step ? call("step.editor", { step_id: step }) : null]);
        if (shared("sequence") !== id || shared("step") !== step) return;
        this.page = page;
        if (ed && ed.step.id !== this.ed?.step?.id) this.fill(ed);
        this.ed = ed ? { ...ed, said: this.ed?.step?.id === ed.step.id ? this.ed.said : null } : null;
        this.error = null;
        this.target();
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  fill(ed) {
    this.fi = 0;
    for (const [label, value, placeholder] of ed.fields) this.inputs[label] = this.wire(InputState.new({ placeholder, value }), label);
    this.body.set_value(ed.body ?? "");
    this.voice.set_value(ed.voicemail ?? "");
  }

  command(c, cx) {
    const fields = this.fields();
    switch (c) {
      case "show": return this.load(cx);
      case "edit": { // Enter on a Step: its first field takes the keyboard
        const first = this.ed?.fields?.[0]?.[0];
        if (first) this.fi = 0;
        return first && this.inputs[first].focus();
      }
      case "save": return cx.spawn(async (cx) => this.persist(cx));
      case "close": return cx.spawn(async (cx) => {
        if (!(await this.persist(cx))) return;
        focusMain();
        setShared("typing", null);
        setShared("focus", "list"); // back to the Steps: the edited Step takes the focus
        run("sequences.show");
      });
      case "focus_entry": this.fi = Math.max(0, Math.min(fields.length - 1, this.fi)); this.target(); return cx.notify();
      case "next": case "prev": // the Step editor's fields, else scroll the Sequence's properties
        if (!fields.length) return run(c === "next" ? "app.scroll_down" : "app.scroll_up");
        this.fi = Math.max(0, Math.min(fields.length - 1, this.fi + (c === "next" ? 1 : -1)));
        this.target();
        return cx.notify();
      case "enter": return fields[this.fi]?.[1].focus();
    }
  }

  /** Saves the open Step when it changed; true when stored or unchanged. A refusal keeps the editor with the reason. */
  async persist(cx) {
    const ed = this.ed;
    if (!ed) return true;
    const fields = Object.fromEntries(ed.fields.map(([label]) => [label, this.inputs[label].value().trim()]));
    const r = await call("step.values", { step_id: ed.step.id, fields, body: this.body.value(), voicemail: this.voice.value() })
      .catch((e) => ({ error: String(e) }));
    if (r.error) { ed.said = `Not saved: ${r.error}`; cx.notify(); return false; }
    if (r.same) return true;
    try {
      await call("command.sequences.save_step", { step_id: ed.step.id, ...r.values });
      ed.said = "saved · edits apply to Tasks not yet due · esc back to Steps";
      ed.step = { ...ed.step, ...r.values };
      run("sequences.show");
    } catch (e) {
      ed.said = `Not saved: ${String(e).replace(/^(Error|Refused): /, "")}`;
      cx.notify();
      return false;
    }
    cx.notify();
    return true;
  }

  render() {
    const t = theme();
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
    const segs = (lines) => (lines ?? []).map((ss) => (ss.length ? h_flex().w_full().gap(0).flex_wrap()
      .children(ss.map(([text, hex, bold]) => div().min_w(0).text_color(color(hex)).when(Boolean(bold), (d) => d.font_semibold()).child(text)))
      : div().h(10)));
    if (!this.page) return pane.child(div().p(24).text_color(t.muted).child(this.error ?? "No Sequences yet. n makes one."));
    const ed = this.ed;
    if (!ed) return pane.child(v_flex().id("seq-props").flex_1().overflow_y_scroll().px(20).py(14).gap(2).children(segs(this.page.props)));
    const bad = (ed.said ?? "").startsWith("Not saved");
    // The path (Logseq bullet threading): the focused field's label has a full bar and highlight.
    const focusHere = shared("focus") === "detail", names = this.fields().map(([l]) => l);
    const bar = (el, label) => {
      const on = focusHere && names[this.fi] === label;
      return el.border_l(3).border_color(on ? t.accent : t.background).when(on, (e) => e.bg(t.selected));
    };
    return pane.child(v_flex().id("step-editor").flex_1().min_h(0)
      .on_key_down((ev, cx) => {
        if (ev.keystroke === "escape") this.command("close", cx);
        else if (ev.keystroke === "ctrl-s") this.command("save", cx);
        else return;
        cx.stop_propagation();
      })
      .child(v_flex().id("step-top").flex_1().min_h(0).overflow_y_scroll().px(20).py(14).gap(2)
        .children(segs(ed.head)).child(div().h(8)).children(segs(ed.problems)).children(segs(ed.preview)))
      .child(v_flex().flex_none().gap(6).px(16).py(12).border_t(1).border_color(t.border)
        .children(ed.fields.map(([label]) => bar(h_flex().gap(8).items_center().text_size(12.5), label)
          .child(div().w(64).flex_none().text_color(t.muted).child(label)).child(div().flex_1().min_w(0).child(Input.new(this.inputs[label])))))
        .when(ed.enroll, (e) => e.child(div().text_size(11.5).text_color(t.muted).child("enrolls them on its day · waits for you if they responded")))
        .child(bar(div().text_size(11.5).text_color(t.muted), ed.body_label ?? "Body").child(`${ed.body_label} · fill-ins {first_name} {last_name} {company}`))
        .child(Textarea.new(this.body))
        .when(ed.voicemail !== null, (e) => e.child(bar(div().text_size(11.5).text_color(t.muted), "Voicemail").child("voicemail, left on the second call"))
          .child(Textarea.new(this.voice)))
        .child(div().text_size(11.5).text_color(bad ? t.red : t.muted).child(ed.said ?? HINT))));
  }
}
