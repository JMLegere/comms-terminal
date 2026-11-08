// Runs shipped App views with GPUI widgets and the host API as doubles; never a Local core or provider.
// Shared by the view scenarios (app_today_view.mjs, app_shell_view.mjs, app_phase1_view.mjs).
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

export const calls = [], tasks = [], toasts = [], ran = [], answers = {}, state = { page: "today" };
export const keyHandlers = []; // every on_key_down handler a render attached, newest last
export const texts = [];       // every string a render put in an element
export const focused = [];     // every input given the keyboard, in order
function element() {
  const el = new Proxy({}, { get(_target, name) {
    if (name === "when") return (condition, render) => condition ? render(el) : el;
    if (name === "on_key_down") return (handler) => { keyHandlers.push(handler); return el; };
    if (name === "child") return (c) => { if (typeof c === "string") texts.push(c); return el; };
    return () => el;
  } });
  return el;
}
const widget = { new: element };
// An input's state: holds its text and its handlers, so a test can type, blur and read back.
export function input(options = {}) {
  const handlers = {};
  let text = "";
  const self = new Proxy({}, { get(_t, name) {
    if (name === "value") return () => text;
    if (name === "placeholder") return options.placeholder; // what the box shows while empty
    if (name === "set_value") return (v) => { text = v; };
    if (name === "focus") return () => focused.push(self);
    if (name === "on") return (event, h) => { (handlers[event] ??= []).push(h); return true; };
    if (name === "fire") return (event, cx) => (handlers[event] ?? []).forEach((h) => h({}, cx));
    return () => {};
  } });
  return self;
}
const state_widget = { new: input };
const comms = {
  async call(method, params) {  // an answer may be a function of the params (the Service's state changing)
    calls.push({ method, params });
    const a = answers[method];
    return (typeof a === "function" ? a(params) : a) ?? null;
  },
  nextEvent: () => new Promise(() => {}), nextCommand: () => new Promise(() => {}), theme: () => ({}),
  shared: (key) => state[key], setShared: (key, value) => { state[key] = value; }, run(c) { ran.push(c); }, open() {},
  focusMain() {}, mark() {}, toast(m) { toasts.push(m); }, keys: () => ({}), moduleOf: () => null, status: () => state.status ?? {},
  fonts: () => ["Alpha Mono", "Beta Sans"], settings: () => state.settingsFile ?? {}, logTail: () => state.log ?? [],
  setting(key, value) { calls.push({ method: "setting", params: { key, value } }); return true; },
};
const modules = { comms, "gpui-kit": { View: class {}, div: element, svg: element } };
// A relative import is a file in the view's own directory; app/src/workspace.rs ships a shared file such as
// views/composer.js into each directory that imports it, so one missing beside the view is read from views/.
function file(name, spec) {
  const own = new URL(`../app/defaults/views/${name}/${spec}`, import.meta.url);
  return fs.existsSync(own) ? own : new URL(`../app/defaults/views/${spec}`, import.meta.url);
}
export async function load(name) {
  const context = vm.createContext({ console });
  const src = fs.readFileSync(new URL(`../app/defaults/views/${name}/main.js`, import.meta.url), "utf8");
  const view = new vm.SourceTextModule(src, { context });
  const deps = {};
  const link = (spec, from) => {
    if (spec.startsWith("./")) {
      if (deps[spec]) return deps[spec];
      const source = fs.readFileSync(file(name, spec), "utf8");
      return (deps[spec] = new vm.SourceTextModule(source, { context, identifier: spec }));
    }
    const text = from.identifier?.startsWith("./") ? fs.readFileSync(file(name, from.identifier), "utf8") : src;
    let exports = modules[spec];
    if (!exports && spec.startsWith("gpui-")) {  // any other widget module: doubles for the names this view imports
      const names = [...text.matchAll(new RegExp(`import \\{([^}]*)\\} from "${spec}"`, "g"))]
        .flatMap((m) => m[1].split(",")).map((n) => n.trim().split(" as ")[0]).filter(Boolean);
      // A component is called (MessageBody(…)) or built (Input.new(state)); both give an element.
      const component = Object.assign((..._a) => element(), { new: element });
      exports = Object.fromEntries(names.map((n) => [n, /State$/.test(n) ? state_widget : /Handle$/.test(n) ? widget
        : /^[A-Z]/.test(n) ? component : element]));
    }
    assert(exports, `Unexpected import: ${spec}`);
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  };
  await view.link(link);
  await view.evaluate();
  const View = view.namespace.default;
  View.ns = view.namespace; // the module's other exports
  View.deps = Object.fromEntries(Object.entries(deps).map(([k, m]) => [k, m.namespace])); // its own files' exports
  return View;
}
export const cx = { notify() {}, stop_propagation() {}, spawn(fn) { const t = fn(cx); tasks.push(t); return t; },
  timer: { after() {} } }; // timers never fire: a test drives the view itself
export const settle = () => Promise.all(tasks.splice(0));
