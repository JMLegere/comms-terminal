// One Connection, as the Console's ConnectionPage (D145): name and OK/BROKEN/UNAVAILABLE, the Platform's guidance
// for that state, account, connected, last health check, problem. The Slack Connection also lists its channel rules
// (which channels sync, D144): J/K pick a rule; a adds one, x deletes the picked one (z undoes).
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const RULE = { channel: "is exactly", prefix: "starts with", suffix: "ends with" };
const EXAMPLE = { channel: "e.g. general", prefix: "e.g. ext-", suffix: "e.g. -alerts" };
const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
const offerUndo = (message, steps) => { setShared("undo", { message, steps }); toast(`${message}  z undo`); };

export default class Connection extends View {
  init(_props, cx) {
    this.rules = [];
    this.at = 0;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && ["channels", "connection"].includes(shared("page"))) this.load(cx);
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
        const [page, rules] = await Promise.all([call("connection.page", { connection_id: String(id) }),
          call("channels.list", { connection_id: id })]);
        if (shared("connection") !== id) return;
        this.conn = page;
        this.rules = page.slack === id ? rules : [];
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
        if (!this.conn?.slack) return toast("No Slack connection yet: run comms-terminal init with --slack-team first.", "error");
        setShared("picker", { title: "Sync channels whose name…", reply: "connection.kind",
          options: Object.entries(RULE).map(([value, label]) => ({ value, label: `${label}…` })) });
        return run("overlay.open");
      case "kind":
        this.kind = answer.value;
        setShared("picker", { title: `Name ${RULE[answer.value]}`, options: [], prompt: EXAMPLE[answer.value], reply: "connection.value" });
        return run("overlay.open");
      case "value": {
        const value = answer.text.replace(/^#/, "");
        if (!value) return;
        return cx.spawn(async () => {
          try {
            await call("command.inbox.add_channel_rule", { connection_id: this.conn.slack, kind: this.kind, value });
            toast(`Rule added: channels whose name ${RULE[this.kind]} “${value}” now sync.`);
          } catch (e) {
            toast(String(e), "error");
          }
        });
      }
      case "delete": {
        const rule = this.rules[this.at];
        if (!rule) return;
        return cx.spawn(async () => {
          try {
            await call("command.inbox.delete_channel_rule", { rule_id: rule.id });
            offerUndo(`Rule deleted: “${rule.value}” channels stop syncing.`,
              [["inbox.add_channel_rule", { connection_id: this.conn.slack, kind: rule.kind, value: rule.value }]]);
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
    if (!c) return pane.child(div().p(24).text_color(t.muted).child(this.error ?? "No Connection selected"));
    const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
    return pane.child(v_flex().id("connection").flex_1().min_h(0).overflow_y_scroll().px(20).py(14).gap(2)
      .children(c.lines.map((segs) => (segs.length ? h_flex().w_full().flex_wrap()
        .children(segs.map(([text, hex, bold]) => div().min_w(0).text_color(color(hex)).when(Boolean(bold), (d) => d.font_semibold()).child(text)))
        : div().h(10))))
      .when(c.slack === shared("connection"), (el) => el
        .child(div().pt(16).font_semibold().text_color(t.foreground).child("Channel rules"))
        .child(div().text_color(t.muted).child("Slack DMs and group DMs always sync. A channel syncs when one of these rules matches its name."))
        .when(!this.rules.length, (e) => e.child(div().pt(4).text_color(t.muted).child("No rules yet. Press a to add one.")))
        .children(this.rules.map((r, n) => h_flex().id(`r-${r.id}`).h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer()
          .when(n === this.at, (e) => e.bg(t.selected))
          .on_click((_e, cx) => { this.at = n; cx.notify(); })
          .child(div().w(90).text_color(t.muted).child(RULE[r.kind] ?? r.kind))
          .child(div().font_semibold().text_color(t.foreground).child(r.value))))));
  }
}
