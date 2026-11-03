// G1 shell: the page stack (AS-CONF-5) and the one undo (AS-CONF-6), run in the shipped rail and Thread views.
// Usage: node --experimental-vm-modules app_shell_view.mjs stack|undo
import { answers, calls, cx, load, settle, state, toasts } from "./app_view_kit.mjs";

const Nav = await load("nav"), Thread = await load("thread");
const nav = Object.create(Nav.prototype), thread = Object.create(Thread.prototype);
Object.assign(nav, { summary: {}, shell: {} });
Object.assign(thread, { bodies: {}, view: { thread: { f: { status_detail: "NEW_REPLY" } }, person: { id: "person-1" }, messages: [] } });
const out = [];
const look = () => ({ page: state.page, view: state.view ?? null, thread: state.thread ?? null, person: state.person ?? null,
  depth: (state.stack ?? []).length, focus: state.focus });

if (process.argv[2] === "stack") {
  nav.go("todo");
  state.thread = "thread-1";
  out.push(look());
  thread.command("person", cx);                       // p: the Person's full page on top
  nav.command("push", cx);                            // run("nav.push") reaches the rail
  out.push(look());
  nav.command("back", cx);                            // esc
  out.push(look());
  nav.command("back", cx);                            // nothing below the rail page
  out.push(look());
  nav.go("people");                                   // a rail pick resets the stack
  out.push(look());
} else {
  state.thread = "thread-1";
  answers["thread.snapshot"] = { thread: "thread-1", status: "NEW_REPLY" };
  thread.command("done", cx);
  await settle();
  nav.command("undo", cx);
  await settle();
  nav.command("undo", cx);
  await settle();
}
console.log(JSON.stringify({ looks: out, calls: calls.map((c) => c.method), toasts }));
