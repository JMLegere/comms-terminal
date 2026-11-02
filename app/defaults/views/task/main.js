// One due Task: what to do, for whom, from which Sequence Step, and its brief. An email Task's Draft opens with
// Enter (or the button) in Drafts, where ctrl-enter sends it and so completes the Task.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const KIND = { email: "Email", call: "Call", linkedin: "LinkedIn", task: "Task", action: "Action", if: "If", sms: "SMS" };

export default class Task extends View {
  init(_props, cx) {
    this.task = null;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("task")) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        if (c === "show") this.load(cx);
        if (c === "open") this.open();
      }
    });
  }

  load(cx) {
    const id = shared("task");
    if (!id) {
      this.task = null;
      return cx.notify();
    }
    cx.spawn(async (cx) => {
      try {
        const [all, text] = await Promise.all([call("tasks.list"), call("task.markdown", { task_id: id }).catch(() => "")]);
        if (shared("task") !== id) return;
        this.task = all.find((t) => t.id === id) ?? null;
        this.text = text;
        this.error = this.task ? null : "This Task is no longer due.";
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  open() {
    const t = this.task;
    if (!t?.draft_id) return toast("This Task has no Draft.");
    setShared("draft", t.draft_id);
    setShared("page", "drafts");
    run("draft.show");
    run("draft.edit");
  }

  render() {
    const t = theme();
    const task = this.task;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!task) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Task selected"));
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg("icons/list-checks.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child(`${KIND[task.kind] ?? task.kind} Task`))
        .child(div().flex_1().truncate().text_color(t.dim).child(`· ${task.sequence} · step ${task.position} of ${task.steps}`)))
      .child(v_flex().id("task").flex_1().min_h(0).overflow_y_scroll().px(24).py(18).gap(10)
        .child(div().text_size(18).font_semibold().text_color(t.strong ?? t.foreground).child(task.person ?? ""))
        .when(Boolean(task.organization), (el) => el.child(div().text_color(t.muted).child(String(task.organization?.name ?? task.organization))))
        .when(task.kind === "if" && task.condition === "jeremy_picks", (el) => el.child(div().text_color(t.accent)
          .child(`1 ${task.true_label} · 2 ${task.false_label}`)))
        .when(Boolean(task.draft_id), (el) => el.child(div().id("open-draft").px(10).py(4).rounded(6).bg(t.selected).text_color(t.accent)
          .cursor_pointer().on_click(() => this.open()).child("Open its Draft · Enter")))
        .child(div().text_size(12.5).line_height(1.5).text_color(t.foreground).child(this.text ?? "")));
  }
}
