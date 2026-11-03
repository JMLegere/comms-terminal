// Organizations, as the Console's OrganizationsPage (D129): one row each (Domain, People, Todo, Stage and Value:
// Revenue's, else Fundraising's). j/k move and the Organization fills the right pane; Enter opens its page; s and v
// set its Stage and Value; p adds or removes People.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run } from "comms";

/** Stage and Value for the list: Revenue's, else Fundraising's (the Console's org_stage_cells). */
export function stageCells(held) {
  const s = ["REVENUE", "FUNDRAISING", "TALENT"].map((p) => held[p]).find((x) => x?.stage);
  return s ? [s.stage, s.value != null ? money(s.value) : ""] : ["", ""];
}

/** 90k, 1.5M, 250 (pipeline.money). */
export function money(v) {
  const a = Math.abs(v);
  const fmt = (x, u) => `${(Math.round(x * 10) / 10).toString().replace(/\.0$/, "")}${u}`;
  return a >= 1e6 ? fmt(v / 1e6, "M") : a >= 1e3 ? `${Math.round(v / 1e3)}k` : String(v);
}

export default class Organizations extends View {
  init(_props, cx) {
    this.rows = [];
    this.states = {};
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "organizations") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  shown() {
    return this.rows;
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const answer = await call("organizations.list");
        this.rows = answer.organizations;
        this.states = answer.states ?? {};
        this.error = null;
        if (shared("page") === "organizations" && !shared("organization")) this.select(this.shown()[0]);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(r) {
    if (!r) return;
    setShared("organization", { id: r.id, domain: r.domain });
    run("organization.show");
    const at = this.shown().findIndex((x) => x.id === r.id);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
  }

  command(c, cx) {
    const rows = this.shown();
    const i = rows.findIndex((r) => r.id === shared("organization")?.id);
    if (c === "show") return this.load(cx);
    if (c === "next" || c === "prev") this.select(rows[Math.max(0, Math.min(rows.length - 1, i + (c === "next" ? 1 : -1)))]);
    if (c === "enter" && rows[i]) { setShared("push", { page: "organization", organization: { id: rows[i].id, domain: rows[i].domain } }); run("nav.push"); }
  }

  render() {
    const t = theme();
    const selected = shared("organization")?.id;
    const rows = this.shown();
    const row = (r) => {
      const [stage, value] = stageCells(this.states[r.id] ?? {});
      return h_flex().h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer()
        .when(r.id === selected, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
        .child(div().w(16).flex_none().text_color(t.foreground).child("\u{f01d7}"))
        .child(div().flex_1().min_w(0).truncate().font_semibold().text_color(t.strong ?? t.foreground).child(r.domain ?? ""))
        .child(div().w(44).flex_none().text_color(t.muted).child(`\u{f000f} ${r.people}`))
        .child(div().w(40).flex_none().text_color(t.blue).child(r.todo ? `\u{f0766} ${r.todo}` : ""))
        .child(div().w(96).flex_none().truncate().text_color(t.foreground).child(stage))
        .child(div().w(40).flex_none().text_color(t.foreground).child(value));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f01d7}"))
        .child(div().font_medium().text_color(t.foreground).child("organizations")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !rows.length, (el) => el.child(div().p(16).text_color(t.muted).child("No organizations yet. They form from company email domains.")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("orgs", rows.length, 26, (i) => rows[i].id, (range) => rows.slice(range.start, range.end).map(row))
          .size_full().track_scroll(this.scroll).on_item_click((key) => this.select(rows.find((r) => r.id === key)))));
  }
}
