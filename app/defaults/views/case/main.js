// One Case: its description (d edits it), its Threads, and its Activity feed. J/K pick a Thread, and the Thread
// keys (e, g, m, l, s, r, …) act on it as they do in a Status view; Enter opens it in Threads.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const DETAIL = { CREATED: ["Needs first response", "status_needs_first"], NEW_REPLY: ["Needs next response", "status_needs_next"],
  IN_PROGRESS: ["Investigating", "status_investigating"], WAITING_FOR_CUSTOMER: ["Waiting for them", "status_waiting"],
  WAITING_FOR_DURATION: ["Paused for later", "status_waiting"], DONE_MANUALLY_SET: ["Done", "status_done"],
  DONE_AUTOMATICALLY_SET: ["Done", "status_done"], IGNORED: ["Ignored", "status_ignored"] };
const FEED = { created: "Case created", added: "Thread added", removed: "Thread removed", description: "Description changed" };
const VIEW = { TODO: "Todo", SNOOZED: "Snoozed", DONE: "Done", IGNORED: "Ignored" };

export default class Case extends View {
  init(_props, cx) {
    this.page = null;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "cases") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  threads() {
    return (this.page?.threads ?? []).map((t) => t.f ?? t);
  }

  load(cx) {
    const id = shared("case");
    if (!id) {
      this.page = null;
      return cx.notify();
    }
    cx.spawn(async (cx) => {
      try {
        const page = await call("case.page", { case_id: id });
        if (shared("case") !== id) return;
        this.page = page;
        this.error = null;
        const threads = this.threads();
        if (shared("page") === "cases" && !threads.some((t) => t.id === shared("thread"))) {
          setShared("thread", threads[0]?.id ?? null);
          run("thread.show");
        }
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const threads = this.threads();
    const i = threads.findIndex((t) => t.id === shared("thread"));
    switch (name) {
      case "show": return this.load(cx);
      case "next": case "prev": {
        const t = threads[Math.max(0, Math.min(threads.length - 1, i + (name === "next" ? 1 : -1)))];
        if (t) { setShared("thread", t.id); run("thread.show"); }
        return cx.notify();
      }
      case "toggle": case "open": {
        const t = threads[i];
        if (!t) return;
        setShared("view", VIEW[t.status] ?? "Todo");
        setShared("page", "threads");
        return run("threads.reload");
      }
      case "describe":
        if (!this.page?.case) return;
        setShared("picker", { title: "Case description", options: [], prompt: "What this Case is for", reply: "case.described",
          value: this.page.case.description ?? "" });
        return run("overlay.open");
      case "described":
        return cx.spawn(async () => {
          try {
            await call("command.inbox.set_case_description", { case_id: this.page.case.id, description: answer.text });
            toast("Description saved.");
          } catch (e) {
            toast(String(e), "error");
          }
        });
    }
  }

  render() {
    const t = theme();
    const p = this.page;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!p?.case) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Case selected"));
    const selected = shared("thread");
    const threads = this.threads();
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg("icons/folder.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Case"))
        .child(div().text_color(t.dim).child(`· ${threads.length} thread${threads.length === 1 ? "" : "s"}`)))
      .child(v_flex().id("case").flex_1().min_h(0).overflow_y_scroll().px(24).py(18).gap(14)
        .child(div().text_size(18).font_semibold().text_color(t.strong ?? t.foreground).child(p.case.title))
        .child(div().id("case-description").cursor_pointer().text_color(p.case.description ? t.foreground : t.dim).line_height(1.5)
          .on_click((_e, cx) => this.command("describe", cx))
          .child(p.case.description || "No description · d to write one"))
        .child(div().text_size(11.5).font_medium().text_color(t.dim).child("Threads"))
        .child(v_flex().gap(2).children(threads.map((th) => {
          const [label, token] = DETAIL[th.status_detail] ?? [th.status_detail, "dim"];
          return h_flex().id(`ct-${th.id}`).h(32).px(8).gap(9).rounded(8).items_center().cursor_pointer()
            .when(th.id === selected, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
            .on_click(() => { setShared("thread", th.id); run("thread.show"); })
            .child(div().size(7).rounded(4).flex_none().bg(t[token] ?? t.dim))
            .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(th.title ?? ""))
            .child(div().flex_none().text_size(11).text_color(t.dim).child(label));
        })))
        .child(div().text_size(11.5).font_medium().text_color(t.dim).child("Activity"))
        .child(v_flex().gap(4).text_size(12).children([...(p.feed ?? [])].reverse().map((f) =>
          h_flex().gap(10).child(div().w(110).flex_none().text_color(t.dim).child((f.at ?? "").slice(0, 16).replace("T", " ")))
            .child(div().flex_1().min_w(0).truncate().text_color(t.muted)
              .child(`${FEED[f.kind] ?? f.kind}${f.title ? ` · ${f.title}` : ""}${f.cause && f.cause !== "action" ? ` (${f.cause})` : ""}`))))));
  }
}
