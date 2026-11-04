// One Person, as the Console's PersonPage (D139): name, Identities (J/K pick one; b blocks or unblocks it, x removes
// it, a adds one), the Sequence section, Organizations (o), Threads and Cases as links, and Context beside them.
// s enrolls in a Sequence (D141), r resumes and f finishes an Enrollment (D142), 1/2 answer an If that asks Jeremy;
// n renames; p sets their Fundraising or Talent Stage and v their Fundraising Value (ADR 0016), as the Console's. Element focus: the Identities, then the links below them, are the Person's elements; j/k walk them,
// tab jumps between the two, Enter opens a link.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, open, focusMain } from "comms";

const KIND_ICON = { email: "\u{f01f0}", slack_user: "\u{f04b1}", phone: "\u{f011c}" };
const SOURCES = ["attio", "tana", "linear"];
const offerUndo = (message, steps) => { setShared("undo", { message, steps }); toast(`${message}  z undo`); };
const KIND = { person: "Person", org: "Organization", thread: "Thread" };

/** Console segment lines ([text, colour, bold, link]); person, org and thread links open where they live; a URL opens
 * on Ctrl+click (Cmd+click on macOS), or on Enter. With `nav` ({ links: [], at }), each link joins nav.links as an
 * element, and the one at nav.at is drawn focused. */
