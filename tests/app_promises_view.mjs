// The App's Promises page and Today's promises pane (FR-PR-8, FR-PR-9), run in the shipped views with the kit's
// doubles. Usage: node --experimental-vm-modules app_promises_view.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  answers, calls, clicks, clipboard, cx, emitEvent, hovers, load, mountedTexts, opened, ran, settle, state, texts, toasts, virtualLists,
  releasedViews,
} from "./app_view_kit.mjs";

const out = {};
const promise = (id, extra = {}) => ({ id, what: `Promise ${id}`, due_at: "2026-11-20T21:00:00+00:00", slack: 90,
  risk: null, state: "OPEN", draft_ready: false, people: [{ id: "p1", display_name: "Anna Ciprick" }], links: [], ...extra });
answers["promises.page"] = { count: 2, captured: { at: "2026-11-18T14:00:00+00:00" }, sections: {
  overdue: [promise("a", { risk: "overdue", slack: -30, links: [{ kind: "thread", target: "thread-9" }] })],
  at_risk: [], today: [], this_week: [promise("b")], later: [], tentative: [promise("c", { state: "TENTATIVE" })] } };
answers["thread.view"] = { view: { thread: { f: { status: "TODO", status_detail: "NEW_REPLY" } } } };

const Promises = await load("promises");
out.rows = Promises.ns.promiseRows(answers["promises.page"]).map((r) => r.header ?? r.p.id);
out.slackTime = [Promises.ns.slackTimeText(-30), Promises.ns.slackTimeText(90), Promises.ns.slackTimeText(45), Promises.ns.slackTimeText(69289)];
state.page = "promises";
const page = Object.create(Promises.prototype);
page.load(cx);
await settle();
out.selected = state.promise;
out.side = state.side;
out.ranAfterLoad = [...ran];
page.render();
out.foot = texts.find((t) => t.startsWith("Captured up to")) ?? null;
page.open(answers["promises.page"].sections.overdue[0]);
await settle();
out.opened = { thread: state.thread, ran: ran.slice(-2) };

// Today: the promises pane comes first, and its row shows the Promise in the side pane.
const Today = await load("today");
const today = Object.create(Today.prototype);
today.data = { promises: [promise("a", { risk: "at_risk", section: "at_risk" })], threads: [{ id: "thread-1", who: "Ana", title: "Hello", detail: "NEW_REPLY" }] };
state.page = "today";
out.todayKinds = [...new Set(today.rows().filter((x) => x.r).map((x) => x.kind))];
out.todayHeader = Today.ns.promiseItems(today.data.promises).map((x) => x.promiseHeader?.key ?? x.row.id);
out.due = [Promises.ns.dueIn("2026-11-18T10:00:00Z", Date.parse("2026-11-18T12:00:00Z")), Promises.ns.dueIn("2026-11-20T12:00:00Z", Date.parse("2026-11-18T12:00:00Z"))];
today.at = today.rows().findIndex((x) => x.kind === "promises" && x.r);
today.select(cx);
out.todaySide = state.side;
out.todayDetail = state.detail;
out.calls = calls.map((c) => c.method);

