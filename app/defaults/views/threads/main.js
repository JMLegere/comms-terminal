// The current Status view's Threads, grouped by Status detail as herdr-app groups agents by state.
// j/k (or ↓/↑) move the selection; the Thread view follows it.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, mark } from "comms";

// Status details (GLOSSARY): label, colour token. Reply Draft is shown, never stored: a Todo Thread with a draft.
const DETAIL = {
  CREATED: ["Needs first response", "status_needs_first"],
  NEW_REPLY: ["Needs next response", "status_needs_next"],
  IN_PROGRESS: ["Investigating", "status_investigating"],
  REPLY_DRAFT: ["Reply Draft", "status_reply_draft"],
  WAITING_FOR_CUSTOMER: ["Waiting for them", "status_waiting"],
  WAITING_FOR_DURATION: ["Paused for later", "status_waiting"],
  DONE_MANUALLY_SET: ["Done", "status_done"],
  DONE_AUTOMATICALLY_SET: ["Done", "status_done"],
  IGNORED: ["Ignored", "status_ignored"],
};
const ORDER = ["CREATED", "NEW_REPLY", "IN_PROGRESS", "REPLY_DRAFT", "WAITING_FOR_CUSTOMER", "WAITING_FOR_DURATION"];
const ICON = { Todo: "square", Snoozed: "clock", Done: "circle-check", Ignored: "archive" };

const detailOf = (r) => (r.status === "TODO" && r.drafted ? "REPLY_DRAFT" : r.detail);

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const mins = Math.round((now - d) / 60000);
  if (mins < 60) return `${Math.max(mins, 1)}m`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)}h`;
  if (mins < 7 * 24 * 60) return `${Math.round(mins / 1440)}d`;
  return iso.slice(5, 10);
}

export default class Threads extends View {
  init(_props, cx) {
    this.rows = [];
    this.folded = new Set();
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    const view = shared("view") ?? "Todo";
    cx.spawn(async (cx) => {
      try {
        const rows = await call("threads.queue", { view });
        const rank = (r) => { const i = ORDER.indexOf(detailOf(r)); return i < 0 ? ORDER.length : i; };
        this.rows = rows.map((r, i) => [r, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([r]) => r);
        this.error = null;
      } catch (e) {
        this.error = String(e);
      }
      if (!this.rows.some((r) => r.id === shared("thread"))) this.select(this.rows[0]?.id ?? null);
      cx.notify();
    });
  }

  select(id) {
    setShared("thread", id);
    run("thread.show");
    const at = (this.items ?? []).findIndex((it) => it.key === `row-${id}`);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
  }

  command(c, cx) {
    if (c === "reload") return this.load(cx);
    const i = this.rows.findIndex((r) => r.id === shared("thread"));
    const n = c === "next" ? i + 1 : c === "prev" ? i - 1 : i;
    const row = this.rows[Math.max(0, Math.min(this.rows.length - 1, n))];
    if (row) this.select(row.id);
  }

  // Virtualized (v_virtual_list): only the rows on screen are built, so a move costs the same in a view of
  // 3 Threads or 459. Rows take no handlers of their own; the list's on_item_click names the row by key.
  render() {
    const t = theme();
    const selected = shared("thread");
    const view = shared("view") ?? "Todo";
    const groups = [];
    for (const r of this.rows) {
      const d = detailOf(r);
      if (groups.at(-1)?.id === d) groups.at(-1).rows.push(r);
      else groups.push({ id: d, rows: [r] });
    }
    this.items = groups.flatMap((g) => [
      { key: `g-${g.id}`, group: g },
      ...(this.folded.has(g.id) ? [] : g.rows.map((r) => ({ key: `row-${r.id}`, row: r }))),
    ]);
    const header = (g) => {
      const [label, token] = DETAIL[g.id] ?? [g.id, "dim"];
      return h_flex().id(`g-${g.id}`).px(8).pt(8).h(30).gap(6).items_center().text_size(11).font_medium().cursor_pointer()
        .child(div().text_color(t[token] ?? t.dim).child(label))
        .child(div().text_color(t.dim).child(String(g.rows.length)))
        .child(div().flex_1())
        .when(this.folded.has(g.id), (el) => el.child(svg("icons/chevron-right.svg").size(11).text_color(t.dim)));
    };
    const row = (r) => {
      const active = r.id === selected;
      const token = (DETAIL[detailOf(r)] ?? [null, "dim"])[1];
      return v_flex().id(`row-${r.id}`).h(52).px(8).justify_center().rounded(8).gap(2).cursor_pointer()
        .when(active, (el) => el.bg(t.selected))
        .hover((s) => s.bg(t.hover))
        .child(h_flex().gap(9).items_center()
          .child(div().size(7).rounded(4).flex_none().bg(t[token] ?? t.dim))
          .child(div().flex_1().min_w(0).truncate().font_medium().text_color(active ? t.strong ?? t.foreground : t.foreground).child(r.who ?? ""))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(when(r.last_at))))
        .child(div().pl(16).truncate().text_color(t.muted).child(r.title ?? ""));
    };
    const items = this.items;
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg(`icons/${ICON[view] ?? "square"}.svg`).size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child(view))
        .child(div().text_color(t.dim).child(`· ${this.rows.length} Thread${this.rows.length === 1 ? "" : "s"}`)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && this.rows.length === 0, (el) => el.child(div().p(16).text_color(t.dim).child(`Nothing in ${view}.`)))
      .child(div().flex_1().min_h(0).px(6).pb(8).child(
        v_virtual_list("rows", items.length, items.map((it) => (it.group ? 30 : 52)), (i) => items[i].key,
          (range) => items.slice(range.start, range.end).map((it) => (it.group ? header(it.group) : row(it.row))))
          .size_full()
          .track_scroll(this.scroll)
          .on_item_click((key, cx) => {
            if (key.startsWith("g-")) {
              const g = key.slice(2);
              this.folded.has(g) ? this.folded.delete(g) : this.folded.add(g);
              cx.notify();
            } else this.select(key.slice(4));
          })));
  }
}
