// FR-A5 in the App: a stored Attachment's chip saves the file in Downloads and selects it in Files (reveal);
// a chip not copied yet opens the message in its service (open). Run in the shipped thread view.
import { clicks, cx, load, opened, revealed } from "./app_view_kit.mjs";

const Thread = await load("thread");
const t = Object.create(Thread.prototype);
const sha = "ab12";
t.files([
  { name: "deck.pdf", sha, state: "stored", url: `http://127.0.0.1:8789/f/${sha}.sig/deck.pdf` },
  { name: "late.zip", sha: null, state: "pending", url: "https://mail.google.com/mail/#search/x" },
  { name: "old.pdf", sha, state: null, url: "https://mail.google.com/mail/#search/y" }, // stored, but no file server link
], {});
for (const click of clicks) click({}, cx);
console.log(JSON.stringify({ revealed, opened }));