// AS-PR-11–12: use the stock key bindings and real Today, Nav and Side command handlers. The kit
// does not host Rust's layout resolver; both Today item types use the installed today:promise → Side route.
const keys = JSON.parse(readFileSync(new URL("../app/defaults/keys.json", import.meta.url), "utf8"));
const Nav = await load("nav"), Side = await load("side");
const Threads = await load("threads"), Thread = await load("thread");
const nav = Object.assign(Object.create(Nav.prototype), { summary: {}, shell: {} });
const side = Object.create(Side.prototype);
const threads = Object.assign(Object.create(Threads.prototype), { items: [], scroll: { scroll_to_item() {} } });
const thread = Object.assign(Object.create(Thread.prototype), { bodies: {} });
const views = { today, promises: page, nav, side, threads, thread };
function resolvePrepFocus(route) {
  if (state.page !== "today" || state.detail !== "promise" || state.focus !== "detail" || state.side?.kind !== "prep_today") return;
  // Only the renderer publishes detail_view. The host command sends the resolved entry.
  dispatch(`${route}.focus_entry`);
}
function dispatch(command) {
  const dot = command.indexOf(".");
  const view = command.slice(0, dot), name = command.slice(dot + 1);
  if (command === "app.focus_prep") return resolvePrepFocus("side");
  views[view]?.command?.(name, cx);
}
async function pump(wait = settle) {
  await wait();
  while (ran.length) {
    const command = ran.shift();
    state.list_view = state.page === "today" || state.page === "promises" ? state.page : "threads";
    state.detail_view = state.page === "promises" || (state.page === "today" && state.detail === "promise") ? "side" : "thread";
    dispatch(command);
    await wait();
  }
}

const prepId = "tana:event:walkthrough";
const prep = promise(prepId, { kind: "prep", what: "Site walkthrough", section: "this_week", people: [{ display_name: "Prep" }] });
const linked = promise("a", { kind: "promise", section: "at_risk", links: [{ kind: "thread", target: "thread-9", label: "Pilot checklist" }] });
const rows = [linked, promise("b", { kind: "promise", section: "this_week" }), prep,
  promise("c", { kind: "promise", section: "tentative", state: "TENTATIVE", status: "tentative" })];
const labels = Object.fromEntries(rows.map((r) => [r.id, r.what]));
const segment = (text, link = null) => [[text, null, false, link]];
const readPromise = (id) => [
  segment(labels[id]), segment(id === "c" ? "Tentative" : "On track"), segment("due Fri 20 Nov 17:00"),
  segment("Owed to Anna Ciprick"), ...(id === "a" ? [segment("Pilot checklist", "thread:thread-9")] : []),
  segment("History"), segment(`Recorded from Origin ${id}`), segment("Read-only: tell an agent to change a Promise (ADR 0024) · enter opens its Thread"),
];
const prepMetadata = [
  segment(labels[prepId]), segment("Not prepared"), segment("Prep 0m"), segment("Travel 12m"),
  segment("Leave by 16:48"), segment("Brief in Tana", "https://example.test/brief"),
  segment("Read-only: Prep lives on the Meeting in Tana (ADR 0025)"),
];
let prepFailed = false;
answers["today.side"] = ({ kind, id }) => id === prepId
  ? [...prepMetadata, ...(kind === "prep_today" && prepFailed ? [segment("Last known Prep metadata")] : [])]
  : readPromise(id);
answers["prep.brief"] = { status: "ready", text: "Baseline local Brief fixture" };
const canonical = { id: "thread-current", title: "Canonical linked Thread", detail: "WAITING_FOR_CUSTOMER" };
const other = { id: "thread-other", title: "Unrelated first Thread", detail: "WAITING_FOR_CUSTOMER" };
const threadView = (r) => ({ view: { thread: { f: { ...r, status: "SNOOZED", status_detail: r.detail } }, messages: [] } });
answers["threads.queue"] = ({ view }) => view === "Snoozed" ? [other, canonical] : [];
const answerThread = ({ thread_id }) => {
  if (thread_id === "thread-9" || thread_id === canonical.id) return threadView(canonical);
  if (thread_id === other.id) return threadView(other);
  throw new Error(`Unexpected Thread: ${thread_id}`);
};
answers["thread.view"] = answerThread;
const snapshot = () => {
  texts.length = clicks.length = 0;
  side.render();
  return { page: state.page, focus: state.focus, target: state.target, route: state.detail_view,
    row: today.rows()[today.at]?.r?.id ?? null, thread: state.thread ?? null, lines: [...texts] };
};
out.activation = {};
for (const [kind, id] of Object.entries({ linked: "a", unlinked: "b", Tentative: "c", prep: prepId })) {
  Object.assign(state, { page: "today", focus: "list", detail: null, detail_view: "thread", list_view: "today", stack: [{ page: "today" }], status: { online: true } });
  today.data = { ...today.data, promises: rows };
  today.at = today.rows().findIndex((row) => row.r?.id === id);
  ran.length = 0;
  const start = calls.length;
  today.select(cx);
  await pump();
  const preview = snapshot();
  dispatch(keys["page:today"].enter);
  await pump();
  const detail = snapshot();
  const links = clicks.length;
  const activationCalls = calls.slice(start);
  dispatch(keys.global.escape);
  await pump();
  out.activation[kind] = { preview, detail, back: snapshot(), calls: activationCalls, links };
}

