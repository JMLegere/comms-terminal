// The Sequences page's right pane, as the Console's side pane (D113, D118): the selected Sequence (size, edited, use
// when, sends from, Enrollments, keys), or, while the Steps pane has the keys, the selected Step's editor: "Step N ·
// Kind", what would stop it running, a preview filled for an enrolled Person, then its fields for this kind of Step
// (Gap, Time, Reply to, Subject, Move, Pipeline, Stage, Value, Question, Path 1, Path 2, Is, Sequence), its body
// (script, note or letter) and a call's voicemail. ctrl-s saves; leaving a field saves (FR-CONF-3); esc saves and
// goes back to the Steps.
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
    this.inputs = Object.fromEntries(LABELS.map((l) => [l, InputState.new({})]));
    this.body = TextareaState.new({ rows: 8 });
    this.body.set_auto_grow(6, 24);
    this.voice = TextareaState.new({ rows: 3 });
    this.voice.set_auto_grow(2, 10);
    for (const f of [...Object.values(this.inputs), this.body, this.voice]) f.on("blur", (_e, cx) => this.ed && cx.spawn(async (cx) => this.persist(cx)));
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "sequences" && !this.dirty) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
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
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  fill(ed) {
    for (const [label, value, placeholder] of ed.fields) {
      this.inputs[label] = InputState.new({ placeholder, value });
      this.inputs[label].on("blur", (_e, cx) => this.ed && cx.spawn(async (cx) => this.persist(cx)));
    }
    this.body.set_value(ed.body ?? "");
    this.voice.set_value(ed.voicemail ?? "");
  }

  command(c, cx) {
    switch (c) {
      case "show": return this.load(cx);
      case "edit": { // Enter on a Step: its first field takes the keyboard
        const first = this.ed?.fields?.[0]?.[0];
        return first && this.inputs[first].focus();
      }
      case "save": return cx.spawn(async (cx) => this.persist(cx));
      case "close": return cx.spawn(async (cx) => { if (await this.persist(cx)) { focusMain(); run("sequences.show"); } });
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
        .children(ed.fields.map(([label]) => h_flex().gap(8).items_center().text_size(12.5)
          .child(div().w(64).flex_none().text_color(t.muted).child(label)).child(div().flex_1().min_w(0).child(Input.new(this.inputs[label])))))
        .when(ed.enroll, (e) => e.child(div().text_size(11.5).text_color(t.muted).child("enrolls them on its day · waits for you if they responded")))
        .child(div().text_size(11.5).text_color(t.muted).child(`${ed.body_label} · fill-ins {first_name} {last_name} {company}`))
        .child(Textarea.new(this.body))
        .when(ed.voicemail !== null, (e) => e.child(div().text_size(11.5).text_color(t.muted).child("voicemail, left on the second call"))
          .child(Textarea.new(this.voice)))
        .child(div().text_size(11.5).text_color(bad ? t.red : t.muted).child(ed.said ?? HINT))));
  }
}
