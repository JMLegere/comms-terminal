// Sequences, as the Console's SequencesPage (D113–D115, D117, D120–D122): two stacked panes, every Sequence on top
// (icon, Sequence, Use when, Enrolled, Due; archived ones folded under "Archived · N") and the selected Sequence's
// Steps below as an outline (On start, the Steps, each If's paths, each path's On completion). The right pane (the
// sequence view) shows the Sequence, or the selected Step's editor while the Steps pane has the keys.
//   Sequences: Enter goes to its Steps (on Archived, folds); n new, R rename, w use when, f sends from, a archive or
//   restore, x delete (an unused one), each undoable.
//   Steps: Enter edits the Step; n adds a Step after it (an Action asks what it does, an If what it checks); x deletes
//   it (z undoes); J/K move it; esc goes back to the Sequences.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const KIND = { email: "Email", call: "Call", linkedin: "LinkedIn", letter: "Letter", action: "Action", if: "If" }; // KIND_LABEL
const ICON = { email: "\u{f01f0}", call: "\u{f0df0}", linkedin: "\u{f033b}", letter: "\u{f0d8c}", action: "\u{f027b}", if: "\u{f0641}",
  sequence: "\u{f027b}", archive: "\u{f003c}", warn: "\u{f002a}" };
const offerUndo = (message, steps) => { setShared("undo", { message, steps }); toast(`${message}  z undo`); };

/** Active Sequences, then the Archived header, then (shown) the archived ones (the Console's rows). */
export function seqRows(all, showArchived) {
  const active = all.filter((q) => !q.archived_at), archived = all.filter((q) => q.archived_at);
  return [...active.map((q) => ({ key: q.id, q })), { key: "archived", archived: archived.length },
    ...(showArchived ? archived.map((q) => ({ key: q.id, q })) : [])];
}

export default class Sequences extends View {
  init(_props, cx) {
    this.rows = [];
    this.showArchived = false;
    this.hi = null;   // the Sequences row
    this.at = 0;      // the Steps outline row
    this.page = null; // the selected Sequence's sequence.page
    setShared("seq_pane", "seqs");
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "sequences") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  items() { return seqRows(this.rows, this.showArchived); }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.rows = await call("sequences.list");
        this.error = null;
        const its = this.items();
        if (!its.some((it) => it.key === this.hi)) this.hi = its[0]?.key ?? null;
        await this.loadSteps(cx);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  async loadSteps(cx, stepId) {
    const q = this.items().find((it) => it.key === this.hi)?.q;
    if (!q) { this.page = null; setShared("sequence", null); setShared("step", null); run("sequence.show"); return; }
    const page = await call("sequence.page", { sequence_id: q.id });
    const keep = stepId ?? (this.page?.sequence?.id === q.id ? this.page.outline[this.at]?.step?.id : null);
    const fresh = this.page?.sequence?.id !== q.id;
    this.page = page;
    const lines = page.outline ?? [];
    const found = keep ? lines.findIndex((l) => l.step?.id === keep) : -1;
    this.at = found >= 0 ? found : fresh ? Math.max(0, lines.findIndex((l) => l.step)) : Math.min(this.at, Math.max(lines.length - 1, 0));
    this.side();
    cx?.notify();
  }

  /** The right pane follows the keys: the Step editor in Steps, else the Sequence. */
  side() {
    if (shared("page") !== "sequences") return;
    setShared("sequence", this.page?.sequence?.id ?? null);
    setShared("step", shared("seq_pane") === "steps" ? this.page?.outline?.[this.at]?.step?.id ?? null : null);
    run("sequence.show");
  }

  pick(title, options, reply, prompt, value, body) {
    setShared("picker", { title, options, reply: `sequences.${reply}`, prompt, value, body });
    run("overlay.open");
  }