// The standalone selection survives Today, but Side is now showing another item and must reselect it.
const standaloneStart = calls.length;
dispatch("nav.promises");
await pump();
out.standaloneReturn = { ...snapshot(), promise: state.promise, calls: calls.slice(standaloneStart) };
Object.assign(state, { page: "today", focus: "list", stack: [{ page: "today" }] });

// Start the explicit linked Thread action from an already-open Detail, independent of Enter's red result.
today.at = today.rows().findIndex((row) => row.r?.id === "a");
today.select(cx);
await pump();
dispatch(keys.global.right);
await pump();
snapshot();
const linkStart = calls.length;
clicks[0]?.({ modifiers: {} }, cx); // the fixture has exactly one clickable internal link
await pump();
out.threadLink = { page: state.page, thread: state.thread, view: state.view, calls: calls.slice(linkStart), opened,
  picked: threads.hi, lines: mountedTexts(() => thread.render()) };

async function delayedThreadClick(transition, outcome) {
  Object.assign(state, { page: "today", focus: "list", stack: [{ page: "today" }] });
  today.at = today.rows().findIndex((row) => row.r?.id === "a");
  today.select(cx);
  await pump();
  dispatch(keys.global.right);
  await pump();
  snapshot();
  let finish;
  answers["thread.view"] = () => new Promise((resolve, reject) => { finish = outcome === "success" ? resolve : reject; });
  const staleStart = calls.length, toastStart = toasts.length;
  clicks[0]?.({ modifiers: {} }, cx);
  if (transition === "refresh") {
    answers["today.page"] = today.data;
    dispatch("today.show");
  } else {
    dispatch(keys.global.escape);
    await pump();
    dispatch(keys["page:today"][transition === "reenter" ? "enter" : "down"]);
  }
  await pump();
  const beforeResolve = snapshot();
  answers["thread.view"] = answerThread;
  finish?.(outcome === "success" ? threadView(canonical) : new Error("Obsolete Thread lookup"));
  await new Promise(setImmediate);
  await pump();
  return { ...snapshot(), view: state.view, beforeResolve, calls: calls.slice(staleStart), toasts: toasts.slice(toastStart) };
}

out.obsoleteClicks = {};
out.reopenedClicks = {};
for (const outcome of ["success", "error"]) {
  out.obsoleteClicks[outcome] = await delayedThreadClick("move", outcome);
  out.reopenedClicks[outcome] = await delayedThreadClick("reenter", outcome);
}
out.clickAfterRefresh = await delayedThreadClick("refresh", "success");

state.page = "today"; state.focus = "list"; state.stack = [{ page: "today" }];
today.at = today.rows().findIndex((row) => row.r?.id === prepId);
today.select(cx);
await pump();
dispatch(keys.global.right);
await pump();
answers["today.page"] = { ...today.data, promises: rows.filter((r) => r.id !== "a") };
dispatch("today.show");
await pump();
dispatch(keys.global.escape);
await pump();
out.surviving = snapshot(); // Prep moved one list index after an earlier Promise disappeared.

