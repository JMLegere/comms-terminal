// The Calendar's right pane, as the Console's DayList (D93, D95, D96): the cursor's day ("Monday, November 3") and
// every Calendar Item on it (Events, Tasks, Snoozed wakes, Draft sends, Enrollment starts), or "Nothing on this
// day.". j/k move, Enter opens the Event page, Thread, Task, Draft or Person; o toasts the join link (Ctrl+click
// opens it). Shared "cal_day" holds {day, items} from the Calendar. Element focus: the cursor's Calendar Item is the
// detail column's element; it names itself in the bar and carries the path's accent bar; tab goes back to the grid.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextCommand, theme, shared, setShared, run, toast, focusMain } from "comms";

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const KIND = { event: "Event", task: "Task", draft: "Draft", enrollment: "Enrollment" }; // else a Snoozed Thread waking
const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };

/** "Monday, November 3" for a YYYY-MM-DD day. */
export const heading = (day) => { const [y, m, d] = day.split("-").map(Number), x = new Date(y, m - 1, d); return `${DAYS[x.getDay()]}, ${MONTHS[x.getMonth()]} ${d}`; };

/** Where a Calendar Item opens, as the Console's cal_open: [shared key, value, command]. */
export function opens(i) {
  if (i.kind === "event") return ["push", { page: "event", event: i.event }, "nav.push"];
  if (i.kind === "enrollment") return ["push", { page: "person", person: i.ref }, "nav.push"];
  if (i.kind === "task") return ["task", i.ref, "nav.tasks"];
  if (i.kind === "draft") return i.task ? ["task", i.task, "nav.tasks"] : ["draft", i.ref, "nav.drafts"]; // an email Task's send: its Task (#146)
  return ["thread", i.ref, null]; // a Snoozed wake: the Thread where it lives
}

export default class Day extends View {
  init(_props, cx) {
    this.at = 0;
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  items() { return shared("cal_day")?.items ?? []; }

  /** The bar's line for the focused Calendar Item (element focus): what it is and the keys it takes. */
  target() {
    if (shared("page") !== "calendar" || shared("focus") !== "detail") return;
    const d = shared("cal_day"), i = this.items()[this.at];
    const text = (i?.line?.[0] ?? []).map(([s]) => s).join("").trim();
    setShared("target", i ? { col: "detail", label: `${KIND[i.kind] ?? "Thread"} · ${text}`,
      keys: [["enter", "open"], ...(i.kind === "event" ? [["o", "join"]] : []), ["tab", "grid"], ["esc", "grid"]] }
      : { col: "detail", label: `Day · ${d ? heading(d.day) : ""}`, keys: [["tab", "grid"], ["esc", "grid"]] });
  }

  command(c, cx) {
    const items = this.items();
    if (c === "show") { this.at = 0; this.target(); return cx.notify(); }
    if (c === "focus_entry") { this.at = Math.max(0, Math.min(items.length - 1, this.at)); this.target(); return cx.notify(); }
    if (c === "next" || c === "prev") {
      this.at = Math.max(0, Math.min(items.length - 1, this.at + (c === "next" ? 1 : -1)));
      this.target();
      return cx.notify();
    }
    if (c === "tab") return run("nav.focus_left"); // back to the grid, as the Calendar's tab
    if (c === "enter") c = "open";
    const i = items[this.at];
    if (c === "join") {
      const url = i?.join_url;
      return url ? toast(url, "information", 15) : toast("No join link for this.");
    }
    if (c !== "open" || !i) return;
    const [key, value, then] = opens(i);
    setShared(key, value);
    if (then) return run(then);
    cx.spawn(async () => { // a Thread opens in its Status view
      try {
        const th = (await call("thread.view", { thread_id: value })).view.thread, v = th?.f ?? th;
        run(`nav.${v.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[v.status] ?? "todo"}`);
        run("thread.show");
      } catch (e) {
        toast(String(e), "error");
      }
    });
  }

  render() {
    const t = theme();
    const d = shared("cal_day");
    const items = this.items();
    const color = (hex) => (hex ? t[GREY[hex]] ?? (hex === "#45707a" ? t.blue : hex === "#654735" ? t.foreground : hex) : t.foreground);
    const focused = shared("focus") === "detail";
    return v_flex().id("day").size_full().min_h(0).overflow_y_scroll().p(16).gap(4).bg(t.background).text_size(13)
      .child(div().pb(6).font_semibold().text_color(t.blue).child(d ? heading(d.day) : ""))
      .when(!items.length, (el) => el.child(div().text_color(t.muted).child("Nothing on this day.")))
      .children(items.map((i, n) => v_flex().id(`i-${n}`).px(8).py(4).rounded(4).cursor_pointer()
        .hover((s) => s.bg(t.hover))
        // The path (Logseq bullet threading): the focused Calendar Item has a full bar and highlight.
        .border_l(3).border_color(n === this.at && focused ? t.accent : t.background)
        .when(n === this.at && focused, (e) => e.bg(t.selected))
        .on_click((_e, cx) => { // a click focuses the Calendar Item and takes the keys from any text field
          this.at = n;
          setShared("focus", "detail");
          focusMain();
          this.target();
          cx.notify();
        })
        .children(i.line.map((segs) => h_flex().flex_wrap()
          .children(segs.map(([text, hex, bold]) => div().min_w(0).text_color(color(hex)).when(Boolean(bold), (x) => x.font_semibold()).child(text)))))));
  }
}
