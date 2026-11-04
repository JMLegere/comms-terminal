// ADR 0016: on Today, an Unattended Person takes p/v and an Unattended Organization s/v, and its last message
// (a tagged datetime from the core) reads as an age.
// Usage: node --experimental-vm-modules app_today_keys_view.mjs <Person|Organization> <key>
import { cx, load, ran, toasts } from "./app_view_kit.mjs";

const Today = await load("today");
const today = Object.create(Today.prototype);
const last = { $dt: new Date(Date.now() - 3 * 86400 * 1000).toISOString() };
const [holder, key] = process.argv.slice(2);
const row = holder === "Person"
  ? { id: "person-1", name: "Nick", person_id: "person-1", stages: { FUNDRAISING: "Meeting" }, last }
  : { id: "org-1", name: "Acme", domain: "acme.test", person_id: null, stages: { REVENUE: "Education" }, last };
today.data = { threads: [], tasks: [], drafts: [], paused: [], unattended: [row] };
today.at = today.rows().findIndex((x) => x.kind === "unattended" && x.r);
today.command(key, cx);
console.log(JSON.stringify({ ran, toasts, age: Today.ns.when(last) }));
