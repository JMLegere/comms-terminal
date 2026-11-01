// Tasks: the due Tasks of every Enrollment, by group. j/k move and the Task fills the right pane. e marks a touch
// done (an email Task is done when its Draft sends), x skips it, 1/2 answer an If that asks Jeremy, n starts a new
// Thread when the reply target was skipped, p opens the Person, / filters by kind or Sequence, y copies for an agent.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

export const KIND = { email: "Email", call: "Call", linkedin: "LinkedIn", task: "Task", action: "Action", if: "If", sms: "SMS" };

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
    run("task.show");
    const at = this.items?.findIndex((it) => it.key === id) ?? -1;
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
  }

  async write(method, params, message) {
    try {
      await call(`command.${method}`, params);
      if (message) toast(message);
    } catch (e) {
      toast(String(e).replace(/^Error: /, ""), "error");
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
      case "next": case "prev": {
        const row = rows[Math.max(0, Math.min(rows.length - 1, i + (name === "next" ? 1 : -1)))];
        return row && this.select(row.id);
      }
      case "done":
        if (!t) return;
        if (t.kind === "email") return toast("An email Task is done when its Draft sends: Enter opens its Draft.");
        if (t.kind === "if") return toast(`Pick a path: 1 ${t.true_label} · 2 ${t.false_label}.`);
        return cx.spawn(async () => this.write("sequences.complete_task", { task_id: t.id },
          t.action_kind === "enroll" ? `Enrolled ${t.person} in ${t.enroll_sequence}.` : `Done: ${t.person}, Day ${t.day} ${KIND[t.kind] ?? t.kind}.`));
      case "skip":
        if (!t) return;
        return cx.spawn(async () => this.write("sequences.skip_task", { task_id: t.id },
          `Skipped: ${t.person}, Day ${t.day} ${KIND[t.kind] ?? t.kind}. The next Step counts from today.`));
      case "pick_first": case "pick_second":
        if (!t || t.kind !== "if" || t.condition !== "jeremy_picks") return;
        return cx.spawn(async () => this.write("sequences.pick", { task_id: t.id, choice: name === "pick_first" ? 1 : 0 },
          `${t.person}: ${t.subject} → ${name === "pick_first" ? t.true_label : t.false_label}.`));
      case "new_thread":
        if (!t || t.flag !== "reply target skipped") return toast("Only a Task whose reply target was skipped starts a new Thread.");
        return cx.spawn(async () => this.write("sequences.task_new_thread", { task_id: t.id }, "This email now starts a new Thread."));
      case "person":
        if (!t) return;
        setShared("person", t.person_id);
        setShared("page", "people");
        return run("person.show");
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
      case "filter": {
        const kinds = [...new Set(this.rows.map((r) => r.kind))];
        const seqs = [...new Map(this.rows.map((r) => [r.sequence_id, r.sequence])).entries()];
        setShared("picker", { title: "Filter Tasks", reply: "tasks.filtered", options: [{ value: "all", label: `All Tasks · ${this.rows.length}` },
          ...kinds.map((k) => ({ value: k, label: KIND[k] ?? k, hint: String(this.rows.filter((r) => r.kind === k).length) })),
          ...seqs.map(([id, n]) => ({ value: `seq:${id}`, label: n, hint: String(this.rows.filter((r) => r.sequence_id === id).length) }))] });
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
    const items = [];
    let group = null;
    for (const r of rows) {
      if (r.group !== group) items.push({ key: `g-${(group = r.group)}`, header: group });
      items.push({ key: r.id, task: r });
    }
    this.items = items;
    const header = (it) => div().h(30).px(8).pt(10).text_size(11).font_medium().text_color(t.muted).child(it.header ?? "");
    const row = (r) => v_flex().h(52).px(8).justify_center().rounded(8).gap(2).cursor_pointer()
      .when(r.id === selected, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
      .child(h_flex().gap(9).items_center()
        .child(div().flex_none().w(56).text_size(11).text_color(t.accent).child(KIND[r.kind] ?? r.kind))
        .child(div().flex_1().min_w(0).truncate().font_medium().text_color(t.foreground).child(r.person ?? ""))
        .child(div().flex_none().text_size(11).text_color(t.dim).child(`Day ${r.day ?? "?"}`)))
      .child(div().pl(65).truncate().text_color(t.muted).child(`${r.sequence ?? ""}${r.subject ? ` · ${r.subject}` : ""}`));
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/list-checks.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Tasks"))
        .child(div().flex_1().text_color(t.dim).child(`· ${rows.length}${this.filter ? " · filtered" : ""}`))
        .child(div().text_size(11).text_color(t.dim).child("e done · x skip · / filter")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !rows.length, (el) => el.child(div().p(16).text_color(t.dim).child("No Tasks due.")))
      .child(div().flex_1().min_h(0).px(6).pb(8).child(
        v_virtual_list("tasks", items.length, items.map((it) => (it.header ? 30 : 52)), (i) => items[i].key,
          (range) => items.slice(range.start, range.end).map((it) => (it.header ? header(it) : row(it.task))))
          .size_full().track_scroll(this.scroll).on_item_click((key) => !key.startsWith("g-") && this.select(key))));
  }
}
