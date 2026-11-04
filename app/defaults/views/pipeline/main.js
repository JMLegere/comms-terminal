// One Pipeline (shared "pipeline": revenue, fundraising or talent), as the Console's PipelinePage (D123–D128): full
// width, action first. THIS WEEK (numbered actions with reasons) beside PROGRESS (verdict, gauge, Target, Projected,
// Today, Coverage, Gap, Deadline, Customers), the CONSTRAINT in red, the ORGANIZATIONS board (a column per Stage,
// its header with count and conversion, the Constraint's marked ◀; each card's name, Value or red "no Value", days
// in Stage, roll-up, last message), and the WEEKLY REVIEW with its legend. Talent shows the board only. Fundraising
// and Talent hold People (ADR 0016): a card names the Person and their Organizations. j/k move in a column, ←/→
// between Stages; Enter opens the Organization or Person; s and v set its Stage and Value in this Pipeline;
// y copies the Pipeline math for an agent. Element focus: the selected card is the detail column's element; it names
// itself in the bar and carries the path's accent bar.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, focusMain } from "comms";

const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };

export default class Pipeline extends View {
  init(_props, cx) {
    this.b = null;
    this.col = 0;
    this.row = 0;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "pipeline") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  name() { return shared("pipeline") ?? "revenue"; }

  load(cx) {
    const name = this.name();
    cx.spawn(async (cx) => {
      try {
        const b = await call("pipeline.board", { name });
        if (this.name() !== name) return;
        const fresh = this.b?.name !== b.name;
        this.b = b;
        if (fresh) { // start on the first Stage that holds an Organization, as the Console's focus_first
          this.col = Math.max(0, b.columns.findIndex((c) => c.cards.length));
          this.row = 0;
        }
        this.error = null;
        this.select();
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  card() { return this.b?.columns?.[this.col]?.cards?.[this.row]; }

  /** The selected card's Organization or Person is the one s and v act on (in its view, off screen). */
  select() {
    const c = this.b?.columns?.[this.col];
    if (c) this.row = Math.max(0, Math.min(this.row, c.cards.length - 1));
    const card = this.card();
    if (shared("page") !== "pipeline") return;
    this.target();
    if (!card) return;
    if (card.org.person) { setShared("person", card.org.id); run("person.show"); return; }
    setShared("organization", { id: card.org.id, domain: card.org.domain });
    run("organization.show");
  }

  /** The bar's line for the focused card (element focus): its Organization, or its empty Stage, and the keys it takes. */
  target() {
    const c = this.b?.columns?.[this.col], card = this.card();
    if (!c || shared("page") !== "pipeline" || shared("focus") !== "detail") return;
    setShared("target", card
      ? { col: "detail", label: `${card.org.person ? "Person" : "Organization"} · ${card.org.name ?? card.org.domain ?? ""}`,
        keys: [["enter", "open"], ["s", "stage"], ...(this.b.board_only ? [] : [["v", "value"]]), ["←→", "Stages"]] }
      : { col: "detail", label: `Stage · ${c.stage ?? ""}`, keys: [["←→", "Stages"]] });
  }

  command(c, cx) {
    const cols = this.b?.columns ?? [];
    switch (c) {
      case "show": return this.load(cx);
      case "focus_entry": this.target(); return cx.notify();
      case "next": case "prev": this.row += c === "next" ? 1 : -1; this.row = Math.max(0, this.row); break;
      case "next_column": case "prev_column": this.col = Math.max(0, Math.min(cols.length - 1, this.col + (c === "next_column" ? 1 : -1))); break;
      case "enter": {
        const card = this.card();
        if (card) {
          setShared("push", card.org.person ? { page: "person", person: card.org.id }
            : { page: "organization", organization: { id: card.org.id, domain: card.org.domain } });
          run("nav.push");
        }
        return;
      }
      case "stage": case "value": { // s and v act in the card's own view: the Person's or the Organization's
        const card = this.card();
        if (card) run(`${card.org.person ? "person" : "organization"}.${c}`);
        return;
      }
      case "copy":
        if (!this.b) return;
        return cx.spawn(async (cx) => {
          cx.write_to_clipboard(this.b.copy);
          toast(`Copied the ${this.b.name} Pipeline for an agent · ${this.b.copy.length} characters.`);
        });
      default: return;
    }
    this.select();
    cx.notify();
  }

  render() {
    const t = theme();
    const b = this.b;
    const root = v_flex().id("pipeline").size_full().min_h(0).overflow_y_scroll().bg(t.background).text_size(13).p(8).gap(8);
    if (!b) return root.child(div().p(16).text_color(t.muted).child(this.error ?? ""));
    const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
    const segs = (lines) => (lines ?? []).map((ss) => (ss.length ? h_flex().w_full().gap(0).flex_wrap()
      .children(ss.map(([text, hex, bold]) => div().min_w(0).text_color(color(hex)).when(Boolean(bold), (d) => d.font_semibold()).child(text)))
      : div().h(10)));
    const box = (title, body, red) => v_flex().p(10).gap(2).rounded(6).border(1).border_color(red ? t.red : t.border)
      .child(div().pb(4).font_semibold().text_color(red ? t.red : t.foreground).child(title)).children(body);
    const focusHere = shared("focus") === "detail";
    const board = h_flex().id("board").gap(6).items_start().overflow_x_scroll().children(b.columns.map((c, i) => v_flex().w(170).flex_none().gap(4)
      .child(v_flex().pb(4).border_b(1).border_color(t.border).children(segs(c.head)))
      .children(c.cards.length ? c.cards.map((k, j) => v_flex().id(`k-${i}-${j}`).p(6).rounded(4).cursor_pointer()
        .hover((s) => s.bg(t.hover))
        // The path (Logseq bullet threading): the focused card has a full bar and highlight.
        .border_l(3).border_color(i === this.col && j === this.row ? t.accent : t.background)
        .when(i === this.col && j === this.row && focusHere, (e) => e.bg(t.selected))
        .when(i === this.col && j === this.row && !focusHere, (e) => e.border_l(1))
        .on_click((_e, cx) => { // a click focuses the card and takes the keys from any text field
          this.col = i; this.row = j;
          setShared("focus", "detail");
          focusMain();
          this.select();
          cx.notify();
        })
        .children(segs(k.lines))) : [div().px(6).text_color(t.dim).child("·")])));
    const title = `${(b.noun ?? "Organizations").toUpperCase()}  ·  each Stage${b.board_only ? "" : ", how many move on"}  ·  enter opens · s Stage${b.board_only ? "" : " · v Value"} · ←/→`;
    const cell = (ss, w) => div().w(w).flex_none().children(segs([ss]));
    const widths = [130, 120, 150, 90, 110, 110, 100, 120, 80];
    return root
      .when(!b.board_only, (el) => el
        .child(h_flex().gap(8).items_start()
          .child(div().flex_1().min_w(0).child(box("THIS WEEK", segs(b.week))))
          .child(div().flex_1().min_w(0).child(box("PROGRESS", segs(b.progress)))))
        .child(box("CONSTRAINT", segs(b.constraint), true)))
      .child(box(title, [board]))
      .when(!b.board_only, (el) => el.child(box("WEEKLY REVIEW", [
        h_flex().gap(0).children(b.weekly_head.map((h, i) => div().w(widths[i]).flex_none().font_semibold().text_color(t.muted).child(h))),
        ...b.weekly.map((r) => h_flex().gap(0).children(r.map((ss, i) => cell(ss, widths[i])))),
        div().pt(6).text_color(t.muted).child(b.legend)])));
  }
}
