// Cases, as the Console's CasesPage (D98–D100): one Case Status view (the rail's; Todo oldest first, the others most
// recently changed), one row each (icon, title, "2 todo · 1 snoozed · 4 done"). j/k move and the Case fills the
// right pane; Enter opens its page on the stack; n starts a new Case.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, focusMain } from "comms";

const EMPTY = { // the Console's CasesPage.EMPTIES
  Todo: "No case needs you. Press n to start one, or l on a thread.", Snoozed: "No case is waiting on anyone.",
  Done: "No case is done yet.", Ignored: "No ignored cases.",
};
export const caseCounts = (counts) => Object.entries(counts ?? {}).filter(([, n]) => n).map(([v, n]) => `${n} ${v.toLowerCase()}`).join(" · ") || "no threads yet";

export default class Cases extends View {
  init(_props, cx) {
    this.rows = [];
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "cases") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.view = shared("case_view") ?? "Todo"; // the rail's Case Status view
        this.rows = await call("cases.view", { view: this.view });
        this.error = null;
        if (shared("page") === "cases" && !this.rows.some((r) => r.id === shared("case"))) this.select(this.rows[0]?.id ?? null);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(id) {
    setShared("case", id);
    run("case.show");
    const at = this.rows.findIndex((r) => r.id === id);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
    this.target();
  }

  /** The bar's line for the focused row (element focus): the Case and the keys it takes. */
  target() {
    if (shared("page") !== "cases" || shared("focus") === "detail") return;
    const r = this.rows.find((x) => x.id === shared("case"));
    if (r) setShared("target", { col: "list", label: `Case · ${r.title ?? ""}`, keys: [["enter", "open"], ["n", "new"]] });
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const i = this.rows.findIndex((r) => r.id === shared("case"));
    switch (name) {
      case "show": return this.load(cx);
      case "focus_entry": return this.target();
      case "next": case "prev": {
        const row = this.rows[Math.max(0, Math.min(this.rows.length - 1, i + (name === "next" ? 1 : -1)))];
        return row && this.select(row.id);
      }
      case "enter": { // the Case's page, on the stack
        const id = shared("case");
        if (!id) return;
        setShared("push", { page: "case", case: id });
        return run("nav.push");
      }
      case "new":
        setShared("picker", { title: "New case title", options: [], prompt: "What is the goal?", reply: "cases.named" });
        return run("overlay.open");
      case "named":
        return cx.spawn(async () => {
          try {
            const id = await call("command.inbox.create_case", { title: answer.text });
            toast(`Case “${answer.text}” started. Press l on a thread to add it.`);
            setShared("case", id ? String(id) : null);
            run("nav.cases:Todo"); // a Case with no Threads is Todo
          } catch (e) {
            toast(String(e), "error");
          }
        });
    }
  }

  render() {
    const t = theme();
    const selected = shared("case");
    const focusHere = (shared("focus") ?? "list") === "list";
    const rows = this.rows;
    const view = this.view ?? "Todo";
    // The path (Logseq bullet threading): full bar and highlight on the focused row, a thin bar while the focus is
    // further in (the Case).
    const row = (r) => h_flex().h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer().hover((s) => s.bg(t.hover))
      .border_l(3).border_color(r.id === selected ? t.accent : t.background)
      .when(r.id === selected && focusHere, (el) => el.bg(t.selected))
      .when(r.id === selected && !focusHere, (el) => el.border_l(1))
      .child(div().w(16).flex_none().text_color(t.foreground).child("\u{f0814}"))
      .child(div().flex_none().max_w(260).truncate().font_semibold().text_color(t.strong ?? t.foreground).child(r.title ?? ""))
      .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(caseCounts(r.counts)));
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f0814}"))
        .child(div().font_medium().text_color(t.foreground).child(`cases · ${view.toLowerCase()}`)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !rows.length, (el) => el.child(div().p(16).text_color(t.muted).child(EMPTY[view] ?? "")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("cases", rows.length, 26, (i) => rows[i].id, (range) => rows.slice(range.start, range.end).map(row))
          .size_full().track_scroll(this.scroll).on_item_click((key, cx) => {
            setShared("focus", "list"); // a click focuses the row and takes the keys from any text field
            focusMain();
            this.select(key);
            cx.notify();
          })));
  }
}
