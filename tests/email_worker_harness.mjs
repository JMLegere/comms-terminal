// Runs deploy/email-worker/worker.js under Node against a live hook: node harness.mjs <hook-url> <secret> <eml> [to]
import { readFileSync } from "node:fs";
import worker from "../deploy/email-worker/worker.js";

const [hookUrl, secret, emlPath, to = "jeremy@inbox.agentmaple.com"] = process.argv.slice(2);
const raw = readFileSync(emlPath);
let rejected = null;
const message = { to, raw: new Blob([raw]).stream(), setReject: (reason) => { rejected = reason; } };
await worker.email(message, { HOOK_URL: hookUrl, EMAIL_HOOK_SECRET: secret });
console.log(rejected === null ? "accepted" : `rejected: ${rejected}`);
