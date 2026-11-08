// Settings (FR-SET-1..5), the App's take on herdr-app's Settings window: the gear at the bottom of the rail or
// ctrl+, opens it over the App (shared "settings" = {section}); sections down the left: Look, Keys, Service &
// updates, Diagnostics. Every change applies at once (settings.json; no Save step). esc or × closes it.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { call, nextCommand, theme, shared, setShared, run, status, keys, fonts, setting, settings, logTail, focusMain, toast } from "comms";

const SECTIONS = [["look", "Look", "monitor"], ["modules", "Modules", "folder"], ["keys", "Keys", "file-text"],
  ["service", "Service & updates", "server"], ["diagnostics", "Diagnostics", "layers"]];
/** Settings › Modules (FR-MOD-13): each Module and what hides while it is off, worded as the Console's MODULE_LINES. */
export const MODULES = [
  ["pipelines", "Pipelines", "Revenue, Fundraising and Talent; Stages and Values; Unattended on Today"],
  ["sequences", "Sequences", "Sequences, Tasks and Enrollments; Tasks and Paused on Today and the Calendar"],
  ["cases", "Cases", "Cases in the rail, Case headers in Threads lists, the case key"],
  ["issues", "Issues", "Issues in the rail, Issues on Today and the Calendar, the issue commands"],
];
/** Settings › Modules: the Undo window's choices in seconds, as Gmail offers (inbox.UNDO_WINDOWS, ADR 0021). */
export const WINDOWS = [0, 5, 10, 20, 30];
/** Settings › Service & updates: the update step (shared "update", window.rs prepare_update) in words. */
export const UPDATE_STEP = { building: "Building in the background", ready: "Ready · opens when you restart",
  failed: "Failed to build · see Diagnostics", unmanaged: "No launcher · reinstall the App" };
