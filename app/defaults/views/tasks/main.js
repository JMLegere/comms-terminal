// Tasks, as the Console's TasksPage (D107, D111, D112): due Tasks under OVERDUE / DUE TODAY / LATER TODAY, one row
// each (kind, Person with ⚠ when flagged, Day N, Sequence, When), "N due · / filters by kind or Sequence" under them.
// j/k move and the Task fills the right pane. e marks a touch done (an email Task is done when its Draft sends), x
// skips it, 1/2 answer an If that asks Jeremy: each is undoable (z) and the next Task is selected. n starts a new
// Thread when the reply target was skipped, p opens the Person, / filters by kind or Sequence, y copies for an agent.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, focusMain, keys } from "comms";

export const KIND = { email: "Email", call: "Call", linkedin: "LinkedIn", letter: "Letter", action: "Action", if: "If" }; // KIND_LABEL
const I = { email: "\u{f01f0}", call: "\u{f0df0}", linkedin: "\u{f033b}", letter: "\u{f0d8c}", action: "\u{f027b}", if: "\u{f0641}",
  warn: "\u{f002a}", sequence: "\u{f027b}", tasks: "\u{f05c7}" };
const pad = (n) => String(n).padStart(2, "0");
// The one undo (nav owns z), as thread/main.js offerUndo.
const offerUndo = (message, steps) => { setShared("undo", { message, steps }); toast(`${message}  z undo`); };

/** The When column: red "Nd" overdue, "HH:MM" later today, blank due today. */
export const whenOf = (r) => r.group === "overdue" ? `${r.age}d`
  : r.group === "later today" ? (() => { const d = new Date(r.due_at); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; })() : "";

