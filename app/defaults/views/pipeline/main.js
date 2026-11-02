// One Pipeline (shared "pipeline": revenue, fundraising or talent): the verdict on its Target and its Bowtie
// Stages (Revenue and Fundraising), then its Organizations by Stage. j/k move and the Organization fills the
// right pane, where s and v set its Stage and Value in this Pipeline; y copies the board for an agent.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const grouped = (x) => String(Math.round(x)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const cad = (x) => (x == null ? "—" : `CAD ${grouped(x)}`);

export default class Pipeline extends View {
  init(_props, cx) {
    this.page = null;
    this.orgs = [];
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "pipeline") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  name() {
    return shared("pipeline") ?? "revenue";
  }

  load(cx) {
    const name = this.name();
    cx.spawn(async (cx) => {
      try {
        const [page, list, stages] = await Promise.all([call("pipeline.page", { name }), call("organizations.list"),
          this.stages ? this.stages : call("pipeline.stages")]);
        if (this.name() !== name) return;
        this.stages = stages;
        const code = name.toUpperCase(), order = stages[code].stages;
        this.page = page;
        this.orgs = list.organizations.filter((o) => list.states?.[o.id]?.[code])
          .map((o) => ({ ...o, ...list.states[o.id][code] }))
          .sort((a, b) => order.indexOf(a.stage) - order.indexOf(b.stage) || (b.value ?? 0) - (a.value ?? 0));
        this.error = null;
        if (shared("page") === "pipeline" && !this.orgs.some((o) => o.id === shared("organization")?.id)) this.select(this.orgs[0]);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(o) {
    if (!o) return;
    setShared("organization", { id: o.id, domain: o.domain });
    run("organization.show");
    const at = this.items?.findIndex((it) => it.key === o.id) ?? -1;
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
  }

  command(c, cx) {
    const i = this.orgs.findIndex((o) => o.id === shared("organization")?.id);
    if (c === "show") return this.load(cx);
    if (c === "next" || c === "prev") return this.select(this.orgs[Math.max(0, Math.min(this.orgs.length - 1, i + (c === "next" ? 1 : -1)))]);
    if (c === "copy" && this.page) {
      return cx.spawn(async (cx) => {
        cx.write_to_clipboard(this.page.board);
        toast("Copied the Pipeline board for an agent.");
      });
    }
  }

  render() {
    const t = theme();
    const p = this.page;
    const code = this.name().toUpperCase();
    const title = this.stages?.[code]?.name ?? this.name();
    const m = p?.math, s = m?.summary;
    const selected = shared("organization")?.id;
    const items = [];
    let stage = null;
    for (const o of this.orgs) {
      if (o.stage !== stage) items.push({ key: `h-${(stage = o.stage)}`, header: stage, count: this.orgs.filter((x) => x.stage === stage).length });
      items.push({ key: o.id, org: o });
    }
    this.items = items;
    const header = (it) => h_flex().h(30).px(8).pt(8).gap(6).items_center().text_size(11).font_medium()
      .child(div().text_color(t.muted).child(it.header)).child(div().text_color(t.dim).child(String(it.count)));
    const row = (o) => h_flex().h(34).px(8).gap(9).rounded(8).items_center().cursor_pointer()
      .when(o.id === selected, (el) => el.bg(t.selected)).hover((st) => st.bg(t.hover))
      .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(o.domain ?? ""))
      .child(div().flex_none().text_size(11.5).text_color(t.muted).child(o.value != null ? cad(o.value) : ""));
    const verdict = p?.verdict;
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/trending-up.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child(title))
        .child(div().flex_1().text_color(t.dim).child(`· ${this.orgs.length} organizations`))
        .child(div().text_size(11).text_color(t.dim).child("y copy board")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(Boolean(s), (el) => el.child(v_flex().px(14).py(10).gap(4).border_b(1).border_color(t.border).text_size(12)
        .child(h_flex().gap(8)
          .child(div().font_medium().text_color(verdict?.[0] === "On track" ? t.status_done : verdict?.[0] === "Off track" ? t.danger : t.foreground)
            .child(verdict?.[0] ?? "No Target"))
          .child(div().flex_1().truncate().text_color(t.dim).child(verdict?.[1] ?? "set one with `comms-terminal pipeline target`")))
        .child(div().text_color(t.muted).child([s.target ? `Target ${cad(s.target.amount)} by ${s.target.due}` : null,
          `paying ${cad(s.paying)} (${s.paying_count})`, `weighted ${cad(s.weighted)}`, s.gap != null ? `gap ${cad(s.gap)}` : null]
          .filter(Boolean).join(" · ")))
        .child(h_flex().flex_wrap().gap(10).text_size(11.5).text_color(t.dim).children((m.stages ?? []).map((st) =>
          div().text_color(st.constraint ? t.danger : t.dim).child(`${st.stage} ${pct(st.rate)}${st.constraint ? " · Constraint" : ""}`))))))
      .when(!this.error && p && !this.orgs.length, (el) => el.child(div().p(16).text_color(t.dim).child(`No Organizations in ${title} yet.`)))
      .child(div().flex_1().min_h(0).px(6).pb(8).child(
        v_virtual_list("pipeline", items.length, items.map((it) => (it.header ? 30 : 34)), (i) => items[i].key,
          (range) => items.slice(range.start, range.end).map((it) => (it.header ? header(it) : row(it.org))))
          .size_full().track_scroll(this.scroll)
          .on_item_click((key) => this.select(this.orgs.find((o) => o.id === key)))));
  }
}
