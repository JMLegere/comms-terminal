// Phase 6: Sequences, Organizations and People as the Console's (AS-CONF-17..19), run in the shipped views.
// Usage: node --experimental-vm-modules app_phase6_view.mjs archive|stage|enrollments
import { answers, calls, cx, load, ran, settle, state, tasks, toasts } from "./app_view_kit.mjs";

const kase = process.argv[2];
const out = {};
const make = (View, fields) => Object.assign(Object.create(View.prototype), fields);

if (kase === "archive") { // D121
  const Sequences = await load("sequences");
  const v = make(Sequences, { rows: [{ id: "q1", name: "Warm intro", active: 2, paused: 1 }], hi: "q1", showArchived: false });
  v.command("archive", cx);
  out.picker = { title: state.picker.title, options: state.picker.options.map((o) => o.label), body: state.picker.body.map((l) => l.text) };
  answers["command.sequences.archive_sequence"] = [{ enrollment: "e1" }];
  v.command(`archive-picked:${JSON.stringify({ value: "cancel" })}`, cx);
  await settle();
  out.write = calls.find((c) => c.method === "command.sequences.archive_sequence").params;
  out.undo = state.undo ?? null;
} else if (kase === "stage") { // D133
  const Organization = await load("organization");
  state.page = "organizations";
  const stages = { held: ["REVENUE"], current: { REVENUE: "Commit" }, all: { REVENUE: ["Selection", "Commit", "Onboarding"] },
    names: { REVENUE: "Revenue", FUNDRAISING: "Fundraising", TALENT: "Talent" } };
  const v = make(Organization, { page: { domain: "acme.test", org: { id: "o1", name: "Acme", domain: "acme.test" }, stages } });
  v.command("stage", cx);
  out.picker = { title: state.picker.title, options: state.picker.options.map((o) => o.label) };
  answers["command.pipeline.set_stage"] = 42;
  v.command(`stage-picked:${JSON.stringify({ value: "Onboarding" })}`, cx);
  await settle();
  out.write = calls.find((c) => c.method === "command.pipeline.set_stage").params;
  out.toast = toasts.at(-1);
  out.undo = state.undo ?? null;
} else if (kase === "person-stage") { // ADR 0016: a Person's Fundraising or Talent Stage
  const Person = await load("person");
  state.page = "people";
  const stages = { held: [], current: {}, all: { FUNDRAISING: ["Research", "Meeting"], TALENT: ["Explore"] },
    names: { REVENUE: "Revenue", FUNDRAISING: "Fundraising", TALENT: "Talent" } };
  const v = make(Person, { page: { person: { id: "p1", name: "Ana", identities: [] }, enrollments: [], stages } });
  v.command("stage", cx);
  out.first = { title: state.picker.title, options: state.picker.options.map((o) => o.label) };
  v.command(`pipeline-picked:${JSON.stringify({ value: "FUNDRAISING" })}`, cx);
  out.picker = { title: state.picker.title, options: state.picker.options.map((o) => o.label) };
  answers["command.pipeline.set_stage"] = 7;
  v.command(`stage-picked:${JSON.stringify({ value: "Meeting" })}`, cx);
  await settle();
  out.write = calls.find((c) => c.method === "command.pipeline.set_stage").params;
  out.toast = toasts.at(-1);
  out.undo = state.undo ?? null;
} else if (kase === "enrollments") { // D142
  const Person = await load("person");
  answers["enroll.choices"] = { reasons: { GOAL_MET: "Goal met", CANCELED: "Canceled", UNSUBSCRIBED: "Unsubscribed" }, finishing: ["GOAL_MET"],
    finish_reasons: [["GOAL_MET", "Goal met · what it was for happened; its On completion runs"], ["CANCELED", "Canceled · you stopped it"],
      ["UNSUBSCRIBED", "Unsubscribed · they asked to stop"]] };
  const one = make(Person, { page: { person: { id: "p1", name: "Ana", identities: [] }, enrollments: [{ id: "e1", sequence: "Warm intro", state: "PAUSED" }] } });
  one.command("resume", cx);
  await settle();
  out.resume = { picker: state.picker ?? null, write: calls.find((c) => c.method === "command.sequences.resume")?.params, toast: toasts.at(-1) };
  const two = make(Person, { page: { person: { id: "p1", name: "Ana", identities: [] }, enrollments: [
    { id: "e1", sequence: "Warm intro", state: "ACTIVE", reason: null }, { id: "e2", sequence: "Bench", state: "FINISHING", reason: "GOAL_MET" }] } });
  two.command("finish", cx);
  out.which = { title: state.picker.title, options: state.picker.options.map((o) => o.label) };
  two.command(`which:${JSON.stringify({ value: "e2" })}`, cx);
  await settle();
  out.reasons = { title: state.picker.title, options: state.picker.options.map((o) => o.value) };
  answers["command.sequences.finish"] = [{ undo: 1 }];
  two.command(`finish-why:${JSON.stringify({ value: "CANCELED" })}`, cx);
  await settle();
  out.finished = { toast: toasts.at(-1), undo: state.undo ?? null };
}
tasks.length = 0;
console.log(JSON.stringify(out));
