// Settings (PRD 2026-11-03-app-settings), run in the shipped view. Usage: node --experimental-vm-modules app_settings_view.mjs
import { calls, cx, load, ran, state, texts, toasts } from "./app_view_kit.mjs";

const Settings = await load("settings");
const v = Object.assign(Object.create(Settings.prototype), { log: [] });
const out = {};
v.command('open:{"section":"keys"}', cx);
out.opened = state.settings;
v.command("close", cx);
out.closed = state.settings ?? null;
out.names = ["global", "page:today", "detail", "rail"].map(Settings.ns.sectionName);
out.line = Settings.ns.reportLine({ t: Date.UTC(2026, 10, 3, 12, 0, 0), kind: "key", key: "e", action: null, page: "today", focus: "list" });
v.set("ui_font", "Beta Sans", "UI font: Beta Sans");
out.setting = calls.filter((c) => c.method === "setting");
out.toasts = toasts.splice(0);
// Service & updates while an update keeps the App read-only (PRD 2026-11-04-app-update-on-restart, FR-UPD-7).
state.status = { online: false, build: "aaaaaaa", service_build: "bbbbbbb" };
out.update = {};
for (const step of ["building", "ready", "failed"]) {
  state.update = step;
  texts.splice(0);
  v.service({});
  out.update[step] = texts.filter((t) => ["Building in the background", "Ready · opens when you restart",
    "Failed to build · see Diagnostics", "Update now", "Try again", "Restart App"].includes(t));
}
// Settings › Modules (PRD 2026-11-07-modules, FR-MOD-13): the rows, and one switch's write and undo.
state.modules = ["pipelines", "sequences"];
texts.splice(0);
v.modules({});
out.modules = texts.filter((t) => ["Pipelines", "Sequences", "Cases", "Issues", "On", "Off", "For this Service"].includes(t));
calls.splice(0);
toasts.splice(0);
v.turn("sequences", "Sequences", false, cx);
await new Promise((r) => setTimeout(r, 0));
out.turned = calls.filter((c) => c.method.startsWith("command."));
out.undo = state.undo;
out.turnToasts = toasts.splice(0);
console.log(JSON.stringify(out));