  async write(method, params, message) {
    try {
      const r = await call(`command.${method}`, params);
      if (message) toast(message);
      return r ?? null;
    } catch (e) {
      toast(String(e).replace(/^(Error|Refused): /, ""), "error");
      return undefined;
    }
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const steps = shared("seq_pane") === "steps";
    const its = this.items(), here = its.findIndex((it) => it.key === this.hi), q = its[here]?.q;
    const lines = this.page?.outline ?? [], line = lines[this.at], st = line?.step;
    switch (name) {
      case "show": return this.load(cx);
      case "next": case "prev": {
        const by = name === "next" ? 1 : -1;
        if (steps) { this.at = Math.max(0, Math.min(lines.length - 1, this.at + by)); this.side(); return cx.notify(); }
        this.hi = its[Math.max(0, Math.min(its.length - 1, here + by))]?.key ?? this.hi;
        return cx.spawn(async (cx) => this.loadSteps(cx));
      }
      case "enter":
        if (steps) return st && run("sequence.edit");
        if (its[here]?.archived !== undefined) { this.showArchived = !this.showArchived; return cx.notify(); }
        if (!q) return;
        setShared("seq_pane", "steps");
        this.side();
        return cx.notify();
      case "escape":
        if (!steps) return run("nav.back");
        setShared("seq_pane", "seqs");
        this.side();
        return cx.notify();
      case "new":
        if (!steps) return this.pick("New Sequence", [], "named", "name, e.g. Trade show follow-up");
        return this.newStep(line, cx);
      case "named":
        if (!answer.text) return;
        return cx.spawn(async (cx) => {
          const id = await this.write("sequences.create_sequence", { name: answer.text }, `Created ${answer.text}. n adds the first Step.`);
          if (!id) return;
          this.rows = await call("sequences.list");
          this.hi = String(id);
          setShared("seq_pane", "steps");
          await this.loadSteps(cx);
        });
      case "kind-picked":
        if (answer.value === "action") return cx.spawn(async () => {
          const ch = await call("step.choices");
          this.pick("Action does", Object.entries(ch.actions).map(([value, label]) => ({ value, label })), "action-picked");
        });
        if (answer.value === "if") {
          if ((this.newPath?.section ?? "body") === "start") return toast("An If cannot be in On start.", "warning");
          return cx.spawn(async () => {
            const ch = await call("step.choices");
            this.pick("If checks", Object.entries(ch.conditions).filter(([k]) => k !== "end_reason" || this.newPath?.section === "completion")
              .map(([value, label]) => ({ value, label })), "if-picked");
          });
        }
        return this.addStep(answer.value, {}, cx);
      case "action-picked": return this.addStep("action", { action_kind: answer.value }, cx);
      case "if-picked": return this.addStep("if", { condition: answer.value }, cx);
      case "delete":
        if (!steps) return q && this.deleteSequence(q, cx);
        if (!st) return;
        return cx.spawn(async (cx) => { // as the Console: delete, then z undo restores it
          const r = await this.write("sequences.delete_step", { step_id: st.id });
          if (!r) return;
          const [moved, undo] = r;
          offerUndo(`Step ${st.position} ${st.kind === "if" ? "and its paths " : ""}deleted. ${moved} Enrollment${moved === 1 ? "" : "s"} on it moved on.`,
            [["sequences.restore_step", { undo }]]);
          await this.loadSteps(cx);
        });
      case "move_down": case "move_up":
        if (!steps || !st) return;
        return cx.spawn(async (cx) => {
          if ((await this.write("sequences.move_step", { step_id: st.id, delta: name === "move_down" ? 1 : -1 })) !== undefined) await this.loadSteps(cx, st.id);
        });
      case "rename": return q && this.pick(`Rename ${q.name}`, [], "renamed", q.name, q.name);
      case "renamed": return answer.text && answer.text !== q.name && cx.spawn(async () => this.write("sequences.rename_sequence", { sequence_id: q.id, name: answer.text }));
      case "describe": return q && this.pick(`Use ${q.name} when`, [], "described", q.description || "e.g. they replied and a call is booked", q.description ?? "");
      case "described": return answer.text && answer.text !== q.description && cx.spawn(async () => this.write("sequences.describe_sequence", { sequence_id: q.id, description: answer.text }));
      case "sends_from":
        if (!q) return;
        return cx.spawn(async () => {
          const conns = (await call("connections.list")).filter((c) => c.platform === "gmail" || c.platform === "fastmail");
          if (!conns.length) return;
          this.pick(`${q.name} sends from`, conns.map((c) => ({ value: c.id, label: `${c.platform === "gmail" ? "Gmail" : "Fastmail"} · ${c.external_id}` })), "sends-picked");
        });
      case "sends-picked": return answer.value !== q.connection_id && cx.spawn(async () => this.write("sequences.set_connection", { sequence_id: q.id, connection_id: answer.value }));
      case "archive": return q && this.archive(q, cx);
      case "archive-picked": return cx.spawn(async () => {
        const live = q.active + q.paused;
        const undo = await this.write("sequences.archive_sequence", { sequence_id: q.id, cancel_live: answer.value === "cancel" });
        if (undo === undefined) return;
        offerUndo(`Archived ${q.name}${answer.value === "cancel" ? `: ${live} Enrollments Canceled.` : "."}`,
          [["sequences.restore_sequence", { sequence_id: q.id, undo: undo ?? [] }]]);
      });
    }
  }

