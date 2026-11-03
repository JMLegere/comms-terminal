// Runs shipped App views with GPUI widgets and the host API as doubles; never a Local core or provider.
// Shared by the view scenarios (app_today_view.mjs, app_shell_view.mjs, app_phase1_view.mjs).
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

export const calls = [], tasks = [], toasts = [], ran = [], answers = {}, state = { page: "today" };
function element() {
  const el = new Proxy({}, { get(_target, name) {
    if (name === "when") return (condition, render) => condition ? render(el) : el;
    return () => el;
  } });
  return el;
}
const widget = { new: element };
// An input's state: holds its text and its handlers, so a test can type, blur and read back.
export function input() {
  const handlers = {};
  let text = "";
  const self = new Proxy({}, { get(_t, name) {
    if (name === "value") return () => text;
    if (name === "set_value") return (v) => { text = v; };
    if (name === "on") return (event, h) => { (handlers[event] ??= []).push(h); return true; };
    if (name === "fire") return (event, cx) => (handlers[event] ?? []).forEach((h) => h({}, cx));
    return () => {};
  } });
  return self;
}
const state_widget = { new: input };
const comms = {
  async call(method, params) { calls.push({ method, params }); return answers[method] ?? null; },
  nextEvent: () => new Promise(() => {}), nextCommand: () => new Promise(() => {}), theme: () => ({}),
  shared: (key) => state[key], setShared: (key, value) => { state[key] = value; }, run(c) { ran.push(c); }, open() {},
  focusMain() {}, mark() {}, toast(m) { toasts.push(m); }, keys: () => ({}), status: () => ({}),
};
const modules = { comms, "gpui-kit": { View: class {}, div: element, svg: element } };
export async function load(name) {
  const context = vm.createContext({ console });
  const src = fs.readFileSync(new URL(`../app/defaults/views/${name}/main.js`, import.meta.url), "utf8");
  const view = new vm.SourceTextModule(src, { context });
  await view.link((spec) => {
    let exports = modules[spec];
    if (!exports && spec.startsWith("gpui-")) {  // any other widget module: doubles for the names this view imports
      const names = [...src.matchAll(new RegExp(`import \\{([^}]*)\\} from "${spec}"`, "g"))]
        .flatMap((m) => m[1].split(",")).map((n) => n.trim().split(" as ")[0]).filter(Boolean);
      exports = Object.fromEntries(names.map((n) => [n, /State$/.test(n) ? state_widget : /Handle$/.test(n) ? widget : element]));
    }
    assert(exports, `Unexpected import: ${spec}`);
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await view.evaluate();
  const View = view.namespace.default;
  View.ns = view.namespace; // the module's other exports
  return View;
}
export const cx = { notify() {}, stop_propagation() {}, spawn(fn) { const t = fn(cx); tasks.push(t); return t; },
  timer: { after() {} } }; // timers never fire: a test drives the view itself
export const settle = () => Promise.all(tasks.splice(0));
