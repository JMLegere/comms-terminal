// Runs the shipped view, never a Local core or provider. Only GPUI widgets and the host API are doubles.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const calls = [], tasks = [], handlers = [], state = {}, toasts = [];
let saved;
function element() {
  const el = new Proxy({}, { get(_target, name) {
    if (name === "on_key_down") return (handler) => { handlers.push(handler); return el; };
    if (name === "when") return (condition, render) => condition ? render(el) : el;
    return () => el;
  } });
  return el;
}
const widget = { new: element };
const context = vm.createContext({});
const modules = {
  "gpui-kit": { View: class {}, div: element, svg: element },
  "gpui-base": { v_flex: element, h_flex: element, InputState: widget, Input: widget,
    TextareaState: widget, Textarea: widget },
  comms: {
    async call(method, params) {
      calls.push({ method, params });
      if (method === "command.inbox.save_draft") { saved = params.values; return { ...saved, revision: 2 }; }
      if (method === "send.later") return null;
      if (method === "send.reply" || method === "send.draft") return null;
      throw Error(`Unexpected Local API call: ${method}`);
    },
    nextEvent() {}, nextCommand() {}, theme: () => ({}), shared: (key) => state[key],
    setShared: (key, value) => { state[key] = value; }, run() {}, open() {}, focusMain() {},
    toast: (message) => toasts.push(message),
  },
};
const view = new vm.SourceTextModule(fs.readFileSync(new URL("../app/defaults/views/draft/main.js", import.meta.url), "utf8"), { context });
await view.link((name) => {
  const exports = modules[name];
  assert(exports, `Unexpected import: ${name}`);
  return new vm.SyntheticModule(Object.keys(exports), function () {
    for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
  }, { context });
});
await view.evaluate();
const editor = new view.namespace.default();
editor.draft = { id: "draft-1", revision: 1, thread_id: process.argv[2] === "Reply Draft" ? "thread-1" : null,
  connection_id: "connection-1", kind: "email", sender: "me@example.test", to: "old@example.test", cc: "",
  subject: "Test", body: "Old text", sending_at: null, send_at: null, needs_edit: 0 };
const field = (value) => ({ value: () => value, focus() {} });
editor.to = field(process.argv[3] === "To" ? "  " : " ana@example.test, ben@example.test ");
editor.cc = field(" copy@example.test ");
editor.subject = field("Edited subject");
editor.body = field(process.argv[3] === "Body" ? "  " : "Edited text");
editor.unsaved = true;
const cx = { notify() {}, stop_propagation() {}, spawn(fn) { const task = fn(cx); tasks.push(task); return task; } };
editor.render();
assert.equal(handlers.length, 1);
handlers[0]({ keystroke: "ctrl-enter" }, cx);
await Promise.all(tasks.splice(0));
assert(state.picker.options.some((option) => option.value === "send"));
editor.command(`send-picked:${JSON.stringify({ value: "send" })}`, cx);
await Promise.all(tasks.splice(0));
console.log(JSON.stringify({ calls, saved, toasts }));