  /** n in Steps: after the selected Step on its path, or last on the selected heading's path (the Console's new_step). */
  newStep(line, cx) {
    if (!this.page) return;
    if (line?.step?.kind === "if") return toast("Nothing follows an If on its path: select ✓ or ✗ under it to add to a path.", "warning");
    this.newPath = line?.path ?? { section: "body" };
    this.newAfter = line?.step?.position ?? null;
    this.pick("New Step", Object.entries(KIND).map(([value, label]) => ({ value, label: `${ICON[value]} ${label}` })), "kind-picked");
  }

  addStep(kind, how, cx) {
    const path = this.newPath ?? {};
    const params = { sequence_id: this.page.sequence.id, kind, section: path.section ?? "body", parent_step_id: path.parent_step_id ?? null,
      branch: path.branch ?? null, ...how };
    if (this.newAfter != null) params.after = this.newAfter;
    cx.spawn(async (cx) => {
      const id = await this.write("sequences.add_step", params);
      if (id === undefined) return;
      setShared("seq_pane", "steps");
      await this.loadSteps(cx, id);
    });
  }

  archive(q, cx) {
    if (q.archived_at) return cx.spawn(async () => {
      const r = await this.write("sequences.restore_sequence", { sequence_id: q.id, undo: [] });
      if (r !== undefined) offerUndo(`Restored ${q.name}: it takes new Enrollments again.`, [["sequences.archive_sequence", { sequence_id: q.id, cancel_live: false }]]);
    });
    const live = q.active + q.paused;
    this.pick(`Archive ${q.name}`, live ? [{ value: "cancel", label: `Cancel the ${live} live Enrollments · Finished: Canceled` },
      { value: "finish", label: "Let them finish · no new Enrollments" }] : [{ value: "finish", label: "Archive · no new Enrollments" }],
    "archive-picked", null, null, [{ text: "Archived Sequences take no new Enrollments and can be restored (a).", dim: true }]);
  }

  deleteSequence(q, cx) {
    cx.spawn(async () => { // as the Console: delete, then z undo puts it back; a used one is refused
      try {
        const rows = await call("command.sequences.delete_sequence", { sequence_id: q.id });
        offerUndo(`Deleted ${q.name}.`, [["sequences.undelete_sequence", { rows }]]);
      } catch (e) {
        toast(`Not deleted: ${String(e).replace(/^(Error|Refused): /, "")} Archive it (a).`, "error");
      }
    });
  }

