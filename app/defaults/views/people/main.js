// People: every Person, with Todo Threads first. Type in the filter to narrow by name or address; j/k move and
// the Person fills the right pane. g merges the selected Person into another.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle, InputState, Input } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain, toast } from "comms";

export default class People extends View {
  init(_props, cx) {
    this.rows = [];
    this.scroll = VirtualListScrollHandle.new();
    this.filter = InputState.new({ placeholder: "Filter people · / " });
    this.filter.on("change", (_e, cx) => cx.notify());
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "people") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  shown() {
    const f = this.filter.value().trim().toLowerCase();
    return f ? this.rows.filter((r) => `${r.name} ${r.address} ${(r.organizations ?? []).join(" ")}`.toLowerCase().includes(f)) : this.rows;
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.rows = (await call("people.list")).sort((a, b) => (b.todo - a.todo) || (b.threads - a.threads));
        this.error = null;
        if (shared("page") === "people" && !shared("person")) this.select(this.shown()[0]?.id ?? null);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(id) {
    setShared("person", id);
    run("person.show");
    const at = this.shown().findIndex((r) => r.id === id);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const rows = this.shown();
    const i = rows.findIndex((r) => r.id === shared("person"));
    switch (name) {
      case "show": return this.load(cx);
      case "filter": return this.filter.focus();
      case "next": case "prev": {
        const row = rows[Math.max(0, Math.min(rows.length - 1, i + (name === "next" ? 1 : -1)))];
        return row && this.select(row.id);
      }
      case "merge": {
        const keep = rows[i];
        if (!keep) return;
        setShared("picker", { title: `Merge into ${keep.name}: whose Identities move here?`, reply: "people.merge-picked",
          options: this.rows.filter((r) => r.id !== keep.id).slice(0, 400).map((r) => ({ value: r.id, label: r.name, hint: r.address })) });
        return run("overlay.open");
      }
      case "merge-picked": {
        const keep = rows[i], drop = this.rows.find((r) => r.id === answer.value);
        if (!keep || !drop) return;
        return cx.spawn(async () => {
          try {
            await call("command.inbox.merge_persons", { keep: keep.id, drop: drop.id });
            toast(`Merged ${drop.name} into ${keep.name}.`);
          } catch (e) {
            toast(String(e), "error");
          }
        });
      }
    }
  }

  render() {
    const t = theme();
    const selected = shared("person");
    const rows = this.shown();
    const row = (r) => {
      const active = r.id === selected;
      return v_flex().h(46).px(8).justify_center().rounded(8).gap(1).cursor_pointer()
        .when(active, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
        .child(h_flex().gap(9).items_center()
          .child(div().flex_1().min_w(0).truncate().font_medium().text_color(r.blocked ? t.dim : t.foreground).child(r.name ?? ""))
          .when(r.todo > 0, (el) => el.child(div().flex_none().text_size(11).text_color(t.status_needs_next).child(`${r.todo} todo`)))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(`${r.threads}`)))
        .child(div().truncate().text_size(12).text_color(t.muted).child([r.address, ...(r.organizations ?? [])].filter(Boolean).join(" · ")));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/users.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("People"))
        .child(div().flex_1().text_color(t.dim).child(`· ${rows.length}`))
        .child(div().text_size(11).text_color(t.dim).child("g merge")))
      .child(div().px(8).pt(8).on_key_down((ev, cx) => {
        if (ev.keystroke === "escape" || ev.keystroke === "enter") { focusMain(); cx.stop_propagation(); }
        else if (ev.keystroke === "down") { this.command("next", cx); cx.stop_propagation(); }
        else if (ev.keystroke === "up") { this.command("prev", cx); cx.stop_propagation(); }
      }).child(Input.new(this.filter)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .child(div().flex_1().min_h(0).px(6).py(6).child(
        v_virtual_list("people", rows.length, 46, (i) => rows[i].id, (range) => rows.slice(range.start, range.end).map(row))
          .size_full().track_scroll(this.scroll).on_item_click((key) => this.select(key))));
  }
}