dispatch(keys.global.right);
await pump();
// A pending local read of the removed Prep must not repaint the next item's Detail, even if it fails.
const answerSide = answers["today.side"];
let rejectObsolete;
answers["today.side"] = () => new Promise((_resolve, reject) => { rejectObsolete = reject; });
dispatch("side.show");
const obsolete = settle();
answers["today.side"] = answerSide;
answers["today.page"] = { ...today.data, promises: today.data.promises.filter((r) => r.id !== prepId) };
dispatch("today.show");
await pump();
rejectObsolete(new Error("Obsolete Prep failure"));
await obsolete;
out.removed = snapshot(); // Fallback is already focused before another Esc is pressed.
dispatch(keys.global.escape);
await pump();

state.status = { online: false };
today.data = { ...today.data, promises: rows };
today.at = today.rows().findIndex((row) => row.r?.id === prepId);
today.select(cx);
await pump();
dispatch(keys.global.right);
await pump();
out.offline = snapshot();
prepFailed = true;
dispatch("side.show");
await pump();
out.offlineAndFailed = snapshot();

// A local refresh can fail independently of the shell's online status.
state.status = { online: true };
prepFailed = false;
dispatch(keys.global.escape);
await pump();
dispatch(keys.global.right);
await pump();
dispatch("side.show");
await pump();
out.beforeRefreshFailure = snapshot();
answers["today.side"] = () => { throw new Error("Prep metadata refresh failed"); };
dispatch("side.show");
await pump();
out.onlineRefreshFailed = snapshot();
let finishPrepRetry;
answers["today.side"] = () => new Promise((resolve) => { finishPrepRetry = resolve; });
dispatch("side.show");
const prepRetry = settle();
out.onlineRetryPending = snapshot();
finishPrepRetry(prepMetadata.map((line) => line[0][0] === "Prep 0m" ? segment("Prep 5m") : line));
await prepRetry;
out.onlineRetryFresh = snapshot();
out.todayCalls = calls.slice(out.calls.length).map((c) => c.method);

