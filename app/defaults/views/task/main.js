// One due Task, as the Console's TaskPane (FR-SEQ-16, D109): "Day N · Kind · Person", what to do for this kind of
// Task (call script and voicemail, LinkedIn move, letter, If paths, Action, Enroll, flags with their guidance), the
// hint line, and the properties column (task, enrollment, next, person, organization) with its Context sections.
// An email Task with a New Thread Draft shows the Draft editor in place; Enter (open) focuses it, and on a Reply
// Draft opens its Thread's reply box (D110). Element focus: the Task is the detail column's one element; j/k scroll
// it a page (its guidance and properties are lines, not a list), Enter opens it as above.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, open, focusMain } from "comms";

const SOURCES = ["attio", "tana", "linear"];

/** Console segment lines ([text, colour, bold, link]) as rows; a blank line keeps its height. A person or org link
 * opens that page on the stack; a URL opens on Ctrl+click (Cmd+click on macOS), as message links do (D44). */
export function segLines(t, lines, key = "s", orgs = {}) {
  const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
  const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
  const follow = (link, ev) => {
    const push = (entry) => { setShared("push", entry); run("nav.push"); };
    if (link.startsWith("person:")) push({ page: "person", person: link.slice(7) });
    else if (link.startsWith("org:")) push({ page: "organization", organization: { domain: link.slice(4), id: orgs[link.slice(4)] ?? null } });
    else if (ev?.modifiers?.control || ev?.modifiers?.platform) open(link);
  };
  return (lines ?? []).map((segs, r) => (segs.length ? h_flex().w_full().gap(0).flex_wrap()
    .children(segs.map(([text, hex, bold, link], i) => div().min_w(0).text_color(link ? t.blue : color(hex)).when(Boolean(bold), (d) => d.font_semibold())
      .when(Boolean(link), (d) => d.id(`${key}-${r}-${i}`).cursor_pointer().on_click((ev) => follow(link, ev))).child(text)))
    : div().h(10)));
}

export default class Task extends View {
  init(_props, cx) {
    this.pane = null;
    this.context = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("task")) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        if (c === "show") this.load(cx);
        if (c === "open" || c === "enter") this.open(cx);
        if (c === "focus_entry") this.target(cx);
        if (c === "next" || c === "prev") run(`app.scroll_${c === "next" ? "down" : "up"}`); // no elements to walk: a page
      }
    });
  }

  load(cx) {
    const id = shared("task");
    if (!id) {
      this.pane = null;
      return cx.notify();
    }
    cx.spawn(async (cx) => {
      try {
        const pane = await call("task.pane", { task_id: id });
        if (shared("task") !== id) return;
        const fresh = this.pane?.id !== id;
        this.pane = { ...pane, id };
        this.error = null;
        if (pane.draft && !pane.draft.thread_id) { // a New Thread Draft: the real Draft editor, in place
          setShared("draft", pane.draft.id);
          setShared("detail", "draft");
          run("draft.show");
        }
        if (fresh) this.loadContext(id, pane, cx);
        // A Task loaded while the focus is on it names itself (not once the Draft editor took its place).
        if (shared("focus") === "detail" && shared("detail_view") === "task" && shared("detail") !== "draft") this.target(cx);
      } catch (e) {
        this.pane = null;
        this.error = /no due Task/.test(String(e)) ? "This Task is no longer due." : String(e);
      }
      cx.notify();
    });
  }

  loadContext(id, pane, cx) {
    this.context = [];
    for (const source of SOURCES) call("context.section", { source, emails: pane.emails ?? [], domains: pane.domains ?? [] })
      .then((section) => { if (section && shared("task") === id) { this.context = [...this.context, section]; cx.notify(); } })
      .catch(() => {});
  }

  /** The bar's line while the Task has the focus (element focus): "Task · Day N · Kind · Person" and its keys. */
  target(cx) {
    const p = this.pane;
    if (!p) return;
    const label = (p.head ?? []).flat().map((seg) => seg[0]).join("");
    setShared("target", { col: "detail", label: `Task · ${label}`,
      keys: [["enter", "open"], ["e", "done"], ["x", "skip"], ["p", "person"], ["y", "copy"], ["j", "scroll"], ["esc", "list"]] });
    cx?.notify();
  }

  open(cx) {
    const d = this.pane?.draft;
    if (!d) return toast("This Task has no Draft.");
    if (!d.thread_id) return run("draft.edit");
    cx.spawn(async () => {
      try {
        const th = (await call("thread.view", { thread_id: d.thread_id })).view.thread, v = th?.f ?? th;
        const view = v.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[v.status] ?? "todo";
        setShared("thread", d.thread_id);
        run(`nav.${view}`);
        run("thread.show");
        run("thread.open_reply");
      } catch (e) {
        toast(String(e), "error");
      }
    });
  }

  render() {
    const t = theme();
    const p = this.pane;
    const root = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!p) return root.child(div().p(24).text_color(t.muted).child(this.error ?? "No Task selected."));
    // A click on the Task focuses it and takes the keys from any text field; the bar marks it while focused.
    const focus = (_e, cx) => { setShared("focus", "detail"); focusMain(); this.target(cx); };
    return root.child(h_flex().flex_1().min_h(0).items_start()
      .child(v_flex().id("task-main").flex_1().min_w(0).h_full().border_l(3)
        .border_color(shared("focus") === "detail" ? t.accent : t.background).on_click(focus)
        .child(v_flex().id("task-timeline").flex_1().min_h(0).overflow_y_scroll().px(20).py(14).gap(2)
          .children(segLines(t, p.head, "h")).child(div().h(10)).children(segLines(t, p.body, "b")))
        .child(div().flex_none().px(20).py(8).border_t(1).border_color(t.border).text_size(11.5).text_color(t.muted).child(p.hint ?? "")))
      .child(v_flex().id("task-props").w(300).flex_none().h_full().overflow_y_scroll().px(16).py(14).gap(2).border_l(1)
        .border_color(t.border).text_size(12.5)
        .children(segLines(t, p.props, "p"))
        .children(this.context.map((c) => v_flex().mt(12).p(8).gap(2).rounded(6).border(1).border_color(t.border)
          .child(div().text_color(t.muted).child(c.title)).children(segLines(t, c.lines, `c${c.title}`))))));
  }
}
