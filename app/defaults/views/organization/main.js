// One Organization, as the Console's OrganizationPage (D131–D134): its Threads (glyph, title, Status; a click opens
// one where it lives), and beside them its Pipeline section (each Pipeline's Stage and days, Value, Now, recent
// changes) and People with their identities, then Context. s sets a Stage (in its one Pipeline, else pick one; the
// current Stage marked, the other Pipelines one step away), v a Value ("90k" works), p adds or removes People; each
// is undoable (z). On the Pipeline page the Pipeline is already chosen. Element focus: the Threads, then the links
// beside them (People, Organizations), are its elements; j/k walk them, tab jumps between the two, Enter opens one.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, open, focusMain } from "comms";

const GLYPH = {
  CREATED: ["\u{f0674}", "status_needs_first", "Needs first response"], NEW_REPLY: ["\u{f173e}", "status_needs_next", "Needs next response"],
  IN_PROGRESS: ["\u{f0349}", "status_investigating", "Investigating"], REPLY_DRAFT: ["\u{f048a}", "status_reply_draft", "Reply Draft"],
  WAITING_FOR_CUSTOMER: ["\u{f051f}", "status_waiting", "Waiting for them"], WAITING_FOR_DURATION: ["\u{f03e6}", "status_waiting", "Paused for later"],
  WAITING_INDEFINITELY: ["\u{f03e6}", "status_waiting", "Paused for later"], DONE_MANUALLY_SET: ["\u{f012c}", "status_done", "Done"],
  IGNORED: ["\u{f06d1}", "status_ignored", "Ignored"],
};
const PIPE_ICON = { REVENUE: "\u{f0116}", FUNDRAISING: "\u{f1890}", TALENT: "\u{f0849}" };
const SOURCES = ["attio", "tana", "linear"];
const grouped = (x) => String(Math.round(x)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
const offerUndo = (message, steps) => { setShared("undo", { message, steps }); toast(`${message}  z undo`); };
const KIND = { person: "Person", org: "Organization" };
const viewOf = (t) => (t.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[t.status] ?? "todo");

/** Console segment lines ([text, colour, bold, link]); person and org links open their page; a URL opens on
 * Ctrl+click (Cmd+click on macOS), or on Enter. With `nav` ({ links: [], at }), each link joins nav.links as an
 * element, and the one at nav.at is drawn focused. */
export function segLines(t, lines, key = "s", orgs = {}, nav = null) {
  const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
  const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
  const follow = (link, ev, enter = false) => {
    const push = (entry) => { setShared("push", entry); run("nav.push"); };
    if (link.startsWith("person:")) push({ page: "person", person: link.slice(7) });
    else if (link.startsWith("org:")) push({ page: "organization", organization: { domain: link.slice(4), id: orgs[link.slice(4)] ?? null } });
    else if (enter || ev?.modifiers?.control || ev?.modifiers?.platform) open(link);
  };
  return (lines ?? []).map((segs, r) => (segs.length ? h_flex().w_full().gap(0).flex_wrap()
    .children(segs.map(([text, hex, bold, link], i) => {
      const n = nav && link ? nav.links.push({ text, link, go: () => follow(link, null, true) }) - 1 : -1;
      return div().min_w(0).text_color(link ? t.blue : color(hex)).when(Boolean(bold), (d) => d.font_semibold())
        .when(Boolean(link), (d) => d.id(`${key}-${r}-${i}`).cursor_pointer().on_click((ev) => follow(link, ev)))
        .when(n >= 0 && n === nav.at, (d) => d.bg(t.selected).border_l(3).border_color(t.accent).pl(4)).child(text);
    }))
    : div().h(10)));
}

/** s's options for one Pipeline: its Stages, the current one marked, then the other Pipelines it can be in (the
 * Console's set_org_stage; an Organization is in Revenue only, a Person in Fundraising and Talent, ADR 0016). */
export function stageOptions(st, p) {
  const now = st.current?.[p];
  return [...st.all[p].map((s) => ({ value: s, label: `${s === now ? "▸ " : "  "}${s}${s === now ? "  (current)" : ""}` })),
    ...Object.keys(st.all).filter((q) => q !== p).map((q) => ({ value: `pipeline:${q}`, label: `  ${st.names[q]}…` }))];
}

export default class Organization extends View {
  init(_props, cx) {
    this.page = null;
    this.context = [];
    this.at = 0;
    this.links = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("organization")) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    const org = shared("organization");
    if (!org?.domain) { this.page = null; return cx.notify(); }
    cx.spawn(async (cx) => {
      try {
        const page = await call("organization.page", { domain: org.domain });
        if (shared("organization")?.domain !== org.domain) return;
        const fresh = this.page?.domain !== page.domain;
        this.page = page;
        this.error = null;
        setShared("crumb", page.org?.name ?? org.domain); // the Breadcrumb's name for this page
        if (fresh) {
          this.at = 0;
          this.links = [];
          this.context = [];
          for (const source of SOURCES) call("context.section", { source, emails: page.emails ?? [], domains: [page.domain] })
            .then((s) => { if (s && shared("organization")?.domain === org.domain) { this.context = [...this.context, s]; cx.notify(); } }).catch(() => {});
        }
        this.target(); // a pushed page loads after its focus_entry
      } catch (e) {
        this.page = null;
        this.error = String(e);
      }
      cx.notify();
    });
  }

  threads() { return (this.page?.threads ?? []).map((x) => x.f ?? x); }

  /** A Thread opens where it lives, as a click on its row. */
  openThread(th) { setShared("thread", th.id); run(`nav.${viewOf(th)}`); run("thread.show"); }

  /** The bar's line for the focused element (element focus): a Thread, or a link beside them. */
  target() {
    if (!this.page || !["organizations", "organization"].includes(shared("page")) || shared("focus") !== "detail") return;
    const ths = this.threads(), th = ths[this.at], l = this.links?.[this.at - ths.length];
    const pipelines = (shared("modules") ?? ["pipelines"]).includes("pipelines"); // off: no Stage keys (ADR 0017)
    const acts = [...(pipelines ? [["s", "stage"], ["v", "value"]] : []), ["p", "people"]];
    if (th) setShared("target", { col: "detail", label: `Thread · ${th.title ?? ""}`, keys: [["enter", "open"], ...acts] });
    else if (l) setShared("target", { col: "detail", label: `${KIND[l.link.split(":")[0]] ?? "Link"} · ${l.text.trim()}`,
      keys: [["enter", "open"], ...acts] });
  }

  pick(title, options, reply, prompt) {
    setShared("picker", { title, options, reply: `organization.${reply}`, prompt });
    run("overlay.open");
  }

  /** The Pipeline to act in: the Pipeline page's, else the one it holds a Stage in, else Jeremy picks (choose_pipeline). */
  choose(what, then) {
    const st = this.page.stages, o = this.page.org;
    if (shared("page") === "pipeline") return then((shared("pipeline") ?? "revenue").toUpperCase());
    if (st.held.length === 1) return then(st.held[0]);
    this.then = then;
    this.pick(`${what} · ${o.name}`, Object.keys(st.all).map((p) => ({ value: p, label: `${PIPE_ICON[p] ?? ""} ${st.names[p]}` })), "pipeline-picked");
  }

  stagePick(p) {
    this.pipeline = p;
    this.pick(`Stage · ${this.page.org.name} (${this.page.stages.names[p]})`, stageOptions(this.page.stages, p), "stage-picked");
  }

  async write(method, params, message, undo) {
    try {
      const r = await call(`command.${method}`, params);
      if (undo && r != null) offerUndo(message, undo(r));
      else if (message && r !== null) toast(message);
      return r;
    } catch (e) {
      toast(`Not changed: ${String(e).replace(/^(Error|Refused): /, "")}`, "error");
      return undefined;
    }
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const pg = this.page, o = pg?.org;
    if (name === "show") return this.load(cx);
    if (!o) return;
    const ths = this.threads(), last = ths.length + (this.links?.length ?? 0) - 1;
    switch (name) {
      case "focus_entry": this.at = Math.max(0, Math.min(last, this.at)); this.target(); return cx.notify();
      case "next": case "prev": // the element cursor: the Threads, then the links beside them
        this.at = Math.max(0, Math.min(last, this.at + (name === "next" ? 1 : -1)));
        this.target();
        return cx.notify();
      case "tab": // the other section: the first link, or back to the first Thread
        this.at = this.at < ths.length && (this.links?.length ?? 0) ? ths.length : 0;
        this.target();
        return cx.notify();
      case "enter": return ths[this.at] ? this.openThread(ths[this.at]) : this.links?.[this.at - ths.length]?.go();
      case "stage": return this.choose("Stage", (p) => this.stagePick(p));
      case "value": return this.choose("Value", (p) => {
        this.pipeline = p;
        this.pick(`${pg.stages.names[p]} Value · ${o.name}`, [], "value-typed", p === "REVENUE" ? "CAD, next 12 months, e.g. 90k" : "CAD cheque, e.g. 250k");
      });
      case "pipeline-picked": return this.then?.(answer.value);
      case "stage-picked":
        if (answer.value.startsWith("pipeline:")) return this.stagePick(answer.value.slice(9));
        return cx.spawn(async () => this.write("pipeline.set_stage", { org_id: o.id, pipeline: this.pipeline, stage: answer.value },
          `${o.name} → ${answer.value}.`, (entry) => [["pipeline.undo", { entry_id: entry }]]));
      case "value-typed":
        if (answer.text == null) return;
        return cx.spawn(async () => {
          const amount = await call("amount.parse", { text: answer.text }).catch(() => null);
          await this.write("pipeline.set_value", { org_id: o.id, pipeline: this.pipeline, value: answer.text },
            `${o.name}: ${pg.stages.names[this.pipeline]} Value CAD ${grouped(amount ?? 0)}.`, (entry) => [["pipeline.undo", { entry_id: entry }]]);
        });
      case "people":
        return this.pick(`People · ${pg.domain}`, [{ value: "add", label: "+ add a Person…" },
          ...(pg.people ?? []).map((p) => ({ value: p.id, label: `− remove ${p.name}` }))], "people-picked");
      case "people-picked": {
        if (answer.value === "add") return this.pick(`Add a Person to ${pg.domain}`, [], "people-typed", "email address, phone number, or name");
        const p = (pg.people ?? []).find((x) => x.id === answer.value);
        return p && this.member(p.id, p.name, false, cx);
      }
      case "people-typed":
        if (!answer.text) return;
        return cx.spawn(async (cx) => {
          const matches = await call("people.find", { text: answer.text }).catch(() => []);
          if (!matches.length) return toast(`No Person matches ${answer.text}. Add the address to a Person first.`, "error");
          if (matches.length === 1) return this.member(matches[0][0], matches[0][1], true, cx);
          this.matches = matches;
          this.pick(`Add to ${pg.domain}`, matches.map(([id, n]) => ({ value: id, label: n })), "people-chosen");
        });
      case "people-chosen": {
        const m = (this.matches ?? []).find(([id]) => id === answer.value);
        return m && this.member(m[0], m[1], true, cx);
      }
    }
  }

  /** Membership: add or remove a Person; z puts back what was before (the Console's set_member). */
  member(personId, name, member, cx) {
    const domain = this.page.domain;
    cx.spawn(async () => {
      try {
        const before = await call("command.inbox.set_membership", { person_id: personId, domain, member });
        offerUndo(member ? `Added ${name} to ${domain}.` : `Removed ${name} from ${domain}.`,
          [["inbox.set_membership", { person_id: personId, domain, member: before ?? null }]]);
      } catch (e) {
        toast(/FreeMail/.test(String(e)) ? `Not added: ${domain} is a free-mail or incomplete domain, never an Organization.` : String(e), "error");
      }
    });
  }

  render() {
    const t = theme();
    const pg = this.page;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!pg) return pane.child(div().p(24).text_color(t.muted).child(this.error ?? "No Organization selected"));
    const threads = this.threads();
    const focusHere = shared("focus") === "detail";
    const nav = { links: (this.links = []), at: focusHere ? this.at - threads.length : -1 };
    return pane.child(h_flex().flex_1().min_h(0).items_start()
      .child(v_flex().id("org-threads").flex_1().min_w(0).h_full().overflow_y_scroll().px(16).py(12).gap(0)
        .child(div().pb(6).font_semibold().text_color(t.blue).child(`\u{f01d7} organization · ${pg.domain}`))
        .when(!threads.length, (e) => e.child(div().text_color(t.muted).child("No threads here.")))
        .children(threads.map((th, n) => {
          const g = GLYPH[th.status_detail] ?? ["·", "muted", ""];
          return h_flex().id(`t-${th.id}`).h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer().hover((s) => s.bg(t.hover))
            // The path (Logseq bullet threading): the focused Thread has the full bar and highlight.
            .border_l(3).border_color(focusHere && n === this.at ? t.accent : t.background)
            .when(focusHere && n === this.at, (el) => el.bg(t.selected))
            .on_click(() => this.openThread(th)) // a link: it opens the Thread where it lives
            .child(div().w(16).flex_none().text_color(t[g[1]] ?? t.muted).child(g[0]))
            .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(th.title ?? ""))
            .child(div().flex_none().text_color(t[g[1]] ?? t.muted).child(g[2]));
        })))
      .child(v_flex().id("org-side").w(320).flex_none().h_full().overflow_y_scroll().px(16).py(12).gap(2).border_l(1)
        .border_color(t.border).text_size(12.5)
        .children(segLines(t, pg.side, "o", {}, nav))
        .children(this.context.map((s) => v_flex().mt(12).p(8).gap(2).rounded(6).border(1).border_color(t.border)
          .child(div().text_color(t.muted).child(s.title)).children(segLines(t, s.lines, `c${s.title}`))))));
  }
}