async function traceBrief() {
  const a = prepId, b = "tana:event:handover";
  const tanaLink = "https://home.tana.inc/o/test/l/site";
  const literal = [
    '<img src="https://assets.example.test/brief.png"> & **Bring plans**',
    "https://notes.example.test/visit",
    "Confirm who brings the pilot checklist, who owns the next walkthrough, and how each crew returns feedback. ".repeat(4).trimEnd(),
    ...Array.from({ length: 32 }, (_, i) => `Prep note ${i + 1}: review the field checklist.`),
    "Final Brief line: note the next review.",
  ].join("\n");
  const result = {};
  const currentSide = views.side = Object.create(Side.prototype);
  const firstCall = calls.length;
  let attempts = [], now = 0, timers = [], notifications = 0;
  cx.notify = () => { notifications++; };
  cx.timer.after = (ms, fn) => timers.push({ at: now + ms, fn });
  // Drain completed work and stock commands, not the unresolved Brief or the long-running command/event loops.
  const flush = () => pump(async () => { void settle(); await new Promise(setImmediate); });
  const enter = async () => { dispatch(keys["page:today"].enter); await flush(); };
  const escape = async () => { dispatch(keys.global.escape); await flush(); };
  const status = async (online) => { emitEvent({ kind: "status", data: { online } }); await flush(); };
  const finish = async (attempt, status, text) => {
    attempt?.resolve(status === "ready" ? { status, text } : { status });
    await flush();
  };
  const late = async (attempt, outcome) => {
    if (outcome === "error") attempt?.reject(new Error("Raw obsolete Brief read failure"));
    else attempt?.resolve({ status: "ready", text: "OBSOLETE BRIEF" });
    await flush();
  };
  const advance = async (ms) => {
    now += ms;
    const due = timers.filter((timer) => timer.at <= now);
    timers = timers.filter((timer) => timer.at > now);
    for (const { fn } of due) fn(cx);
    await flush();
  };
  const capture = () => {
    clicks.length = 0;
    const lines = mountedTexts(() => currentSide.render());
    return { lines, links: clicks.length, focus: state.focus, route: state.detail_view, notifications,
      opened: [...opened], toasts: [...toasts], reads: attempts.map(({ params }) => params), released: releasedViews.length };
  };
  const retains = (text) => JSON.stringify([currentSide, state]).includes(JSON.stringify(text).slice(1, -1));
  const all = [...rows.filter((r) => r.id !== "c"), { ...prep, id: b, what: "Handover" }].map((r) =>
    r.kind === "prep" ? { ...r, links: [{ kind: "tana", target: tanaLink, label: "Brief" }] } : r);
  const items = { ...today.data, promises: all };
  Object.assign(state, { page: "today", focus: "list", stack: [{ page: "today" }], side: null, detail: null,
    list_view: "today", detail_view: "side", status: { online: true } });
  today.data = items;
  answers["today.page"] = items;
  let siteLink = tanaLink;
  answers["today.side"] = ({ id }) => id === a
    ? [...prepMetadata.filter((line) => line[0][0] !== "Brief in Tana"),
      ...(siteLink ? [segment("Brief in Tana", siteLink)] : [])]
    : id === b ? [segment("Handover"), segment("Not prepared"), segment("Prep 7m"), segment("Brief in Tana", tanaLink)]
      : readPromise(id);
  answers["prep.brief"] = (params) => new Promise((resolve, reject) => attempts.push({ params, resolve, reject }));
  const row = (id) => today.rows().find((x) => x.r?.id === id);
  const clickRow = (id) => {
    today.render();
    virtualLists.get("today-column").click(row(id).key, cx);
  };
  const openPrep = async (id = a) => { clickRow(id); await flush(); await enter(); };
  const pointerSelect = async (id) => { clickRow(id); await flush(); };
  currentSide.init({}, cx); // exercise its real status/disconnect subscription
  await pointerSelect("a");
  hovers.length = 0;
  const list = virtualLists.get("today-column");
  const index = Array.from({ length: list.length }, (_, i) => list.keyAt(i)).indexOf(row(a).key);
  list.render({ start: index, end: index + 1 });
  for (const hover of hovers) { hover(true, cx); hover(false, cx); }
  await flush();
  const hovered = capture();
  dispatch(keys["page:today"].down);
  dispatch(keys["page:today"].down);
  await flush();
  const highlighted = capture();
  await pointerSelect("a");
  await enter();
  const unrelated = capture();
  await escape();

  await openPrep();
  const pending = capture();
  dispatch("today.show"); // a refresh and a repeated focus entry are not a second open
  dispatch("side.focus_entry");
  await flush();
  const refreshed = capture();
  await finish(attempts.at(-1), "ready", literal);
  const stateHasBody = literal.split("\n").some((line) => JSON.stringify(state).includes(JSON.stringify(line).slice(1, -1)));
  const parentHasBody = mountedTexts(() => currentSide.render(), { includeNested: false }).some((text) => text.includes("Bring plans"));
  const rendered = capture();
  clicks[0]?.({ modifiers: { control: true } }, cx);
  const explicitlyOpened = [...opened];
  await escape();
  const exited = { ...capture(), retained: literal.split("\n").some(retains) };
  await enter();
  const reopened = capture();
  await finish(attempts.at(-1), "ready", "BRIEF AFTER REOPEN");
  result.activation = {
    hovered, highlighted, unrelated, pending, refreshed, rendered, explicitlyOpened, exited, reopened, stateHasBody, parentHasBody,
  };
  const beforePointerExit = capture();
  await pointerSelect(a); // a click on the same row leaves Detail too
  result.pointerExit = { ...capture(), before: beforePointerExit, retained: retains("BRIEF AFTER REOPEN") };

  result.outcomes = {};
  for (const outcome of ["missing", "unavailable", "timeout"]) {
    attempts = [];
    await openPrep(b); // unknown Travel and Leave by
    await finish(attempts.at(-1), outcome);
    result.outcomes[outcome] = capture();
    await escape();
  }

  result.disconnect = {};
  for (const kind of ["status", "disconnected"]) {
    attempts = [];
    await openPrep();
    const original = attempts.at(-1);
    const text = "BRIEF BEFORE OFFLINE";
    if (kind === "status") await finish(original, "ready", text);
    const beforeOffline = capture();
    if (kind === "status") {
      await status(false);
    } else {
      emitEvent({ kind }); // a Local-core socket logout/disconnect does not replace the last host status
      await flush();
    }
    const offline = { ...capture(), retained: retains(text) };
    await escape();
    await enter();
    const openOffline = capture();
    await status(true);
    const reconnected = capture();
    await escape();
    await enter();
    const newPending = capture();
    await finish(attempts.at(-1), "ready", "BRIEF AFTER RECONNECT");
    const current = capture();
    if (kind === "disconnected") await late(original, "success");
    result.disconnect[kind] = { beforeOffline, offline, openOffline, reconnected, newPending, current, afterLate: capture() };
    await escape();
  }

  attempts = [];
  await openPrep(a);
  await openPrep(b);
  await openPrep(a);
  await finish(attempts[2], "ready", "CURRENT BRIEF A");
  result.returnToA = { current: capture() };
  await late(attempts[0], "success");
  result.returnToA.afterA = capture();
  await late(attempts[1], "error");
  result.returnToA.afterB = capture();
  await escape();

  result.deadline = {};
  for (const outcome of ["success", "error"]) {
    attempts = [];
    await openPrep();
    const original = attempts.at(-1);
    await advance(7999);
    const before = capture();
    await advance(1);
    const timedOut = capture();
    await escape();
    await enter();
    await finish(attempts.at(-1), "ready", "CURRENT BRIEF AFTER TIMEOUT");
    const current = capture();
    await late(original, outcome);
    const afterLate = capture();
    await escape();
    await enter();
    const leaving = attempts.at(-1);
    await escape();
    const exited = capture();
    await late(leaving, outcome);
    result.deadline[outcome] = { before, timedOut, current, afterLate, exited, afterExitReply: capture(),
      retained: retains("CURRENT BRIEF AFTER TIMEOUT") || retains("OBSOLETE BRIEF") };
  }

  attempts = [];
  await openPrep(a);
  await finish(attempts.at(-1), "ready", "BRIEF OF REMOVED PREP");
  const beforeRemoval = capture();
  answers["today.page"] = { ...items, promises: all.filter((r) => r.id !== a) };
  dispatch("today.show");
  await flush();
  result.removed = { ...capture(), before: beforeRemoval, retained: retains("BRIEF OF REMOVED PREP") };
  result.calls = calls.slice(firstCall);
  // These cases intentionally run outside the original trace's call allowlist check.
  answers["today.page"] = items;
  dispatch("today.show");
  await flush();

  result.queuedStatus = {};
  for (const phase of ["pending", "ready"]) {
    attempts = [];
    await openPrep();
    const original = attempts.at(-1), text = "BRIEF BEFORE QUEUED STATUS";
    if (phase === "ready") await finish(original, "ready", text);
    const before = capture();
    // Rust updates the global host status before queuing each event for the Side view.
    emitEvent({ kind: "status", data: { online: false } });
    emitEvent({ kind: "status", data: { online: true } });
    await flush();
    const after = { ...capture(), retained: retains(text) };
    if (phase === "pending") await finish(original, "ready", text);
    const afterLate = { ...capture(), retained: retains(text) };
    await escape();
    await enter();
    await finish(attempts.at(-1), "ready", "BRIEF AFTER QUEUED STATUS REOPEN");
    result.queuedStatus[phase] = { before, after, afterLate, reopened: capture() };
    await escape();
  }

  // Pointer selection and Enter can both land before the next host layout publishes detail_view.
  result.earlyEnter = {};
  for (const route of ["side", "custom"]) {
    attempts = [];
    today.at = today.rows().findIndex((row) => row.kind === "threads" && row.r);
    today.select(cx);
    await flush();
    clickRow(a);
    dispatch(keys["page:today"].enter);
    let focusPrep = false;
    while (ran.length) {
      const command = ran.shift();
      if (command === "app.focus_prep") focusPrep = true; // model delivery after the navigation commands
      else dispatch(command);
    }
    await flush();
    const before = capture();
    if (focusPrep) resolvePrepFocus(route);
    await flush();
    const afterCommand = capture(); // still the previous renderer-published route
    state.detail_view = route;
    if (route === "side") dispatch("side.focus_entry"); // render-time transition hook
    await flush();
    await finish(attempts.at(-1), "ready", "BRIEF AFTER EARLY ENTER");
    const current = capture();
    if (route === "side") {
      state.detail_view = "custom";
      state.today_detail_epoch = (state.today_detail_epoch ?? 0) + 1;
      dispatch("side.focus_exit");
      await flush();
    }
    result.earlyEnter[route] = { before, afterCommand, current, afterRouteExit: capture() };
    await escape();
  }

  // The previous Paused row really uses Side. A custom Prep detail does not.
  attempts = [];
  today.data = { ...items, paused: [{ id: "pause-1", person_id: "p1", person: "Anna Ciprick" }] };
  today.at = today.rows().findIndex((row) => row.kind === "paused");
  today.select(cx);
  await flush();
  state.detail_view = "side"; // host-resolved route for the currently drawn Paused row
  const fromPaused = { detail: state.detail, route: state.detail_view };
  clickRow(a);
  dispatch(keys["page:today"].enter);
  const commands = [];
  while (ran.length) {
    const command = ran.shift();
    commands.push(command);
    if (command === "app.focus_prep") resolvePrepFocus("custom");
    else dispatch(command); // layout has not yet run
  }
  await flush();
  const beforeResolution = capture();
  state.detail_view = "custom"; // renderer publishes after the command ran
  result.pausedToCustomPrep = { fromPaused, commands, beforeResolution, current: capture() };
  await escape();

  result.linkChanges = {};
  for (const change of ["replace", "remove"]) {
    result.linkChanges[change] = {};
    for (const phase of ["pending", "ready"]) {
      attempts = [];
      siteLink = tanaLink;
      answers["today.page"] = items;
      dispatch("today.show");
      await flush();
      await openPrep();
      const original = attempts.at(-1), text = "BRIEF FOR ORIGINAL PIN";
      if (phase === "ready") await finish(original, "ready", text);
      const before = capture();
      siteLink = change === "replace" ? "https://home.tana.inc/o/test/l/new-site" : null;
      answers["today.page"] = { ...items, promises: all.map((r) => r.id === a
        ? { ...r, links: siteLink ? [{ kind: "tana", target: siteLink, label: "Brief" }] : [] } : r) };
      dispatch("today.show");
      await flush();
      const changed = { ...capture(), retained: retains(text) };
      if (phase === "pending") await finish(original, "ready", text);
      const afterLate = { ...capture(), retained: retains(text) };
      dispatch("side.focus_entry"); // a queued duplicate focus command is not an explicit reopen
      await flush();
      const sameOpen = capture();
      await escape();
      await enter();
      await finish(attempts.at(-1), change === "replace" ? "ready" : "missing", "BRIEF FOR NEW PIN");
      result.linkChanges[change][phase] = { before, changed, afterLate, sameOpen, reopened: capture() };
      await escape();
    }
  }
  return result;
}