  render() {
    const t = theme();
    const steps = shared("seq_pane") === "steps";
    const its = this.items();
    const active = this.rows.filter((q) => !q.archived_at).length, archived = this.rows.length - active;
    const pill = (text) => div().flex_none().px(6).rounded(4).bg(t.blue).text_color(t.background).font_semibold().child(text);
    const pane = (on) => v_flex().rounded(6).border(1).border_color(on ? t.accent : t.border).min_h(0);
    const seqRow = (it) => {
      const el = h_flex().id(`q-${it.key}`).h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer()
        .when(it.key === this.hi, (e) => e.bg(t.selected)).hover((s) => s.bg(t.hover))
        .on_click((_e, cx) => { this.hi = it.key; setShared("seq_pane", "seqs"); cx.spawn(async (cx) => this.loadSteps(cx)); });
      if (it.archived !== undefined) return el.child(div().w(16).text_color(t.muted).child(this.showArchived ? "\u{f0140}" : "\u{f0142}"))
        .child(div().text_color(t.muted).child(`Archived · ${it.archived}`));
      const q = it.q;
      const enrolled = q.active || q.paused ? `${q.active} active${q.paused ? ` · ${q.paused} paused` : ""}` : "never used";
      return el.child(div().w(16).flex_none().text_color(q.archived_at ? t.muted : t.foreground).child(q.archived_at ? ICON.archive : ICON.sequence))
        .child(div().w(150).flex_none().truncate().font_semibold().text_color(q.archived_at ? t.muted : t.strong ?? t.foreground).child(q.name))
        .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(q.description ?? ""))
        .child(div().w(110).flex_none().truncate().text_color(q.active || q.paused ? t.foreground : t.muted).child(enrolled))
        .child(div().w(24).flex_none().text_color(q.due_today ? t.blue : t.muted).child(q.due_today ? String(q.due_today) : "·"));
    };
    const lines = this.page?.outline ?? [];
    const stepRow = (l, n) => {
      const el = h_flex().id(`s-${n}`).h(26).pr(8).gap(8).rounded(4).items_center().cursor_pointer()
        .when(steps && n === this.at, (e) => e.bg(t.selected)).hover((s) => s.bg(t.hover))
        .on_click((_e, cx) => { this.at = n; setShared("seq_pane", "steps"); this.side(); cx.notify(); });
      const pad = 8 + 14 * (l.depth ?? 0);
      if (!l.step) return el.pl(pad).child(div().font_semibold().text_color(t.muted).child(l.label ?? ""));
      const s = l.step;
      return el.pl(8).child(div().w(22).flex_none().text_color(t.muted).child(String(s.position)))
        .child(div().w(16).flex_none().text_color(t.foreground).child(ICON[s.kind] ?? ""))
        .child(h_flex().w(170).flex_none().pl(14 * (l.depth ?? 0)).gap(4).overflow_hidden()
          .child(div().truncate().text_color(t.foreground).child(l.label ?? ""))
          .when(l.reply != null, (e) => e.child(div().flex_none().text_color(t.muted).child(`↩ ${l.reply}`)))
          .when(Boolean(l.problem), (e) => e.child(div().flex_none().text_color(t.red).child(ICON.warn))))
        .child(div().w(30).flex_none().text_color(t.foreground).child(`+${s.gap_days}`))
        .child(div().w(44).flex_none().text_color(s.at_time ? t.foreground : t.muted).child(s.at_time || "·"))
        .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(l.what ?? ""));
    };
    const q = this.page?.sequence;
    return v_flex().size_full().min_h(0).gap(6).p(2).bg(t.background).text_size(13)
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_color(t.danger).child(this.error ?? "")))
      .child(pane(!steps).flex_none().max_h(300)
        .child(h_flex().h(26).px(8).gap(8).items_center().child(pill(`${ICON.sequence} sequences`))
          .child(div().text_color(t.muted).child(`${active} active · ${archived} archived`)))
        .child(v_flex().id("seqs").min_h(0).overflow_y_scroll().px(2).pb(4).children(its.map(seqRow))))
      .child(pane(steps).flex_1()
        .child(h_flex().h(26).px(8).gap(8).items_center().child(pill(`${ICON.sequence} steps${q ? ` · ${q.name}` : ""}`)))
        .child(v_flex().id("steps").flex_1().min_h(0).overflow_y_scroll().px(2).children(lines.map(stepRow)))
        .when(Boolean(q), (e) => e.child(div().flex_none().px(8).py(4).border_t(1).border_color(t.border).text_size(11.5).text_color(t.muted)
          .child(`${(this.page.steps ?? []).length} Steps · ${(this.rows.find((r) => r.id === q.id) ?? {}).active ?? 0} active · n on a heading adds to that path · edits apply to Tasks not yet due`))));
  }
}