export function segLines(t, lines, key = "s", orgs = {}, nav = null) {
  const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
  const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
  const follow = (link, ev, enter = false) => {
    const push = (entry) => { setShared("push", entry); run("nav.push"); };
    if (link.startsWith("person:")) push({ page: "person", person: link.slice(7) });
    else if (link.startsWith("org:")) push({ page: "organization", organization: { domain: link.slice(4), id: orgs[link.slice(4)] ?? null } });
    else if (link.startsWith("thread:")) {
      const id = link.slice(7);
      call("thread.view", { thread_id: id }).then((a) => {
        const v = a.view.thread.f ?? a.view.thread;
        setShared("thread", id);
        run(`nav.${v.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[v.status] ?? "todo"}`);
        run("thread.show");
      }).catch((e) => toast(String(e), "error"));
    } else if (enter || ev?.modifiers?.control || ev?.modifiers?.platform) open(link);
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

export default class Person extends View {
  init(_props, cx) {
    this.page = null;
    this.at = 0;
    this.enroll = {};
    this.links = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("person")) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    const id = shared("person");
    if (!id) {
      this.page = null;
      return cx.notify();
    }
    cx.spawn(async (cx) => {
      try {
        const page = await call("person.page", { person_id: id });
        if (shared("person") !== id) return;
        if (this.page?.person?.id !== id) {
          this.at = 0;
          this.links = [];
          this.context = [];
          for (const source of SOURCES) call("context.section", { source, emails: page.emails ?? [], domains: page.domains ?? [] })
            .then((s) => { if (s && shared("person") === id) { this.context = [...this.context, s]; cx.notify(); } }).catch(() => {});
        }
        this.page = page;
        this.error = null;
        setShared("crumb", page.person?.name ?? null); // the Breadcrumb's name for this page
        this.target(); // a pushed page loads after its focus_entry
      } catch (e) {
        this.page = null;
        this.error = String(e);
      }
      cx.notify();
    });
  }

  /** The bar's line for the focused element (element focus): an Identity and what it takes, or a link. */
  target() {
    const p = this.page?.person;
    if (!p || !["people", "person"].includes(shared("page")) || shared("focus") !== "detail") return;
    const ids = p.identities ?? [], i = ids[this.at], l = this.links?.[this.at - ids.length];
    if (i) setShared("target", { col: "detail", label: `Identity · ${i.value}`, keys: [["b", i.blocked ? "unblock" : "block"],
      ["x", "remove"], ["a", "add"], ["s", "enroll"], ["p", "stage"], ["o", "organizations"], ["n", "rename"]] });
    else if (l) setShared("target", { col: "detail", label: `${KIND[l.link.split(":")[0]] ?? "Link"} · ${l.text.trim()}`,
      keys: [["enter", "open"], ["s", "enroll"], ["n", "rename"]] });
  }

  pick(title, options, reply, prompt, value, body) {
    setShared("picker", { title, options, reply: `person.${reply}`, prompt, value, body });
    run("overlay.open");
  }

  /** The Pipeline to act in: the Pipeline page's, else the one they hold a Stage in, else Jeremy picks. */
  choose(what, then) {
    const st = this.page.stages, p = this.page.person;
    if (shared("page") === "pipeline") return then((shared("pipeline") ?? "fundraising").toUpperCase());
    if (st.held.length === 1) return then(st.held[0]);
    this.then = then;
    this.pick(`${what} · ${p.name}`, Object.keys(st.all).map((q) => ({ value: q, label: st.names[q] })), "pipeline-picked");
  }

  /** p's options: the Pipeline's Stages, the current one marked, then the other Pipeline (the Console's set_org_stage). */
  stagePick(q) {
    const st = this.page.stages, now = st.current?.[q];
    this.pipeline = q;
    this.pick(`Stage · ${this.page.person.name} (${st.names[q]})`, [
      ...st.all[q].map((s) => ({ value: s, label: `${s === now ? "▸ " : "  "}${s}${s === now ? "  (current)" : ""}` })),
      ...Object.keys(st.all).filter((x) => x !== q).map((x) => ({ value: `pipeline:${x}`, label: `  ${st.names[x]}…` }))], "stage-picked");
  }

  /** A Pipeline write that z undoes, as the Organization view's. */
  async undoable(method, params, message) {
    try {
      const entry = await call(`command.${method}`, params);
      if (entry != null) offerUndo(message, [["pipeline.undo", { entry_id: entry }]]);
    } catch (e) {
      toast(`Not changed: ${String(e).replace(/^(Error|Refused): /, "")}`, "error");
    }
  }

  async write(method, params, message) {
    try {
      const r = await call(`command.${method}`, params);
      if (message) toast(message);
      return r ?? null;
    } catch (e) {
      toast(String(e).replace(/^Error: /, ""), "error");
      return undefined;
    }
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const p = this.page?.person;
    if (name === "show") return this.load(cx);
    if (!p) return;
    const ids = p.identities ?? [];
    const identity = ids[this.at] ?? null; // null while a link has the focus
    const live = (this.page.enrollments ?? []).filter((e) => e.state !== "FINISHED");
    const last = ids.length + (this.links?.length ?? 0) - 1;
    switch (name) {
      case "focus_entry": this.at = Math.max(0, Math.min(last, this.at)); this.target(); return cx.notify();
      case "next": case "prev": // the element cursor: the Identities, then the links
        this.at = Math.max(0, Math.min(last, this.at + (name === "next" ? 1 : -1)));
        this.target();
        return cx.notify();
      case "tab": // the other section: the first link, or back to the first Identity
        this.at = this.at < ids.length && (this.links?.length ?? 0) ? ids.length : 0;
        this.target();
        return cx.notify();
      case "enter": return this.links?.[this.at - ids.length]?.go();
      case "stage": return this.choose("Stage", (q) => this.stagePick(q));
      case "value": // a Person's only Value is Fundraising's
        this.pipeline = "FUNDRAISING";
        return this.pick(`Fundraising Value · ${p.name}`, [], "value-typed", "CAD cheque, e.g. 250k");
      case "pipeline-picked": return this.then?.(answer.value);
      case "stage-picked":
        if (answer.value.startsWith("pipeline:")) return this.stagePick(answer.value.slice(9));
        return cx.spawn(async () => this.undoable("pipeline.set_stage", { org_id: p.id, pipeline: this.pipeline, stage: answer.value },
          `${p.name} → ${answer.value}.`));
      case "value-typed":
        if (answer.text == null) return;
        return cx.spawn(async () => {
          const amount = await call("amount.parse", { text: answer.text }).catch(() => null);
          await this.undoable("pipeline.set_value", { org_id: p.id, pipeline: this.pipeline, value: answer.text },
            `${p.name}: Fundraising Value CAD ${String(Math.round(amount ?? 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.`);
        });
      case "block":
        if (!identity) return;
        return cx.spawn(async () => this.write("inbox.block", { kind: identity.kind, value: identity.value, blocked: !identity.blocked },
          identity.blocked ? `Unblocked ${identity.value}: future messages follow normal Thread rules.`
            : `Blocked messages from ${identity.value}: new Threads start Ignored.`));
      case "rename": return this.pick(`Rename ${p.name}`, [], "renamed", p.name, p.name);
      case "renamed":
        if (!answer.text || answer.text === p.name) return;
        return cx.spawn(async () => this.write("inbox.rename_person", { person_id: p.id, name: answer.text }, `Renamed ${p.name} to ${answer.text}.`));
      case "add": return this.pick("Add identity", [], "added", "email address, phone number (+1…), or Slack user ID");
      case "added": {
        const text = answer.text;
        const kind = text.includes("@") ? "email" : text.startsWith("+") ? "phone" : "slack_user";
        return cx.spawn(async () => this.write("inbox.add_identity", { person_id: p.id, kind, value: text },
          `Added ${text}: its messages now belong to ${p.name}.`));
      }
      case "remove":
        if (!identity) return;
        return cx.spawn(async () => this.write("inbox.remove_identity", { identity_id: identity.id }, `Removed ${identity.value} from ${p.name}.`));
      case "organizations":
        return this.pick(`Organizations · ${p.name}`, [{ value: "add", label: "+ add to an Organization…" },
          ...(p.organizations ?? []).map((d) => ({ value: d, label: `− remove from ${d}` }))], "org-picked");
      case "org-picked":
        if (answer.value === "add") return this.pick(`Add ${p.name} to an Organization`, [], "org-added", "company domain, such as lclexcavation.com");
        return cx.spawn(async () => this.write("inbox.set_membership", { person_id: p.id, domain: answer.value, member: false }, `Removed ${p.name} from ${answer.value}.`));
      case "org-added":
        return cx.spawn(async () => this.write("inbox.set_membership", { person_id: p.id, domain: answer.text, member: true }, `Added ${p.name} to ${answer.text}.`));
      case "enroll":
        return cx.spawn(async () => {
          const ch = await call("enroll.choices", { person_id: p.id, organization_id: "" }).catch((e) => toast(String(e), "error"));
          if (!ch) return;
          if (!ch.sequences.length) return toast("No Sequences yet: make one under Sequences (n).", "warning");
          this.enroll = { choices: ch };
          this.pick(`Enroll ${p.name}`, ch.sequences.map((q) => ({ value: q.id,
            label: q.live ? `${q.name} · already in it, ${q.live[0]}${q.live.slice(1).toLowerCase()}` : `${q.name} · ${q.steps} Steps`,
            hint: q.description ?? "" })), "enroll-sequence", null, null,
            [{ text: "Every active Sequence. One live Enrollment per Person per Sequence.", dim: true }]);
        });
      case "enroll-sequence": {
        this.enroll.sequence = answer.value;
        const orgs = this.enroll.choices.organizations.map((o) => ({ value: o.id, label: o.name ?? o.domain, hint: o.domain }));
        return this.pick(`For which Organization? · ${p.name}`, [...orgs, { value: "sole", label: `New sole proprietorship: ${p.name}` },
          ...(orgs.length ? [] : [{ value: "none", label: "None for now" }])], "enroll-org", null, null,
          [{ text: "Its Actions set this Organization's Stage. First: the one their latest message came from.", dim: true }]);
      }
      case "enroll-org":
        this.enroll.org = answer.value;
        return cx.spawn(async () => {
          const org = ["sole", "none"].includes(answer.value) ? "" : answer.value;
          const ch = await call("enroll.choices", { person_id: p.id, organization_id: org }).catch(() => null);
          const addresses = ch?.addresses ?? [];
          if (addresses.length < 2) return this.startPick();
          this.pick(`Send to which address? · ${p.name}`, addresses.map((a) => ({ value: a.id, label: a.value })), "enroll-address", null, null,
            [{ text: "Its emails go here. First: the one at its Organization, else the one added last.", dim: true }]);
        });
      case "enroll-address":
        this.enroll.identity = answer.value;
        return this.startPick();
      case "enroll-start":
        if (answer.value === "later") return this.pick("Start on", [], "enroll-typed", "2026-12-01, or a number of days from today");
        return this.enrollNow(null, cx);
      case "enroll-typed": {
        const text = answer.text.trim();
        return cx.spawn(async (cx) => {
          const start = await call("enroll.start", { text }).catch(() => null);
          if (!start) return toast(`Not enrolled: ${JSON.stringify(text)} is neither a date (2026-12-01) nor a number of days.`, "error");
          this.enrollNow(start, cx);
        });
      }
      case "resume": return this.pickEnrollment(live, ["PAUSED"], (e) => cx.spawn(async () =>
        this.write("sequences.resume", { enrollment_id: e.id }, `Resumed ${e.sequence}: each Task keeps its due date.`)));
      case "finish": return this.pickEnrollment(live, ["ACTIVE", "PAUSED", "FINISHING"], (e) => {
        this.finishing = e;
        cx.spawn(async () => {
          const ch = await call("enroll.choices", { person_id: p.id, organization_id: "" });
          this.finishingReasons = ch.finishing;
          const reasons = ch.finish_reasons.filter(([k]) => !e.reason || ["CANCELED", "UNSUBSCRIBED"].includes(k));
          this.pick(`Finish ${e.sequence}`, reasons.map(([value, label]) => ({ value, label })), "finish-why");
        });
      });
      case "which": return this.whichThen?.(live.find((e) => e.id === answer.value));
      case "finish-why": {
        const e = this.finishing;
        return cx.spawn(async () => {
          try {
            const undo = await call("command.sequences.finish", { enrollment_id: e.id, reason: answer.value });
            const ch = await call("enroll.choices", { person_id: p.id, organization_id: "" });
            const runs = (this.finishingReasons ?? []).includes(answer.value) ? " Its On completion ran." : "";
            offerUndo(`Finished ${e.sequence}: ${ch.reasons[answer.value]}.${runs} Its unsent Task Drafts are gone.`,
              [["sequences.restore_finish", { undo }]]);
          } catch (err) {
            toast(`Not finished: ${String(err).replace(/^(Error|Refused): /, "")}`, "error");
          }
        });
      }
      case "pick_first": case "pick_second": return this.answerIf(name === "pick_first" ? 1 : 0, cx);
    }
  }

  startPick() {
    const q = this.enroll.choices.sequences.find((x) => x.id === this.enroll.sequence);
    this.pick(`Start ${q?.name ?? ""}`, [{ value: "today", label: "Start today" }, { value: "later", label: "Start later…" }], "enroll-start",
      null, null, [{ text: "Start later holds the first Task until that day.", dim: true }]);
  }

  /** The Person's one live Enrollment in `states`, or the one Jeremy picks (the Console's pick_enrollment). */
  pickEnrollment(live, states, then) {
    const found = live.filter((e) => states.includes(e.state));
    const title = (s) => s[0] + s.slice(1).toLowerCase();
    if (!found.length) return toast(`No ${states.map(title).join(" or ")} Enrollment here.`, "warning");
    if (found.length === 1) return then(found[0]);
    this.whichThen = then;
    this.pick("Which Enrollment?", found.map((e) => ({ value: e.id, label: `${e.sequence} · ${title(e.state)}` })), "which");
  }

  enrollNow(start, cx) {
    const p = this.page.person, e = this.enroll;
    const q = e.choices.sequences.find((x) => x.id === e.sequence);
    cx.spawn(async () => {
      let org = e.org === "none" ? null : e.org;
      if (org === "sole") org = await this.write("inbox.create_sole_proprietorship", { person_id: p.id });
      if (org === undefined) return;
      try {
        const eid = await call("command.sequences.enroll", { person_id: p.id, sequence_id: e.sequence, start: start ? { $date: start } : null,
          organization_id: org, identity_id: e.identity ?? null });
        const due = await call("enrollment.first_due", { enrollment_id: String(eid) }).catch(() => "soon");
        offerUndo(`Enrolled ${p.name} in ${q?.name ?? "the Sequence"}: the first Task is due ${due}, under Tasks.`,
          [["sequences.unenroll", { enrollment_id: eid }]]);
      } catch (err) {
        toast(`Not enrolled: ${String(err).replace(/^(Error|Refused): /, "")}`, "error");
      }
      this.enroll = {};
    });
  }

  /** 1 or 2: answer this Person's If that asks Jeremy (FR-SEQ-31). */
  answerIf(choice, cx) {
    const p = this.page.person;
    cx.spawn(async () => {
      const t = (await call("tasks.list")).find((t) => t.person_id === p.id && t.kind === "if" && t.condition === "jeremy_picks");
      if (!t) return toast("Nothing is waiting for your pick here.", "warning");
      await this.write("sequences.pick", { task_id: t.id, choice }, `${t.person}: ${t.subject} → ${choice ? t.true_label : t.false_label}.`);
    });
  }

  render() {
    const t = theme();
    const pg = this.page, p = pg?.person;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!p) return pane.child(div().p(24).text_color(t.muted).child(this.error ?? "No Person selected"));
    const ids = p.identities ?? [];
    const focusHere = shared("focus") === "detail";
    const nav = { links: (this.links = []), at: focusHere ? this.at - ids.length : -1 };
    return pane.child(h_flex().flex_1().min_h(0).items_start()
      .child(v_flex().id("person").flex_1().min_w(0).h_full().overflow_y_scroll().px(20).py(14).gap(2)
        .children(segLines(t, pg.head, "h"))
        .child(v_flex().gap(0).children(ids.map((i, n) => h_flex().id(`i-${i.id}`).h(26).px(8).gap(10).rounded(4).items_center().cursor_pointer()
          // The path (Logseq bullet threading): the focused Identity has the full bar and highlight; the picked one
          // keeps a thin bar while the focus is elsewhere (b and x still act on it).
          .border_l(3).border_color(n === this.at ? t.accent : t.background)
          .when(n === this.at && focusHere, (el) => el.bg(t.selected))
          .when(n === this.at && !focusHere, (el) => el.border_l(1))
          .on_click((_e, cx) => { // a click focuses the Identity and takes the keys from any text field
            this.at = n;
            setShared("focus", "detail");
            focusMain();
            this.target();
            cx.notify();
          })
          .child(div().w(16).flex_none().text_color(t.foreground).child(KIND_ICON[i.kind] ?? ""))
          .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(i.value))
          .when(Boolean(i.blocked), (el) => el.child(div().text_color(t.red).child("\u{f00ad} blocked"))))))
        .children(segLines(t, pg.rest, "r", pg.orgs ?? {}, nav)))
      .child(v_flex().id("person-context").w(320).flex_none().h_full().overflow_y_scroll().px(16).py(14).gap(8).border_l(1)
        .border_color(t.border).text_size(12.5)
        .children((this.context ?? []).map((s) => v_flex().p(8).gap(2).rounded(6).border(1).border_color(t.border)
          .child(div().text_color(t.muted).child(s.title)).children(segLines(t, s.lines, `c${s.title}`))))));
  }
}