if (process.argv[2] === "brief") out.brief = await traceBrief();

// Press y via the shipped page bindings. Only the host and clipboard are doubled;
// both list selections, Today routing, and Promise command are production code.
answers["today.side"] = answerSide; // end the deferred Prep metadata fixtures before selecting new rows
const pressCopy = () => {
  const command = keys[`page:${state.page}`]?.y;
  if (command) dispatch(command);
};
const handoff = "# Synthetic Promise handoff\n\n- Check the original evidence.\n";
answers["promise.markdown"] = handoff;
async function expectCopy(id) {
  const start = calls.length, copied = clipboard.length;
  pressCopy();
  await pump();
  const requested = calls.slice(start);
  if (id) {
    assert.deepEqual(requested.map((c) => c.method), ["promise.markdown"], `y on ${state.page} reads only the selected Promise`);
    assert.deepEqual(Object.entries(requested[0].params), [["promise_id", id]]);
    assert.deepEqual(clipboard.slice(copied), [handoff]);
    assert.equal(toasts.at(-1), "Copied the Promise for an agent.");
  } else {
    assert.deepEqual(requested, [], `y on ${state.page} without a Promise must not call the core`);
    assert.equal(clipboard.length, copied);
  }
}

delete views.promises; // custom Today layout omits the standalone Promises view
Object.assign(state, { page: "today", focus: "list", promise: "a" }); // a different standalone selection
today.data = { promises: rows };
today.at = today.rows().findIndex(({ r }) => r?.id === "b");
today.select(cx);
await pump();
await expectCopy("b");
assert.ok(state.target.keys.some(([key, label]) => key === "y" && label === "copy"));
dispatch(keys.global.right);
await pump();
assert.equal(state.focus, "detail");
await expectCopy("b");
assert.ok(state.target.keys.some(([key]) => key === "y"));
dispatch(keys.global.escape);
await pump();
today.at = today.rows().findIndex(({ r }) => r?.id === prepId);
today.select(cx);
await pump();
await expectCopy(null);
assert.ok(state.target.keys.every(([key]) => key !== "y"));
today.at = -1;
today.render();
const header = today.column().find((row) => row.promiseHeader);
virtualLists.get("today-column").click(header.key, cx);
await expectCopy(null);
today.data = {};
today.at = 0;
await expectCopy(null);

