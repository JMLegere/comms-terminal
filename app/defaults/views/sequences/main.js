// Sequences: every Sequence with its Steps, days and Enrollments. j/k move and its Steps fill the right pane.
// n new, R rename, w "use when" description, f sends from, a archive or restore, x delete (an unused one).
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

export default class Sequences extends View {
  init(_props, cx) {
    this.rows = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "sequences") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.rows = (await call("sequences.list")).sort((a, b) => Boolean(a.archived_at) - Boolean(b.archived_at) || a.name.localeCompare(b.name));
        this.error = null;
        if (shared("page") === "sequences" && !this.rows.some((r) => r.id === shared("sequence"))) this.select(this.rows[0]?.id);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(id) {
    if (!id) return;
    setShared("sequence", id);
    run("sequence.show");
  }

  pick(title, options, reply, prompt, value) {
    setShared("picker", { title, options, reply: `sequences.${reply}`, prompt, value });
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
    const i = this.rows.findIndex((r) => r.id === shared("sequence"));
    const q = this.rows[i];
    switch (name) {
      case "show": return this.load(cx);
      case "next": case "prev":
        return this.select(this.rows[Math.max(0, Math.min(this.rows.length - 1, i + (name === "next" ? 1 : -1)))]?.id);
      case "new": return this.pick("New Sequence", [], "named", "its name");
      case "named":
        return cx.spawn(async () => {
          const id = await this.write("sequences.create_sequence", { name: answer.text }, `Sequence “${answer.text}” created: n adds Steps.`);
          if (id) this.select(String(id));
        });
      case "rename": return q && this.pick(`Rename ${q.name}`, [], "renamed", q.name, q.name);
      case "renamed": return cx.spawn(async () => this.write("sequences.rename_sequence", { sequence_id: q.id, name: answer.text }, "Renamed."));
      case "describe": return q && this.pick(`Use ${q.name} when…`, [], "described", "who it is for and when to use it", q.description ?? "");
      case "described": return cx.spawn(async () => this.write("sequences.describe_sequence", { sequence_id: q.id, description: answer.text }, "Saved."));
      case "sends_from":
        if (!q) return;
        return cx.spawn(async () => {
          const conns = (await call("connections.list")).filter((c) => c.platform === "gmail" || c.platform === "fastmail");
          this.pick(`${q.name} sends from`, conns.map((c) => ({ value: c.id, label: c.external_id, hint: c.id === q.connection_id ? "now" : "" })), "sends-picked");
        });
      case "sends-picked": return cx.spawn(async () => this.write("sequences.set_connection", { sequence_id: q.id, connection_id: answer.value }, "Its email Tasks send from there now."));
      case "archive":
        if (!q) return;
        if (q.archived_at) return cx.spawn(async () => this.write("sequences.restore_sequence", { sequence_id: q.id, undo: [] }, `${q.name} restored.`));
        if (!q.active && !q.paused) return cx.spawn(async () => this.write("sequences.archive_sequence", { sequence_id: q.id, cancel_live: false }, `${q.name} archived.`));
        return this.pick(`Archive ${q.name}: ${q.active + q.paused} live Enrollments`, [{ value: "keep", label: "Archive · let them finish" },
          { value: "cancel", label: "Archive · finish them now as Canceled" }], "archive-picked");
      case "archive-picked":
        return cx.spawn(async () => this.write("sequences.archive_sequence", { sequence_id: q.id, cancel_live: answer.value === "cancel" }, `${q.name} archived.`));
      case "delete":
        if (!q) return;
        if (q.used) return toast(`${q.name} has been used: archive it (a) instead.`, "warning");
        return this.pick(`Delete ${q.name}?`, [{ value: "delete", label: "Delete it" }, { value: "keep", label: "Keep it" }], "delete-picked");
      case "delete-picked":
        if (answer.value !== "delete") return;
        return cx.spawn(async () => this.write("sequences.delete_sequence", { sequence_id: q.id }, `${q.name} deleted.`));
    }
  }

  render() {
    const t = theme();
    const selected = shared("sequence");
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/workflow.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Sequences"))
        .child(div().flex_1().text_color(t.dim).child(`· ${this.rows.filter((r) => !r.archived_at).length}`))
        .child(div().text_size(11).text_color(t.dim).child("n new · R rename · w use when · a archive")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .child(v_flex().id("sequences").flex_1().min_h(0).overflow_y_scroll().px(6).py(6).children(this.rows.map((q) =>
        v_flex().id(`q-${q.id}`).h(50).px(8).justify_center().rounded(8).gap(1).cursor_pointer()
          .when(q.id === selected, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
          .on_click(() => this.select(q.id))
          .child(h_flex().gap(9).items_center()
            .child(div().flex_1().min_w(0).truncate().font_medium().text_color(q.archived_at ? t.dim : t.foreground).child(q.name))
            .when(q.due_today > 0, (el) => el.child(div().text_size(11).text_color(t.status_needs_next).child(`${q.due_today} due`)))
            .child(div().text_size(11).text_color(t.dim).child(q.archived_at ? "archived" : `${q.active} active`)))
          .child(div().truncate().text_size(12).text_color(t.muted).child(`${q.steps} Steps · ${q.days} days${q.description ? ` · ${q.description}` : ""}`)))));
  }
}
