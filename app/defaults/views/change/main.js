// One change from History (ADR 0021), as the Console's HistoryPage side pane: its words in full, Source, time,
// whether it was undone, and why it cannot be undone when it cannot. Shared "command" is the selected row.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { nextCommand, theme, shared } from "comms";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");

/** The pane's label/value lines for one Command. */
export function changeLines(c) {
  if (!c) return [];
  const at = new Date(c.at);
  return [["Source", c.source_name], ["When", `${DAYS[at.getDay()]} ${MONTHS[at.getMonth()]} ${at.getDate()} ${pad(at.getHours())}:${pad(at.getMinutes())}`],
    ...(c.undone ? [["Undone", "yes"]] : []), ...(c.undoes ? [["Undoes", "an earlier change"]] : []),
    ...(c.reason ? [["No undo", c.reason]] : [])];
}

export default class Change extends View {
  init(_props, cx) {
    cx.spawn(async (cx) => {
      while (true) if ((await nextCommand()) === "show") cx.notify();
    });
  }

  render() {
    const t = theme();
    const c = shared("command");
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13).p(20).gap(14)
      .child(div().text_size(16).font_semibold().text_color(c?.undone ? t.muted : t.accent).child(c?.label ?? ""))
      .children(changeLines(c).map(([k, v]) => h_flex().gap(12)
        .child(div().w(70).flex_none().text_color(t.muted).child(k))
        .child(div().flex_1().min_w(0).text_color(t.foreground).child(String(v ?? "")))));
  }
}
