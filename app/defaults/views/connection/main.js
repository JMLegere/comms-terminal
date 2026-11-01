// One Connection: its health and its channel rules (which Slack channels or email folders become Threads).
// J/K pick a rule; a adds one, x deletes the picked one.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const RULE = { channel: "is exactly", prefix: "starts with", suffix: "ends with" };

export default class Connection extends View {
  init(_props, cx) {
    this.rules = [];
    this.at = 0;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "channels") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    const id = shared("connection");
    if (!id) return;
    cx.spawn(async (cx) => {
      try {
        const [all, rules] = await Promise.all([call("connections.list"), call("channels.list", { connection_id: id })]);
        if (shared("connection") !== id) return;
        this.conn = all.find((c) => c.id === id);
        this.rules = rules;
        this.at = Math.min(this.at, Math.max(rules.length - 1, 0));
        this.error = null;
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
    const id = shared("connection");
    switch (name) {
      case "show": return this.load(cx);
      case "next": case "prev":
        this.at = Math.max(0, Math.min(this.rules.length - 1, this.at + (name === "next" ? 1 : -1)));
        return cx.notify();
      case "add":
        setShared("picker", { title: "Add a channel rule: a name that…", reply: "connection.kind",
          options: Object.entries(RULE).map(([value, label]) => ({ value, label })) });
        return run("overlay.open");
      case "kind":
        this.kind = answer.value;
        setShared("picker", { title: `Channel name ${RULE[answer.value]}…`, options: [], prompt: "a channel name, without #", reply: "connection.value" });
        return run("overlay.open");
      case "value":
        return cx.spawn(async () => {
          try {
            await call("command.inbox.add_channel_rule", { connection_id: id, kind: this.kind, value: answer.text.replace(/^#/, "") });
            toast("Rule added.");
          } catch (e) {
            toast(String(e), "error");
          }
        });
      case "delete": {
        const rule = this.rules[this.at];
        if (!rule) return;
        return cx.spawn(async () => {
          try {
            await call("command.inbox.delete_channel_rule", { rule_id: rule.id });
            toast(`Rule removed: ${RULE[rule.kind]} ${rule.value}.`);
          } catch (e) {
            toast(String(e), "error");
          }
        });
      }
    }
  }

  render() {
    const t = theme();
    const c = this.conn;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!c) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Connection selected"));
    const bad = !c.auth_ok || Boolean(c.probe_error || c.last_error);
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg("icons/hash.svg").size(13).text_color(t.accent))
        .child(div().flex_1().font_medium().text_color(t.foreground).child("Connection"))
        .child(div().text_size(11).text_color(t.dim).child("a add rule · x delete rule · J/K pick")))
      .child(v_flex().id("connection").flex_1().min_h(0).overflow_y_scroll().px(24).py(18).gap(8)
        .child(div().text_size(18).font_semibold().text_color(t.strong ?? t.foreground).child(c.external_id))
        .child(div().text_color(bad ? t.danger : t.muted).child(bad ? (c.probe_error || c.last_error || "signed out") : `Healthy · checked ${(c.probed_at ?? "").slice(0, 16).replace("T", " ")}`))
        .child(div().text_color(t.dim).child(`${c.platform} · connected ${(c.connected_at ?? "").slice(0, 10)}${c.inbound ? ` · forward reaches ${c.inbound}` : ""}`))
        .child(div().pt(8).text_size(11.5).font_medium().text_color(t.dim).child("Channel rules"))
        .when(!this.rules.length, (el) => el.child(div().text_color(t.dim).child("None · a adds one")))
        .children(this.rules.map((r, n) => h_flex().id(`r-${r.id}`).h(28).px(8).gap(8).rounded(6).items_center().cursor_pointer()
          .when(n === this.at, (el) => el.bg(t.selected))
          .on_click((_e, cx) => { this.at = n; cx.notify(); })
          .child(div().w(90).text_color(t.dim).child(RULE[r.kind] ?? r.kind))
          .child(div().text_color(t.foreground).child(r.value)))));
  }
}