export default class Tasks extends View {
  init(_props, cx) {
    this.rows = [];
    this.filter = null;
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "tasks") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  shown() {
    const f = this.filter;
    return f ? this.rows.filter((t) => (f.startsWith("seq:") ? t.sequence_id === f.slice(4) : t.kind === f)) : this.rows;
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const was = this.shown().findIndex((t) => t.id === shared("task"));
        this.rows = await call("tasks.list");
        this.error = null;
        const rows = this.shown();
        if (shared("page") === "tasks" && !rows.some((t) => t.id === shared("task")))
          this.select(rows[Math.max(0, Math.min(was, rows.length - 1))]?.id ?? null);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(id) {
    setShared("task", id);
    setShared("detail", null); // the task pane; it swaps in the Draft editor for a New Thread Draft
    run("task.show");
    const at = this.items?.findIndex((it) => it.key === id) ?? -1;
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
    this.target();
  }

  /** The bar's line for the focused row (element focus): the Task and the keys its page binds for it. */
  target() {
    if (shared("page") !== "tasks" || shared("focus") === "detail") return;
    const t = this.rows.find((x) => x.id === shared("task"));
    if (!t) return;
    const takes = (c) => (c !== "tasks.pick_first" && c !== "tasks.pick_second") || (t.kind === "if" && t.condition === "jeremy_picks");
    const bound = Object.entries((keys() ?? {})["page:tasks"] ?? {})
      .filter(([k, c]) => k.length === 1 && c.startsWith("tasks.") && !/\.(next|prev)$/.test(c) && takes(c))
      .map(([k, c]) => [k, c === "tasks.pick_first" ? t.true_label : c === "tasks.pick_second" ? t.false_label : c.split(".")[1].replace(/_/g, " ")]);
    setShared("target", { col: "list", label: `Task · ${t.person ?? ""} · Day ${t.day} ${KIND[t.kind] ?? t.kind}`, keys: [["enter", "open"], ...bound] });
  }

  /** The Task below, else the one above: selected once this one leaves (the Console's _next_after). */
  nextAfter(t) {
    const rows = this.shown(), i = rows.findIndex((x) => x.id === t.id);
    return (rows[i + 1] ?? rows[i - 1])?.id ?? null;
  }

  /** A Task write, undoable with z when the core hands back what undo_task needs. */
  async write(method, params, message, t) {
    const next = t && this.nextAfter(t);
    try {
      const undo = await call(`command.${method}`, params);
      if (undo && t) offerUndo(message, [["sequences.undo_task", { undo }]]);
      else if (message) toast(message);
      if (t) this.select(next);
    } catch (e) {
      toast(String(e).replace(/^Error: /, "").replace(/^Refused: /, "Not enrolled: "), "error");
    }
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const rows = this.shown();
    const i = rows.findIndex((t) => t.id === shared("task"));
    const t = rows[i];
    switch (name) {
      case "show": return this.load(cx);
      case "focus_entry": return this.target();
      case "next": case "prev": {
        const row = rows[Math.max(0, Math.min(rows.length - 1, i + (name === "next" ? 1 : -1)))];
        return row && this.select(row.id);
      }
      case "done":
        if (!t) return;
        if (t.kind === "email") return toast("An email Task is done when its Draft sends: ctrl+enter in the Draft, or enter to open its Thread.");
        if (t.kind === "if") return toast(`Pick a path: 1 ${t.true_label} · 2 ${t.false_label}.`);
        return cx.spawn(async () => this.write("sequences.complete_task", { task_id: t.id },
          t.action_kind === "enroll" ? `Enrolled ${t.person} in ${t.enroll_sequence}.` : `Done: ${t.person}, Day ${t.day} ${KIND[t.kind] ?? t.kind}.`, t));
      case "skip":
        if (!t) return;
        return cx.spawn(async () => this.write("sequences.skip_task", { task_id: t.id },
          `Skipped: ${t.person}, Day ${t.day} ${KIND[t.kind] ?? t.kind}. The next Step counts from today.`, t));
      case "pick_first": case "pick_second":
        if (!t || t.kind !== "if" || t.condition !== "jeremy_picks") return;
        return cx.spawn(async () => this.write("sequences.pick", { task_id: t.id, choice: name === "pick_first" ? 1 : 0 },
          `${t.person}: ${t.subject} → ${name === "pick_first" ? t.true_label : t.false_label}.`, t));
      case "new_thread":
        if (!t || t.flag !== "reply target skipped") return toast("Only a Task whose reply target was skipped starts a new Thread.");
        return cx.spawn(async () => this.write("sequences.task_new_thread", { task_id: t.id }, "This email now starts a new Thread."));
      case "person":
        if (!t) return;
        setShared("push", { page: "person", person: t.person_id }); return run("nav.push");
      case "copy":
        if (!t) return;
        return cx.spawn(async (cx) => {
          try {
            cx.write_to_clipboard(await call("task.markdown", { task_id: t.id }));
            toast("Copied the Task for an agent.");
          } catch (e) {
            toast(String(e), "error");
          }
        });
      case "filter": { // every kind, with its count (zero too), then each Sequence (the Console's action_filter)
        const n = (f) => this.rows.filter(f).length;
        const seqs = [...new Map(this.rows.map((r) => [r.sequence_id, r.sequence])).entries()];
        setShared("picker", { title: "Filter Tasks", reply: "tasks.filtered", options: [{ value: "all", label: `All Tasks · ${this.rows.length}` },
          ...Object.entries(KIND).map(([k, label]) => ({ value: k, label: `${I[k]} ${label} · ${n((r) => r.kind === k)}` })),
          ...seqs.map(([id, name]) => ({ value: `seq:${id}`, label: `${I.sequence} ${name} · ${n((r) => r.sequence_id === id)}` }))] });
        return run("overlay.open");
      }
      case "filtered":
        this.filter = answer.value === "all" ? null : answer.value;
        return this.load(cx);
    }
  }

  render() {
    const t = theme();
    const rows = this.shown();
    const selected = shared("task");
    const focusHere = (shared("focus") ?? "list") === "list";
    const items = [];
    let group = null;
    for (const r of rows) {
      if (r.group !== group) items.push({ key: `g-${(group = r.group)}`, header: group.toUpperCase() });
      items.push({ key: r.id, task: r });
    }
    this.items = items;
    const f = this.filter;
    const title = !f ? "due" : f.startsWith("seq:") ? (this.rows.find((r) => r.sequence_id === f.slice(4))?.sequence ?? "") : (KIND[f] ?? f).toLowerCase();
    const header = (it) => h_flex().h(26).px(8).items_center().child(div().w(16)).child(div().font_semibold().text_color(t.muted).child(it.header));
    // The path (Logseq bullet threading): full bar and highlight on the focused row, a thin bar while the focus is
    // further in (the Task).
    const row = (r) => h_flex().h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer().hover((s) => s.bg(t.hover))
      .border_l(3).border_color(r.id === selected ? t.accent : t.background)
      .when(r.id === selected && focusHere, (el) => el.bg(t.selected))
      .when(r.id === selected && !focusHere, (el) => el.border_l(1))
      .child(div().w(16).flex_none().text_color(t.foreground).child(I[r.kind] ?? ""))
      .when(Boolean(r.flag), (el) => el.child(div().flex_none().text_color(t.red).child(I.warn)))
      .child(div().w(160).flex_none().truncate().font_semibold().text_color(t.strong ?? t.foreground).child(r.person ?? ""))
      .child(div().w(52).flex_none().text_color(t.foreground).child(`Day ${r.day}`))
      .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(r.sequence ?? ""))
      .child(div().flex_none().text_color(r.group === "overdue" ? t.red : t.muted).child(whenOf(r)));
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child(I.tasks))
        .child(div().font_medium().text_color(t.foreground).child(`tasks · ${title}`)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !rows.length, (el) => el.child(div().p(16).text_color(t.muted)
        .child("All caught up: no Tasks due. Tasks come due from Enrollments; enroll People from People (s).")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("tasks", items.length, 26, (i) => items[i].key,
          (range) => items.slice(range.start, range.end).map((it) => (it.header ? header(it) : row(it.task))))
          .size_full().track_scroll(this.scroll).on_item_click((key, cx) => {
            if (key.startsWith("g-")) return; // a group header is blank space
            setShared("focus", "list"); // a click focuses the row and takes the keys from any text field
            focusMain();
            this.select(key);
            cx.notify();
          })))
      .when(rows.length > 0, (el) => el.child(div().flex_none().px(12).py(6).border_t(1).border_color(t.border).text_size(11.5)
        .text_color(t.muted).child(`${rows.length} due · / filters by kind or Sequence`)));
  }
}
