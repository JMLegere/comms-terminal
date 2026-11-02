// One Sequence's Steps as an outline: each path (body, On start, On completion, an If's branches) and its Steps.
// J/K pick a row; N adds a Step after it (or onto a path's header), X deletes the picked Step, ctrl-j/ctrl-k move
// it, Enter edits it below: its gap, subject and brief (ctrl-s saves, Escape leaves). Problems that would stop it
// running show at the top.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, TextareaState, Textarea } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain, toast } from "comms";

const KIND = { email: "Email", call: "Call", linkedin: "LinkedIn", letter: "Letter", action: "Action", if: "If" };

export default class Sequence extends View {
  init(_props, cx) {
    this.page = null;
    this.at = 0;
    this.editing = null;
    this.subject = InputState.new({ placeholder: "subject" });
    this.gap = InputState.new({ placeholder: "business days after the Step before" });
    this.body = TextareaState.new({ placeholder: "the brief, with {first_name} and other fill-ins", rows: 8 });
    this.body.set_auto_grow(6, 24);
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "sequences" && !this.editing) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    const id = shared("sequence");
    if (!id) return;
    cx.spawn(async (cx) => {
      try {
        const page = await call("sequence.page", { sequence_id: id });
        if (shared("sequence") !== id) return;
        if (this.page?.sequence?.id !== id) { this.at = 0; this.editing = null; }
        this.page = page;
        this.at = Math.min(this.at, Math.max((page.outline ?? []).length - 1, 0));
        this.error = null;
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  async write(method, params, message) {
    try {
      const r = await call(`command.${method}`, params);
      if (message) toast(message);
      return r ?? null;
    } catch (e) {
      toast(String(e).replace(/^Error: /, ""), "error");
      return undefined;
    }
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const rows = this.page?.outline ?? [];
    const row = rows[this.at];
    const q = this.page?.sequence;
    switch (name) {
      case "show": return this.load(cx);
      case "next": case "prev":
        this.at = Math.max(0, Math.min(rows.length - 1, this.at + (name === "next" ? 1 : -1)));
        return cx.notify();
      case "new_step":
        if (!q) return;
        setShared("picker", { title: "New Step", reply: "sequence.kind-picked", options: Object.entries(KIND).map(([value, label]) => ({ value, label })) });
        return run("overlay.open");
      case "kind-picked": {
        const path = row?.path ?? {};
        const params = { sequence_id: q.id, kind: answer.value, section: path.section ?? "body",
          parent_step_id: path.parent_step_id ?? null, branch: path.branch ?? null };
        if (row?.step) params.after = row.step.position;
        return cx.spawn(async () => this.write("sequences.add_step", params, `${KIND[answer.value]} Step added: Enter edits it.`));
      }
      case "delete_step":
        if (!row?.step) return;
        return cx.spawn(async () => this.write("sequences.delete_step", { step_id: row.step.id }, "Step deleted."));
      case "move_down": case "move_up":
        if (!row?.step) return;
        return cx.spawn(async () => {
          if ((await this.write("sequences.move_step", { step_id: row.step.id, delta: name === "move_down" ? 1 : -1 })) !== undefined)
            this.at = Math.max(0, Math.min(rows.length - 1, this.at + (name === "move_down" ? 1 : -1)));
        });
      case "edit":
        if (!row?.step) return;
        this.editing = row.step;
        this.subject.set_value(row.step.subject ?? "");
        this.gap.set_value(String(row.step.gap_days ?? 0));
        this.body.set_value(row.step.body ?? "");
        this.body.focus();
        return cx.notify();
      case "save": return cx.spawn(async (cx) => this.save(cx));
      case "close": this.editing = null; focusMain(); return this.load(cx);
    }
  }

  async save(cx) {
    const s = this.editing;
    if (!s) return;
    const gap = this.gap.value().trim();
    if (!/^\d+$/.test(gap)) return toast("Not saved: the gap is a whole number of business days.", "error");
    if ((await this.write("sequences.save_step", { step_id: s.id, subject: this.subject.value(), body: this.body.value(), gap_days: +gap },
      "Step saved; Tasks already due keep their content.")) !== undefined) {
      this.editing = null;
      focusMain();
      this.load(cx);
    }
  }

  render() {
    const t = theme();
    const p = this.page;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!p?.sequence) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Sequence selected"));
    const rows = p.outline ?? [];
    const s = this.editing;
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg("icons/workflow.svg").size(13).text_color(t.accent))
        .child(div().flex_1().font_medium().text_color(t.foreground).child(p.sequence.name))
        .child(div().text_size(11).text_color(t.dim).child("J/K pick · N new · X delete · Enter edit")))
      .child(v_flex().id("steps").flex_1().min_h(0).overflow_y_scroll().px(16).py(12).gap(2)
        .children((p.problems ?? []).map((x) => div().pb(4).text_size(12).text_color(t.danger).child(typeof x === "string" ? x : x.message ?? JSON.stringify(x))))
        .children(rows.map((r, n) => {
          const st = r.step;
          return h_flex().id(`o-${n}`).h(30).pl(8 + 16 * (r.depth ?? 0)).pr(8).gap(8).rounded(6).items_center().cursor_pointer()
            .when(n === this.at, (el) => el.bg(t.selected)).hover((x) => x.bg(t.hover))
            .on_click((_e, cx) => { this.at = n; cx.notify(); })
            .child(st
              ? h_flex().flex_1().min_w(0).gap(8)
                .child(div().w(64).flex_none().text_size(11.5).text_color(st.needs_edit ? t.danger : t.accent).child(KIND[st.kind] ?? st.kind))
                .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(st.subject || st.true_label && `${st.true_label} / ${st.false_label}` || st.action_kind || ""))
                .child(div().flex_none().text_size(11).text_color(t.dim).child(`+${st.gap_days}d`))
              : div().text_size(11.5).font_medium().text_color(t.dim).child(r.label ?? ""));
        }))
        .when(Boolean(s), (el) => el.child(v_flex().mt(12).p(12).gap(8).rounded(10).border(1).border_color(t.accent)
          .on_key_down((ev, cx) => {
            if (ev.keystroke === "escape") this.command("close", cx);
            else if (ev.keystroke === "ctrl-s") this.command("save", cx);
            else return;
            cx.stop_propagation();
          })
          .child(div().text_size(11.5).font_medium().text_color(t.dim).child(`Edit ${KIND[s.kind] ?? s.kind} Step · ctrl-s saves · Escape leaves`))
          .child(h_flex().gap(8).items_center().child(div().w(60).text_size(12).text_color(t.dim).child("Gap")).child(div().flex_1().child(Input.new(this.gap))))
          .child(h_flex().gap(8).items_center().child(div().w(60).text_size(12).text_color(t.dim).child("Subject")).child(div().flex_1().child(Input.new(this.subject))))
          .child(Textarea.new(this.body)))));
  }
}
