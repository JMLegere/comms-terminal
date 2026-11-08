// Phase 1 safety (AS-CONF-2, 3, 4) and delete-then-undo (FR-CONF-5), run in the shipped views.
// Usage: node --experimental-vm-modules app_phase1_view.mjs locked|step|merge|delete
import { answers, calls, cx, load, settle, state, tasks, toasts } from "./app_view_kit.mjs";

const out = {};
const methods = () => calls.map((c) => c.method);
const start = (v) => { v.init({}, cx); tasks.length = 0; return v; }; // init's event loops never end

if (process.argv[2] === "locked") {
  const Draft = await load("draft");
  const d = new Draft();
  start(d);
  d.draft = { id: "draft-1", revision: 3, kind: "email", to: "ana@example.test", cc: "", subject: "Hi",
    body: "Locked text", sending_at: "2026-11-02T10:00:00", send_at: null, needs_edit: 0 };
  d.composer.showDraft({ draft: d.draft, staged: [] });   // the Composer on it, as draft.show loads it
  d.composer.body.set_value("Typed while locked");
  d.composer.body.fire("change", cx);                     // a keystroke in the body
  d.command("save", cx);                                  // ctrl-s
  d.command("close", cx);                                 // esc
  await settle();
  d.render();
  out.body = d.composer.body.value();
  out.saves = methods().filter((m) => m === "command.inbox.save_draft").length;
} else if (process.argv[2] === "step") { // the Step editor in the Sequences page's right pane
  const Sequence = await load("sequence");
  const s = new Sequence();
  start(s);
  s.command("edit", cx);                                  // no Step open: nothing to edit
  s.ed = { step: { id: "step-1", kind: "email" }, fields: [["Gap", "2", ""], ["Subject", "Old", ""]], said: null };
  s.inputs.Gap.set_value("2"); s.inputs.Subject.set_value("New subject"); s.body.set_value("Brief");
  answers["step.values"] = { values: { subject: "New subject", body: "Brief", gap_days: 2 }, same: false };
  s.inputs.Subject.fire("blur", cx);                      // leaving the field saves
  await settle();
  out.blur = calls.filter((c) => c.method === "command.sequences.save_step").map((c) => c.params);
  answers["step.values"] = { error: "Gap is a whole number of business days, 0 or more." };
  s.command("close", cx);                                 // esc with a bad gap: refused, editor stays
  await settle();
  out.refused = { open: Boolean(s.ed), message: s.ed.said };
  answers["step.values"] = { values: { subject: "New subject", body: "Brief", gap_days: 3 }, same: false };
  s.command("close", cx);                                 // esc: saves, then back to the Steps
  await settle();
  out.escape = calls.filter((c) => c.method === "command.sequences.save_step").map((c) => c.params).slice(1);
  out.closed = (await import("./app_view_kit.mjs")).ran.includes("sequences.show");
} else if (process.argv[2] === "merge") {
  const People = await load("people");
  const p = new People();
  start(p);
  p.rows = [{ id: "p-ana", name: "Ana Ng", address: "ana@x.test" }, { id: "p-ben", name: "Ben Ode", address: "ben@x.test" }];
  state.person = "p-ana";
  p.command("merge", cx);
  out.pick = state.picker.title;
  answers["people.merge_preview"] = { name: "Ben Ode", identities: [{ kind: "email", value: "ana@x.test" }],
    organizations: [], threads: 2, todo: 1 };
  p.command(`merge-picked:${JSON.stringify({ value: "p-ben" })}`, cx);
  await settle();
  out.preview = { title: state.picker.title, body: state.picker.body.map((l) => l.text), merged: methods().includes("command.inbox.merge_persons") };
  p.command(`merge-chosen:${JSON.stringify({ value: "accept" })}`, cx);
  await settle();
  out.merge = calls.find((c) => c.method === "command.inbox.merge_persons")?.params;
} else {
  const Drafts = await load("drafts"), Sequences = await load("sequences"), Nav = await load("nav");
  const drafts = Object.create(Drafts.prototype), seqs = Object.create(Sequences.prototype), nav = Object.create(Nav.prototype);
  Object.assign(drafts, { rows: [{ id: "draft-1" }] });
  Object.assign(seqs, { rows: [{ id: "seq-1", name: "Warm intro" }], hi: "seq-1" });
  Object.assign(nav, { summary: {}, shell: {} });
  state.draft = "draft-1"; state.sequence = "seq-1";
  const said = { "command.inbox.delete_draft": "Draft deleted: x", "command.sequences.delete_sequence": "Deleted Sequence: Warm intro",
    "command.sequences.delete_step": "Step 2 deleted" };  // the newest Command's words, as the Service keeps them
  answers["undo.newest"] = () => ({ id: "c", label: said[calls.filter((c) => c.method in said).at(-1).method] });
  answers["command.inbox.delete_draft"] = { id: "draft-1", body: "x" };
  drafts.command("delete", cx);
  await settle();
  nav.command("undo", cx);
  await settle();
  answers["command.sequences.delete_sequence"] = { sequences: [{ id: "seq-1" }] };
  seqs.command("delete", cx);
  await settle();
  nav.command("undo", cx);
  await settle();
  const steps = Object.create(Sequences.prototype);  // x in the Steps pane deletes the Step
  Object.assign(steps, { at: 0, rows: [{ id: "seq-1", name: "Warm intro" }], hi: "seq-1",
    page: { sequence: { id: "seq-1" }, outline: [{ step: { id: "step-1", position: 2, kind: "email" } }] } });
  state.seq_pane = "steps";
  answers["sequence.page"] = { sequence: { id: "seq-1" }, outline: [], steps: [] };
  answers["command.sequences.delete_step"] = [1, { step: { id: "step-1" } }];
  steps.command("delete", cx);
  await settle();
  nav.command("undo", cx);
  await settle();
  out.calls = calls.map((c) => [c.method, c.params]);
  out.pickers = state.picker ?? null;
}
console.log(JSON.stringify({ ...out, toasts }));
