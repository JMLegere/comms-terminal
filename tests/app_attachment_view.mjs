// FR-A5 in the App: a stored Attachment's chip saves the file in Downloads and selects it in Files (reveal);
// a chip not copied yet opens the message in its service (open). A file staged on a Draft opens the same way
// from the Composer. Run in the shipped thread and draft views.
import { clicks, cx, load, opened, revealed, tasks } from "./app_view_kit.mjs";

const Thread = await load("thread");
const t = Object.create(Thread.prototype);
const sha = "ab12";
t.files([
  { name: "deck.pdf", sha, state: "stored", url: `http://127.0.0.1:8789/f/${sha}.sig/deck.pdf` },
  { name: "late.zip", sha: null, state: "pending", url: "https://mail.google.com/mail/#search/x" },
  { name: "old.pdf", sha, state: null, url: "https://mail.google.com/mail/#search/y" }, // stored, but no file server link
], {});
for (const click of clicks) click({}, cx);
const received = { revealed: revealed.splice(0), opened: opened.splice(0) };

// A New Thread Draft with one staged file, shown in the Composer as draft.view returns it.
const Draft = await load("draft");
const view = new Draft();
view.init({}, cx);
tasks.length = 0;
const draft = { id: "draft-2", revision: 1, thread_id: null, connection_id: "connection-1", kind: "email", sender: "me@example.test",
  to: "ana@example.test", cc: "", subject: "Plans", body: "See attached", sending_at: null, send_at: null, needs_edit: 0 };
view.composer.showDraft({ draft, connection: "Gmail · me@example.test", sender: "me@example.test",
  staged: [{ id: "s1", sha: "cd34", name: "quote.pdf", type: "application/pdf", size: 9, url: "http://127.0.0.1:8789/f/cd34.sig/quote.pdf" }] });
clicks.length = 0;
view.composer.editor({});
clicks.at(-1)({}, cx); // the staged file's chip is the last thing in the editor that takes a click
console.log(JSON.stringify({ ...received, staged: { revealed, opened } }));