const DEFAULT_FONT = "JetBrainsMono Nerd Font Mono"; // the shipped settings.json's ui_font
const pad = (n) => String(n).padStart(2, "0");
const clock = (ms) => { const d = new Date(ms); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
/** "page:today" → "Today", "detail" → "Detail column": a keys.json section as the Keys list heads it. */
export const sectionName = (s) => s === "global" ? "Everywhere" : s === "always" ? "Always (also in text fields)"
  : s === "detail" ? "Detail column" : s === "rail" ? "Rail" : s.startsWith("page:") ? s[5].toUpperCase() + s.slice(6) : s;
/** One log entry as a report line for an agent (Copy report). */
export function reportLine(e) {
  const t = clock(e.t);
  if (e.kind === "key") return `${t} key ${e.key} -> ${e.action ?? "nothing"} (page ${e.page ?? "?"}, focus ${e.focus ?? "?"}${e.active === false ? ", window inactive" : ""})`;
  if (e.kind === "run") return `${t} run ${e.command}${e.to && e.to !== "sent" ? ` (${e.to})` : ""}`;
  if (e.kind === "toast") return `${t} toast [${e.severity ?? "information"}] ${e.message}`;
  if (e.kind === "error") return `${t} error ${e.view ?? ""}: ${e.error}`;
  if (e.kind === "status") return `${t} status online=${e.online} ${e.problem ?? ""}`.trim();
  const { t: _t, kind, ...rest } = e;
  return `${t} ${kind} ${JSON.stringify(rest)}`;
}

export default class Settings extends View {
  init(_props, cx) {
    this.fontSearch = InputState.new({ placeholder: "Search installed fonts" });
    this.keySearch = InputState.new({ placeholder: "Search keys or commands" });
    for (const [f, name] of [[this.fontSearch, "font search"], [this.keySearch, "key search"]]) {
      f.on("change", (_e, cx) => cx.notify());
      f.on("focus", () => setShared("typing", name));
      f.on("blur", () => { if (shared("typing") === name) setShared("typing", null); });
    }
    this.log = [];
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    if (name === "open") {
      const asked = at < 0 ? null : JSON.parse(c.slice(at + 1)).section;
      setShared("settings", { section: SECTIONS.some(([id]) => id === asked) ? asked : shared("settings")?.section ?? "look" });
      this.turned = {};
      this.loadLog();
      call("send.later").then((l) => { this.window = l?.window ?? null; cx.notify(); }).catch(() => {});
      return cx.notify();
    }
    if (name === "close") {
      setShared("settings", null);
      if (shared("typing")?.endsWith?.("search")) setShared("typing", null);
      focusMain();
      return cx.notify();
    }
  }

  section() { return shared("settings")?.section ?? "look"; }

  go(id, cx) {
    setShared("settings", { section: id });
    if (id === "diagnostics") this.loadLog();
    cx.notify();
  }

  loadLog() {
    try { this.log = logTail(2000) ?? []; } catch (e) { this.log = []; this.logError = String(e); }
  }

  set(key, value, done) {
    try { setting(key, value); if (done) toast(done); } catch (e) { toast(`Not saved: ${e}`, "error"); }
  }

  look(t) {
    const s = settings() ?? {};
    const current = s.ui_font ?? DEFAULT_FONT;
    const zoom = s.zoom ?? 1;
    const q = this.fontSearch.value().trim().toLowerCase();
    const list = (fonts() ?? []).filter((f) => !q || f.toLowerCase().includes(q)).slice(0, 200);
    const button = (id, label, command) => div().id(id).px(10).py(4).rounded(6).border(1).border_color(t.border).cursor_pointer()
      .hover((e) => e.bg(t.hover)).on_click(() => run(command)).child(label);
    return v_flex().gap(14)
      .child(this.title(t, "Zoom", "Scales the whole App; ctrl+= and ctrl+− from anywhere, ctrl+0 resets."))
      .child(h_flex().gap(8).items_center()
        .child(button("zoom-out", "−", "app.zoom_out"))
        .child(h_flex().w(56).justify_center().font_semibold().child(`${Math.round(zoom * 100)}%`))
        .child(button("zoom-in", "+", "app.zoom_in"))
        .child(button("zoom-reset", "Reset", "app.zoom_reset")))
      .child(this.title(t, "UI font", `Now ${current}. A click applies it at once.`))
      .child(Input.new(this.fontSearch))
      .child(v_flex().id("fonts").h(260).overflow_y_scroll().rounded(6).border(1).border_color(t.border)
        .children(list.map((f) => h_flex().id(`font-${f}`).h(26).px(10).items_center().cursor_pointer()
          .border_l(3).border_color(f === current ? t.accent : t.background)
          .when(f === current, (e) => e.bg(t.selected)).hover((e) => e.bg(t.hover))
          .on_click(() => this.set("ui_font", f === DEFAULT_FONT ? null : f, `UI font: ${f}`))
          .child(div().font_family(f).child(f)))));
  }

  keys(t) {
    const k = keys() ?? {};
    const q = this.keySearch.value().trim().toLowerCase();
    const groups = Object.entries(k).map(([sec, map]) => [sec, Object.entries(map ?? {})
      .filter(([key, cmd]) => !q || key.toLowerCase().includes(q) || String(cmd).toLowerCase().includes(q) || sectionName(sec).toLowerCase().includes(q))])
      .filter(([, rows]) => rows.length);
    const button = (id, label, onClick) => div().id(id).px(10).py(4).rounded(6).border(1).border_color(t.border).cursor_pointer()
      .hover((e) => e.bg(t.hover)).on_click(onClick).child(label);
    return v_flex().gap(10)
      .child(this.title(t, "Keys", "Every key per page, from keys.json. Edit the file to change one; it reloads as you save."))
      .child(h_flex().gap(8)
        .child(button("edit-keys", "Open keys.json", () => run("app.edit_keys")))
        .child(button("reset-keys", "Reset to shipped keys", () => {
          run("app.reset");
          toast("Keys and layout reset to the shipped ones; the old files are kept as *.reset-<time>.");
        })))
      .child(Input.new(this.keySearch))
      .child(v_flex().id("keys-list").h(340).overflow_y_scroll().gap(8)
        .children(groups.map(([sec, rows]) => v_flex().gap(0)
          .child(div().pt(4).pb(2).text_size(11.5).font_semibold().text_color(t.dim).child(sectionName(sec)))
          .children(rows.map(([key, cmd]) => h_flex().h(22).gap(12).items_center()
            .child(div().w(120).flex_none().font_semibold().text_color(t.blue ?? t.accent).child(key))
            .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(String(cmd))))))));
  }

  service(t) {
    const c = status() ?? {};
    const differ = Boolean(c.service_build && c.build && !c.build.endsWith(c.service_build));
    const row = (label, value, color) => h_flex().gap(12).child(div().w(140).flex_none().text_color(t.dim).child(label))
      .child(div().flex_1().min_w(0).text_color(color ?? t.foreground).child(value ?? "—"));
    const button = (id, label, onClick) => div().id(id).px(10).py(4).rounded(6).border(1).border_color(t.border).cursor_pointer()
      .hover((e) => e.bg(t.hover)).on_click(onClick).child(label);
    return v_flex().gap(8)
      .child(this.title(t, "Service", "Where the App reads and writes; it keeps a local copy and sends each write there."))
      .child(row("Service", c.service ?? "This machine"))
      .child(row("State", c.online ? "Online" : `Read-only · ${c.problem ?? "offline"}`, c.online ? t.green : t.danger))
      .child(row("Writes waiting", String(c.pending ?? 0)))
      .child(div().h(8))
      .child(this.title(t, "Updates", "The App runs the build the Service runs. A new one builds in the background and opens the next time you start the App."))
      .child(row("This App", c.build || "?"))
      .child(row("Service runs", c.service_build ?? "?", differ ? t.accent : undefined))
      .when(differ, (el) => el.child(row("Update", UPDATE_STEP[shared("update")] ?? "Waiting to build", shared("update") === "failed" ? t.danger : undefined)))
      .child(h_flex().gap(8).pt(6)
        .when(differ && !["building", "ready"].includes(shared("update")), (el) => el.child(button("update", shared("update") === "failed" ? "Try again" : "Update now", () => {
          run("app.update");
          toast("Building the Service's version; it opens the next time you start the App.");
        })))
        .child(button("restart", "Restart App", () => run("app.restart"))))
      .when(!differ, (el) => el.child(div().text_color(t.dim).child("Up to date with the Service.")));
  }

  modules(t) {
    const on = new Set(shared("modules") ?? MODULES.map(([m]) => m));
    for (const [m, v] of Object.entries(this.turned ?? {})) v ? on.add(m) : on.delete(m); // shown before the copy follows
    return v_flex().gap(8)
      .child(this.title(t, "For this Service", "Every Client shows the same. An off Module hides everywhere; its data is kept."))
      .children(MODULES.map(([m, name, line]) => h_flex().id(`module-${m}`).gap(12).items_center().py(6)
        .child(v_flex().flex_1().min_w(0).child(div().font_semibold().child(name)).child(div().text_color(t.dim).child(line)))
        .child(div().id(`module-${m}-switch`).w(56).flex_none().py(4).rounded(6).border(1).border_color(t.border)
          .flex().justify_center().cursor_pointer().when(on.has(m), (e) => e.bg(t.selected)).hover((e) => e.bg(t.hover))
          .on_click((_e, cx) => this.turn(m, name, !on.has(m), cx)).child(on.has(m) ? "On" : "Off"))))
      .child(h_flex().id("undo-window").gap(12).items_center().py(6)  // the Undo window (ADR 0021), Gmail's choices
        .child(v_flex().flex_1().min_w(0).child(div().font_semibold().child("Undo window"))
          .child(div().text_color(t.dim).child("Send now waits this long, so z can call it back; 0 sends at once")))
        .children(WINDOWS.map((s) => div().id(`undo-window-${s}`).w(40).flex_none().py(4).rounded(6).border(1)
          .border_color(t.border).flex().justify_center().cursor_pointer().when(this.window === s, (e) => e.bg(t.selected))
          .hover((e) => e.bg(t.hover)).on_click((_e, cx) => this.setWindow(s, cx)).child(`${s} s`))));
  }

  /** The Undo window, written to this Service like a Module; z undoes it. */
  setWindow(seconds, cx) {
    const was = this.window;
    this.window = seconds;
    cx.notify();
    call("command.inbox.set_undo_window", { seconds }).then(() => toast(`Undo window ${seconds} s.  z undo`)).catch((e) => {
      this.window = was;
      toast(`Not saved: ${e}`, "error");
      cx.notify();
    });
  }

  /** One write through the Local core; z undoes it, as every change in the App (FR-MOD-13). */
  turn(m, name, value, cx) {
    this.turned = { ...this.turned, [m]: value };
    cx.notify();
    call("command.inbox.set_module", { module: m, enabled: value }).then(() => {
      const message = `${name} turned ${value ? "on" : "off"}.`;
      setShared("turned", m); // nav does not say it again
      toast(`${name} turned ${value ? "on" : "off"} · z undo`);
    }).catch((e) => {
      delete this.turned[m];
      toast(`Not saved: ${e}`, "error");
      cx.notify();
    });
  }

  diagnostics(t) {
    const log = this.log ?? [];
    const keysRows = log.filter((e) => e.kind === "key").slice(-50).reverse();
    const notes = log.filter((e) => e.kind === "error" || e.kind === "toast").slice(-30).reverse();
    const button = (id, label, onClick) => div().id(id).px(10).py(4).rounded(6).border(1).border_color(t.border).cursor_pointer()
      .hover((e) => e.bg(t.hover)).on_click(onClick).child(label);
    return v_flex().gap(8)
      .child(this.title(t, "Diagnostics", "From the App's own log (24 hours, on this machine). Typed text shows as <text>."))
      .child(h_flex().gap(8)
        .child(button("refresh-log", "Refresh", (_e, cx) => { this.loadLog(); cx.notify(); }))
        .child(button("copy-report", "Copy report for an agent", (_e, cx) => {
          const since = Date.now() - 3600e3;
          const lines = (logTail(5000) ?? []).filter((e) => e.t >= since).map(reportLine);
          cx.write_to_clipboard(`Comms Terminal App · diagnostic log, last hour (${lines.length} lines)\n${lines.join("\n")}`);
          toast(`Copied ${lines.length} log lines for an agent.`);
        })))
      .child(div().pt(4).text_size(11.5).font_semibold().text_color(t.dim).child("Recent keys"))
      .child(v_flex().id("diag-keys").h(200).overflow_y_scroll().text_size(12)
        .children(keysRows.map((e, i) => h_flex().id(`dk-${i}`).h(20).gap(10).items_center()
          .child(div().w(64).flex_none().text_color(t.dim).child(clock(e.t)))
          .child(div().w(90).flex_none().font_semibold().child(e.key ?? ""))
          .child(div().w(150).flex_none().truncate().text_color(t.dim).child(`${e.page ?? ""} · ${e.focus ?? ""}`))
          .child(div().flex_1().min_w(0).truncate().text_color(e.action ? t.foreground : t.danger).child(e.action ?? "nothing")))))
      .child(div().pt(4).text_size(11.5).font_semibold().text_color(t.dim).child("Errors and toasts"))
      .child(v_flex().id("diag-notes").h(140).overflow_y_scroll().text_size(12)
        .children(notes.map((e, i) => h_flex().id(`dn-${i}`).gap(10).items_start()
          .child(div().w(64).flex_none().text_color(t.dim).child(clock(e.t)))
          .child(div().flex_1().min_w(0).text_color(e.kind === "error" || e.severity === "error" ? t.danger : t.foreground)
            .child(e.kind === "error" ? `${e.view ?? ""}: ${e.error ?? ""}` : e.message ?? "")))));
  }

  title(t, head, note) {
    return v_flex().gap(2).child(div().font_semibold().text_color(t.strong ?? t.foreground).child(head))
      .child(div().text_size(12).text_color(t.dim).child(note));
  }

  render() {
    const t = theme();
    if (!shared("settings")) return div();
    const at = this.section();
    const body = at === "keys" ? this.keys(t) : at === "modules" ? this.modules(t) : at === "service" ? this.service(t)
      : at === "diagnostics" ? this.diagnostics(t) : this.look(t);
    return div().size_full().flex().justify_center().pt(60).bg("#00000033")
      .on_mouse_down("left", (_e, cx) => this.command("close", cx))
      .child(h_flex().id("settings").w(860).h(620).rounded(10).border(1).border_color(t.border).bg(t.background)
        .shadow_lg().overflow_hidden().text_size(13).items_start()
        .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
        .on_key_down((e, cx) => { if (e.keystroke === "escape") { this.command("close", cx); cx.stop_propagation(); } })
        .child(v_flex().w(200).flex_none().h_full().py(12).gap(2).bg(t.chrome).border_r(1).border_color(t.border)
          .child(div().px(16).pb(10).font_semibold().text_color(t.strong ?? t.foreground).child("Settings"))
          .children(SECTIONS.map(([id, label, icon]) => h_flex().id(`sec-${id}`).h(30).mx(8).px(8).gap(9).rounded(8)
            .items_center().cursor_pointer().border_l(3).border_color(id === at ? t.accent : t.chrome)
            .when(id === at, (e) => e.bg(t.selected)).hover((e) => e.bg(t.hover)).on_click((_e, cx) => this.go(id, cx))
            .child(svg(`icons/${icon}.svg`).size(14).flex_none().text_color(id === at ? t.accent : t.muted))
            .child(div().child(label)))))
        .child(v_flex().flex_1().min_w(0).h_full().px(20).py(16).gap(12)
          .child(h_flex().justify_between().items_center()
            .child(div().text_size(16).font_semibold().text_color(t.blue ?? t.accent).child(SECTIONS.find(([id]) => id === at)?.[1] ?? ""))
            .child(div().id("settings-close").px(8).rounded(6).cursor_pointer().hover((e) => e.bg(t.hover))
              .on_click((_e, cx) => this.command("close", cx)).child("× esc")))
          .child(body)));
  }
}