views.promises = page;
dispatch("nav.promises");
await pump();
dispatch(keys["page:promises"].j);
await pump();
await expectCopy("b");
assert.ok(state.target.keys.some(([key, label]) => key === "y" && label === "copy"));
answers["promise.markdown"] = () => { throw new Error("Synthetic Promise read failed"); };
const copied = clipboard.length;
pressCopy();
await pump();
assert.equal(clipboard.length, copied);
assert.equal(toasts.at(-1), "Error: Synthetic Promise read failed");

// Prep can also appear in the standalone list. Neither it nor an empty list can
// copy, even if a Promise was selected on the other page.
answers["promises.page"] = { sections: { this_week: [prep] } };
dispatch("promises.show");
await pump();
await expectCopy(null);
assert.ok(state.target.keys.every(([key]) => key !== "y"));
answers["promises.page"] = { sections: {} };
dispatch("promises.show");
await pump();
await expectCopy(null);
assert.equal(state.target, null);

state.page = "today";
today.data = {
  threads: [{ id: "synthetic-thread", detail: "NEW_REPLY" }],
  drafts: [{ id: "synthetic-draft" }],
  tasks: [{ id: "synthetic-task", group: "today" }],
  issues: [{ id: "synthetic-issue" }],
};
for (const [kind, command] of Object.entries({ threads: "thread.copy", drafts: "drafts.copy", tasks: "tasks.copy", issues: "issue.copy" })) {
  today.at = today.rows().findIndex((r) => r.kind === kind && r.r);
  today.select(cx);
  ran.length = 0;
  pressCopy();
  assert.deepEqual(ran.splice(0), [command], `Today retains y for ${kind}`);
}
console.log(JSON.stringify(out));
