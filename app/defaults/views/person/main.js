// One Person: Identities (J/K pick one; b blocks or unblocks it, x removes it, a adds one), Organizations (o),
// Enrollments (s enrolls in a Sequence, r resumes a paused one, f finishes one, 1/2 answer an If that asks Jeremy),
// Threads and Cases. n renames. Clicking a Thread opens it in Threads.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const VIEW = { TODO: "Todo", SNOOZED: "Snoozed", DONE: "Done", IGNORED: "Ignored" };

export default class Person extends View {
  init(_props, cx) {
    this.page = null;
    this.at = 0;
    this.enroll = {};
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
        if (this.page?.person?.id !== id) this.at = 0;
        this.page = page;
        this.error = null;
      } catch (e) {
        this.page = null;
        this.error = String(e);
      }
      cx.notify();
    });
  }

  pick(title, options, reply, prompt, value) {
    setShared("picker", { title, options, reply: `person.${reply}`, prompt, value });
    run("overlay.open");
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
    const identity = ids[Math.min(this.at, ids.length - 1)];
    const live = (this.page.enrollments ?? []).filter((e) => e.state !== "FINISHED");
    switch (name) {
      case "next": case "prev":
        this.at = Math.max(0, Math.min(ids.length - 1, this.at + (name === "next" ? 1 : -1)));
        return cx.notify();
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
          this.pick(`Enroll ${p.name}`, ch.sequences.map((q) => ({ value: q.id, label: q.name,
            hint: q.live ? `already in it, ${q.live.toLowerCase()}` : `${q.steps} Steps` })), "enroll-sequence");
        });
      case "enroll-sequence": {
        this.enroll.sequence = answer.value;
        const orgs = this.enroll.choices.organizations.map((o) => ({ value: o.id, label: o.name ?? o.domain, hint: o.domain }));
        return this.pick(`For which Organization? · ${p.name}`, [...orgs, { value: "sole", label: `New sole proprietorship: ${p.name}` },
          ...(orgs.length ? [] : [{ value: "none", label: "None for now" }])], "enroll-org");
      }
      case "enroll-org":
        this.enroll.org = answer.value;
        return cx.spawn(async () => {
          const org = ["sole", "none"].includes(answer.value) ? "" : answer.value;
          const ch = await call("enroll.choices", { person_id: p.id, organization_id: org }).catch(() => null);
          const addresses = ch?.addresses ?? [];
          if (addresses.length < 2) return this.pick("Start", [{ value: "today", label: "Start today" }, { value: "later", label: "Start later…" }], "enroll-start");
          this.pick(`Send to which address? · ${p.name}`, addresses.map((a) => ({ value: a.id, label: a.value })), "enroll-address");
        });
      case "enroll-address":
        this.enroll.identity = answer.value;
        return this.pick("Start", [{ value: "today", label: "Start today" }, { value: "later", label: "Start later…" }], "enroll-start");
      case "enroll-start":
        if (answer.value === "later") return this.pick("Start on", [], "enroll-typed", "2026-12-01, or a number of days from today");
        return this.enrollNow(null, cx);
      case "enroll-typed": {
        const text = answer.text.trim();
        let start = null;
        if (/^\d+$/.test(text)) { const d = new Date(); d.setDate(d.getDate() + +text); start = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
        else if (/^\d{4}-\d{2}-\d{2}$/.test(text)) start = text;
        if (!start) return toast(`Not enrolled: ${text} is neither a date (2026-12-01) nor a number of days.`, "error");
        return this.enrollNow(start, cx);
      }
      case "resume": {
        const paused = live.filter((e) => e.state === "PAUSED");
        if (!paused.length) return toast("No paused Enrollment here.", "warning");
        return this.pick("Resume which?", paused.map((e) => ({ value: e.id, label: e.sequence })), "resumed");
      }
      case "resumed":
        return cx.spawn(async () => this.write("sequences.resume", { enrollment_id: answer.value }, "Resumed: the next Task is due now, under Tasks."));
      case "finish":
        if (!live.length) return toast("No live Enrollment here.", "warning");
        return this.pick("Finish which?", live.map((e) => ({ value: e.id, label: e.sequence, hint: e.state.toLowerCase() })), "finish-which");
      case "finish-which":
        this.finishing = answer.value;
        return cx.spawn(async () => {
          const ch = await call("enroll.choices", { person_id: p.id, organization_id: "" });
          this.pick("Why is it finished?", Object.entries(ch.reasons).map(([value, label]) => ({ value, label,
            hint: ch.finishing.includes(value) ? "runs On completion" : "ends at once" })), "finish-why");
        });
      case "finish-why":
        return cx.spawn(async () => this.write("sequences.finish", { enrollment_id: this.finishing, reason: answer.value },
          "Finished. Its unsent Task Drafts are gone."));
      case "pick_first": case "pick_second": return this.answerIf(name === "pick_first" ? 1 : 0, cx);
    }
  }

  enrollNow(start, cx) {
    const p = this.page.person, e = this.enroll;
    cx.spawn(async () => {
      let org = e.org === "none" ? null : e.org;
      if (org === "sole") org = await this.write("inbox.create_sole_proprietorship", { person_id: p.id });
      if (org === undefined) return;
      await this.write("sequences.enroll", { person_id: p.id, sequence_id: e.sequence, start: start ? { $date: start } : null,
        organization_id: org, identity_id: e.identity ?? null }, `Enrolled ${p.name}: the first Task shows under Tasks.`);
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
    const p = this.page?.person;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!p) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Person selected"));
    const label = (text) => div().pt(6).text_size(11.5).font_medium().text_color(t.dim).child(text);
    const ids = p.identities ?? [];
    const threads = (p.threads ?? []).map((x) => x.f ?? x);
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg("icons/users.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Person"))
        .child(div().flex_1())
        .child(div().text_size(11).text_color(t.dim).child("n rename · a add · x remove · b block · o orgs · s enroll")))
      .child(v_flex().id("person").flex_1().min_h(0).overflow_y_scroll().px(24).py(18).gap(8)
        .child(div().text_size(18).font_semibold().text_color(t.strong ?? t.foreground).child(p.name ?? ""))
        .child(label("Identities · J/K pick"))
        .child(v_flex().gap(2).children(ids.map((i, n) =>
          h_flex().id(`i-${i.id}`).h(28).px(8).gap(8).rounded(6).items_center().cursor_pointer()
            .when(n === this.at, (el) => el.bg(t.selected))
            .on_click((_e, cx) => { this.at = n; cx.notify(); })
            .child(div().w(80).flex_none().text_size(11.5).text_color(t.dim).child(i.kind))
            .child(div().flex_1().min_w(0).truncate().text_color(i.blocked ? t.dim : t.foreground).child(i.value))
            .when(Boolean(i.blocked), (el) => el.child(div().text_size(11).text_color(t.danger).child("blocked"))))))
        .child(label("Organizations"))
        .child(div().text_color(t.foreground).child((p.organizations ?? []).join(", ") || "—"))
        .child(label("Enrollments"))
        .child(v_flex().gap(2).children((this.page.enrollments ?? []).map((e) =>
          h_flex().gap(8).text_size(12.5).child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(e.sequence ?? ""))
            .child(div().text_size(11).text_color(t.dim).child((e.state ?? "").toLowerCase())))))
        .when(!(this.page.enrollments ?? []).length, (el) => el.child(div().text_color(t.dim).child("— · s enrolls")))
        .child(label("Threads"))
        .child(v_flex().gap(2).children(threads.map((th) =>
          div().id(`t-${th.id}`).truncate().cursor_pointer().text_color(t.foreground).hover((s) => s.text_color(t.accent))
            .on_click(() => { setShared("thread", th.id); setShared("view", VIEW[th.status] ?? "Todo"); setShared("page", "threads"); run("threads.reload"); })
            .child(th.title ?? ""))))
        .child(label("Cases"))
        .child(div().text_color(t.foreground).child((p.cases ?? []).join(", ") || "—")));
  }
}
