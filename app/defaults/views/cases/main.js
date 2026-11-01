// Cases: each Case with its rolled-up Status (Jeremy never sets it) and its Threads by Status. j/k move and the
// Case fills the right pane; n starts a new Case.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const COLOR = { Todo: "status_needs_next", Snoozed: "status_waiting", Done: "status_done", Ignored: "status_ignored" };

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
        this.rows = await call("cases.list");
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
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const i = this.rows.findIndex((r) => r.id === shared("case"));
    switch (name) {
      case "show": return this.load(cx);
      case "next": case "prev": {
        const row = this.rows[Math.max(0, Math.min(this.rows.length - 1, i + (name === "next" ? 1 : -1)))];
        return row && this.select(row.id);
      }
      case "new":
        setShared("picker", { title: "New case title", options: [], prompt: "What is this about?", reply: "cases.named" });
        return run("overlay.open");
      case "named":
        return cx.spawn(async () => {
          try {
            const id = await call("command.inbox.create_case", { title: answer.text });
            toast(`Case “${answer.text}” created.`);
            if (id) this.select(String(id));
          } catch (e) {
            toast(String(e), "error");
          }
        });
    }
  }

  render() {
    const t = theme();
    const selected = shared("case");
    const rows = this.rows;
    const row = (r) => {
      const active = r.id === selected;
      const counts = Object.entries(r.counts ?? {}).filter(([, n]) => n).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(" · ");
      return v_flex().h(52).px(8).justify_center().rounded(8).gap(2).cursor_pointer()
        .when(active, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
        .child(h_flex().gap(9).items_center()
          .child(div().size(7).rounded(4).flex_none().bg(t[COLOR[r.status]] ?? t.dim))
          .child(div().flex_1().min_w(0).truncate().font_medium().text_color(t.foreground).child(r.title ?? ""))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(r.status ?? "")))
        .child(div().pl(16).truncate().text_color(t.muted).child(counts || "no threads"));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/folder.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Cases"))
        .child(div().flex_1().text_color(t.dim).child(`· ${rows.length}`))
        .child(div().text_size(11).text_color(t.dim).child("n new · J/K threads")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !rows.length, (el) => el.child(div().p(16).text_color(t.dim).child("No cases. n starts one.")))
      .child(div().flex_1().min_h(0).px(6).py(6).child(
        v_virtual_list("cases", rows.length, 52, (i) => rows[i].id, (range) => rows.slice(range.start, range.end).map(row))
          .size_full().track_scroll(this.scroll).on_item_click((key) => this.select(key))));
  }
}
