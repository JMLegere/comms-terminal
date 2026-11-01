// Drafts: every Draft, newest first. j/k move and the Draft fills the right pane (the draft view); n starts a New
// Thread Draft on a Connection; x deletes; y copies it for an agent; Enter edits.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const FORMAT = { gmail: "email", fastmail: "email", slack: "slack", quo: "sms" };

function when(iso) {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 60) return `${Math.max(mins, 1)}m`;
  if (mins < 1440) return `${Math.round(mins / 60)}h`;
  return mins < 10080 ? `${Math.round(mins / 1440)}d` : iso.slice(5, 10);
}

export default class Drafts extends View {
  init(_props, cx) {
    this.rows = [];
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "drafts") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const was = this.rows.findIndex((r) => r.id === shared("draft"));
        this.rows = (await call("drafts.list")).sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
        this.error = null;
        if (shared("page") === "drafts" && !this.rows.some((r) => r.id === shared("draft")))
          this.select(this.rows[Math.max(0, Math.min(was, this.rows.length - 1))]?.id ?? null);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(id) {
    setShared("draft", id);
    run("draft.show");
    const at = this.rows.findIndex((r) => r.id === id);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const i = this.rows.findIndex((r) => r.id === shared("draft"));
    const id = shared("draft");
    switch (name) {
      case "show": return this.load(cx);
      case "next": case "prev": {
        const row = this.rows[Math.max(0, Math.min(this.rows.length - 1, i + (name === "next" ? 1 : -1)))];
        return row && this.select(row.id);
      }
      case "new":
        return cx.spawn(async () => {
          const conns = (await call("connections.list")).filter((c) => FORMAT[c.platform]);
          setShared("picker", { title: "New draft from", reply: "drafts.new-picked",
            options: conns.map((c) => ({ value: c.id, label: c.platform === "slack" ? c.label || "Slack" : c.external_id, hint: c.platform })) });
          run("overlay.open");
        });
      case "new-picked":
        return cx.spawn(async (cx) => {
          const c = (await call("connections.list")).find((c) => c.id === answer.value);
          if (!c) return;
          const kind = FORMAT[c.platform];
          try {
            const d = await call("command.inbox.save_draft", { values: { connection_id: c.id, kind, sender: c.self_id ?? "",
              to: "", cc: "", subject: kind === "email" ? "" : null, body: "" } });
            this.select(d.id);
            run("draft.edit");
          } catch (e) {
            toast(String(e), "error");
          }
          cx.notify();
        });
      case "delete":
        if (!id) return;
        setShared("picker", { title: "Delete this draft?", reply: "drafts.delete-picked",
          options: [{ value: "delete", label: "Delete the draft" }, { value: "keep", label: "Keep it" }] });
        return run("overlay.open");
      case "delete-picked":
        if (answer.value !== "delete") return;
        return cx.spawn(async () => {
          try {
            await call("command.inbox.delete_draft", { draft_id: id });
            toast("Draft deleted.");
          } catch (e) {
            toast(String(e), "error");
          }
        });
      case "copy":
        if (!id) return;
        return cx.spawn(async (cx) => {
          try {
            cx.write_to_clipboard(await call("draft.markdown", { draft_id: id }));
            toast("Copied the draft for an agent.");
          } catch (e) {
            toast(String(e), "error");
          }
        });
    }
  }

  render() {
    const t = theme();
    const selected = shared("draft");
    const rows = this.rows;
    const row = (r) => {
      const active = r.id === selected;
      const state = r.sending_at ? ["Send outcome unknown", t.danger] : r.send_error ? ["Scheduled send failed", t.danger]
        : r.send_at ? [`Sends ${r.send_at.slice(5, 16).replace("T", " ")}`, t.accent] : r.needs_edit ? ["Needs edit", t.status_waiting] : null;
      return v_flex().h(52).px(8).justify_center().rounded(8).gap(2).cursor_pointer()
        .when(active, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
        .child(h_flex().gap(9).items_center()
          .child(div().flex_1().min_w(0).truncate().font_medium().text_color(t.foreground).child(r.subject || r.to || "(no subject)"))
          .when(Boolean(state), (el) => el.child(div().flex_none().text_size(11).text_color(state[1]).child(state[0])))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(when(r.updated_at))))
        .child(div().truncate().text_color(t.muted).child(`${r.thread_id ? "Reply · " : ""}${r.to ? `${r.to} · ` : ""}${(r.body ?? "").split("\n")[0]}`));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/square-pen.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Drafts"))
        .child(div().flex_1().text_color(t.dim).child(`· ${rows.length}`))
        .child(div().text_size(11).text_color(t.dim).child("n new · x delete · y copy")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !rows.length, (el) => el.child(div().p(16).text_color(t.dim).child("No drafts. n starts one.")))
      .child(div().flex_1().min_h(0).px(6).py(6).child(
        v_virtual_list("drafts", rows.length, 52, (i) => rows[i].id, (range) => rows.slice(range.start, range.end).map(row))
          .size_full().track_scroll(this.scroll)
          .on_item_click((key) => this.select(key))));
  }
}
