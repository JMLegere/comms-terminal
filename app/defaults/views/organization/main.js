// One Organization: its Pipeline Stages and Values (s sets a Stage, v a Value, each in a picked Pipeline), its
// People and their Threads, and its Stage and Value history. Clicking a Person opens them in People.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const grouped = (x) => String(Math.round(x)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
const VIEW = { TODO: "Todo", SNOOZED: "Snoozed", DONE: "Done", IGNORED: "Ignored" };

export default class Organization extends View {
  init(_props, cx) {
    this.page = null;
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
    if (!org?.domain) {
      this.page = null;
      return cx.notify();
    }
    cx.spawn(async (cx) => {
      try {
        const [page, list, history, stages] = await Promise.all([call("organization.page", { domain: org.domain }),
          call("organizations.list"), call("organization.history", { org_id: org.id }), this.stages ? this.stages : call("pipeline.stages")]);
        if (shared("organization")?.id !== org.id) return;
        this.page = page;
        this.state = list.states?.[org.id] ?? {};
        this.history = history ?? [];
        this.stages = stages;
        this.error = null;
      } catch (e) {
        this.page = null;
        this.error = String(e);
      }
      cx.notify();
    });
  }

  pick(title, options, reply, prompt) {
    setShared("picker", { title, options, reply: `organization.${reply}`, prompt });
    run("overlay.open");
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const org = shared("organization");
    const pipelines = Object.entries(this.stages ?? {});
    // On a Pipeline page the Pipeline is already chosen.
    const chosen = shared("page") === "pipeline" ? (shared("pipeline") ?? "revenue").toUpperCase() : null;
    switch (name) {
      case "show": return this.load(cx);
      case "stage": case "value":
        if (!org || !pipelines.length) return;
        this.doing = name;
        if (chosen) return this.command(`pipeline-picked:${JSON.stringify({ value: chosen })}`, cx);
        return this.pick(`${name === "stage" ? "Stage" : "Value"} in which Pipeline?`,
          pipelines.map(([p, d]) => ({ value: p, label: d.name, hint: this.state?.[p]?.stage ?? "" })), "pipeline-picked");
      case "pipeline-picked": {
        this.pipeline = answer.value;
        const d = this.stages[answer.value];
        if (this.doing === "stage") return this.pick(`${d.name} Stage · ${org.domain}`, d.stages.map((s) => ({ value: s, label: s,
          hint: d.paying.includes(s) ? "paying" : d.ending.includes(s) ? "ending" : "" })), "stage-picked");
        return this.pick(`${d.name} Value (CAD) · ${org.domain}`, [], "value-typed", "a number, such as 85000; empty clears it");
      }
      case "stage-picked":
        return cx.spawn(async () => {
          try {
            await call("command.pipeline.set_stage", { org_id: org.id, pipeline: this.pipeline, stage: answer.value });
            toast(`${org.domain}: ${answer.value}.`);
          } catch (e) {
            toast(String(e), "error");
          }
        });
      case "value-typed": {
        const text = answer.text.replace(/[,$\s]/g, "");
        if (text && !/^\d+$/.test(text)) return toast(`Not set: ${answer.text} is not a whole number.`, "error");
        return cx.spawn(async () => {
          try {
            await call("command.pipeline.set_value", { org_id: org.id, pipeline: this.pipeline, value: text ? +text : null });
            toast(text ? `${org.domain}: CAD ${grouped(+text)}.` : `${org.domain}: Value cleared.`);
          } catch (e) {
            toast(String(e), "error");
          }
        });
      }
      case "people": {
        const people = this.page?.people ?? [];
        if (!people.length) return toast("Nobody at this Organization yet.");
        return this.pick(`People · ${org.domain}`, people.map((p) => ({ value: p.id, label: p.name })), "person-picked");
      }
      case "person-picked":
        setShared("person", answer.value);
        setShared("page", "people");
        return run("person.show");
    }
  }

  render() {
    const t = theme();
    const p = this.page;
    const org = shared("organization");
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!p) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Organization selected"));
    const label = (text) => div().pt(6).text_size(11.5).font_medium().text_color(t.dim).child(text);
    const threads = (p.threads ?? []).map((x) => x.f ?? x);
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg("icons/building-2.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Organization"))
        .child(div().flex_1())
        .child(div().text_size(11).text_color(t.dim).child("s stage · v value · p people")))
      .child(v_flex().id("organization").flex_1().min_h(0).overflow_y_scroll().px(24).py(18).gap(8)
        .child(div().text_size(18).font_semibold().text_color(t.strong ?? t.foreground).child(org?.domain ?? p.domain ?? ""))
        .child(label("Pipelines"))
        .children(Object.entries(this.stages ?? {}).map(([code, d]) => {
          const s = this.state?.[code];
          return h_flex().gap(10).text_size(12.5)
            .child(div().w(100).flex_none().text_color(t.dim).child(d.name))
            .child(div().flex_1().text_color(s ? t.foreground : t.dim).child(s ? s.stage : "—"))
            .child(div().text_color(t.muted).child(s?.value != null ? `CAD ${grouped(s.value)}` : ""));
        }))
        .child(label("People"))
        .child(v_flex().gap(2).children((p.people ?? []).map((person) =>
          div().id(`p-${person.id}`).cursor_pointer().truncate().text_color(t.foreground).hover((s) => s.text_color(t.accent))
            .on_click(() => { setShared("person", person.id); setShared("page", "people"); run("person.show"); })
            .child(`${person.name}${(person.identities ?? []).length ? ` · ${person.identities[0].value}` : ""}`))))
        .child(label("Threads"))
        .child(v_flex().gap(2).children(threads.map((th) =>
          div().id(`t-${th.id}`).truncate().cursor_pointer().text_color(t.foreground).hover((s) => s.text_color(t.accent))
            .on_click(() => { setShared("thread", th.id); setShared("view", VIEW[th.status] ?? "Todo"); setShared("page", "threads"); run("threads.reload"); })
            .child(th.title ?? ""))))
        .when(Boolean(this.history?.length), (el) => el.child(label("History"))
          .child(v_flex().gap(2).text_size(12).children(this.history.slice(0, 20).map((h) =>
            h_flex().gap(10).child(div().w(110).flex_none().text_color(t.dim).child(String(h.at?.$dt ?? h.at ?? "").slice(0, 10)))
              .child(div().flex_1().text_color(t.muted).child(`${this.stages?.[h.pipeline]?.name ?? h.pipeline} ${h.kind.toLowerCase()}: ${h.old ?? "—"} → ${h.new ?? "—"}`)))))));
  }
}
