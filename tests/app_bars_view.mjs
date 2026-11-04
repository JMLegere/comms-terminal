// App bars and Palette (PRD 2026-11-03-app-bars): the Breadcrumb, the bottom bar's parts, the Palette's commands,
// choices and the rail's crumb and jump, run in the shipped views with the host as doubles.
import { answers, calls, cx, load, ran, settle, state } from "./app_view_kit.mjs";

const out = {};
const { crumbs } = (await load("topbar")).ns;
const stack = [{ page: "today", detail: "task" }, { page: "person", title: "Evan Boerchers" }, { page: "case", title: null }];
out.deep = crumbs(stack, "case", null, {}, "Talent 1 Bench").map((l) => [l.label, l.at]);
out.todo = crumbs([{ page: "threads", view: "Todo" }], "threads", null, { Todo: 12 }, null).map((l) => [l.label, l.count ?? null, l.at]);
out.long = crumbs([{ page: "today" }, { page: "person", title: "A very long name of somebody here" },
  { page: "organization", title: "An organization with a long name too" }, { page: "case", title: "Hiring" }],
  "case", null, {}, null).map((l) => l.label);

const { problems, acting } = (await load("status")).ns;
out.fine = problems({ online: true }, { connections: [{ ok: true }] });
out.bad = problems({ online: false, problem: "Service unreachable", pending: 2 },
  { calendar_broken: true, connections: [{ ok: false, name: "Slack", writes: false, supported: true }] });
out.many = problems({ online: true }, { connections: [1, 2, 3].map((n) => ({ ok: false, name: `c${n}`, supported: true })) });
// An update keeps the App read-only (PRD 2026-11-04-app-update-on-restart, FR-UPD-7): the bar names the step.
const updating = { online: false, problem: "Update ready · restart Comms Terminal to load the service's version; read-only." };
out.updates = ["building", "ready", "failed"].map((step) => problems(updating, {}, step)[0]);
out.progress = [null, { elapsed: 30, percent: null, left: null }, { elapsed: 150, percent: 40, left: 210 },
  { elapsed: 400, percent: 99, left: 0 }].map((p) => problems(updating, {}, "building", p)[0][0]);
out.offlineNoUpdate = problems({ online: false, problem: "offline" }, {}, null)[0];
out.typing = acting("reply", null, "detail");
out.target = acting(null, { col: "list", label: "Thread · Brunch", keys: [["e", "done"], ["r", "reply"], ["s", "status"], ["i", "respond"], ["y", "copy"]] }, "list");
out.otherCol = acting(null, { col: "rail", label: "Rail · Todo", keys: [] }, "list");

const Overlay = await load("overlay");
const keys = { global: { "/": "overlay.palette", "1": "nav.today", z: "nav.undo", j: "nav.down" },
  "page:threads": { j: "threads.next", e: "thread.done", down: "threads.next", "shift-r": "thread.reply_all" } };
out.commands = Overlay.ns.paletteCommands(keys, "threads").map((c) => [c.id, c.label, c.key, c.group]);
out.today = Overlay.ns.paletteCommands({ "page:today": { e: "today.e", x: "today.x", j: "today.next" } }, "today",
  { col: "list", label: "Task · Evan Boerchers · Day 1 Email", keys: [["enter", "open"], ["e", "done · next"]] }).map((c) => [c.id, c.label, c.group]);

const o = Object.create(Overlay.prototype);
let typed = "boerchrs";
Object.assign(o, { at: 0, commands: [], input: { value: () => typed } });
answers.search = [{ kind: "Person", id: "p-evan", title: "Evan Boerchers", sub: "evan@x.ca", open: { page: "person", person: "p-evan", title: "Evan Boerchers" } },
  { kind: "Command", id: "thread.done", title: "Done", key: "e", command: "thread.done" }];
o.palette();
o.search(cx);
await settle();
out.asked = calls.filter((c) => c.method === "search").map((c) => c.params.text);
out.options = state.picker.options.map((p) => [p.label, p.hint]);
o.choose(state.picker.options[0], cx);
out.jump = [state.jump, ran.at(-1), state.picker];
o.palette();
await (o.search(cx), settle());
o.choose(state.picker.options[1], cx);
out.ranCommand = ran.at(-1);

const Nav = await load("nav");
const nav = Object.create(Nav.prototype);
Object.assign(nav, { summary: {}, shell: {} });
state.stack = [{ page: "today", detail: "task" }, { page: "person", person: "p-evan", title: "Evan Boerchers" }, { page: "case", case: "c1" }];
nav.command("crumb:0", cx);
out.crumbed = [state.stack.length, state.page, state.detail];
nav.jump({ page: "threads", view: "Snoozed", thread: "t-9" });
out.jumpedList = [state.stack.length, state.page, state.view, state.thread];
nav.jump({ page: "person", person: "p-evan", title: "Evan Boerchers" });
out.jumpedFull = [state.stack.length, state.page, state.person, state.crumb];

console.log(JSON.stringify(out));
