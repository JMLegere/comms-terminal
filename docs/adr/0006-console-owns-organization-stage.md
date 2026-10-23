# Comms Terminal owns Organization Stage, with no Attio sync

Status: accepted (2026-10-23)

Jeremy wants one place that tells him what to do on sales, using Winning by Design's Bowtie math: work backwards from the Target through each Stage's Conversion rate and Time in Stage to the Required volume per week. That math needs Stage history he trusts. Each Organization holds a Stage and a Value in each Pipeline (Revenue, Fundraising), and Jeremy sets every change himself in the console or CLI. Every change goes into an append-only log in Comms Terminal. Attio is not read or written for Stage. This changes the PRD non-goal "CRM pipeline" (`docs/prd.md`), which left the pipeline to the CRM. The deciding facts, recorded in `.agents/discovery/2026-10-22-pipeline-management.md`:

- Jeremy removed Attio's Deals object on 2026-10-22, following Winning by Design's advice to track accounts, not deals. The stage history held there went with it.
- Attio's company "Account Stage" attribute has no statuses and no history. Only the Paying Customers list keeps history, and only for customers who already pay.
- Metabase's pipeline cards read the Deals object, so they no longer show the funnel.
- Agents updated Attio stages after the fact, so the stages drifted and the measured rates were unreliable.

## Considered Options

- **Attio owns Stage, and the console reads it** (with `show_historic` for history). Rejected: the console's Attio token is read-only, the company Stage has no history yet, and drift from agent updates would keep corrupting the rates.
- **The console owns Stage and syncs to Attio through the CLI**, either as a diff or a push. Rejected by Jeremy: the console stays self-contained, and a second copy of Stage only invites disagreement.
- **Metabase owns the math.** Rejected: it is a reporting tool away from the daily console, and its funnel cards depend on the removed Deals object.

## Consequences

- Attio's Account Stage and the console's Stage will differ over time. The console's Stage is the one the math uses.
- Value (forward revenue for a customer, expected next-twelve-month revenue for a prospect, the cheque for an investor) is set by hand and can differ from Xero. Nothing checks it against Xero.
- Past Stages before this decision are entered once by hand with backdated changes (Iron Maple, Chestnut, LCL, STMEC). Every rate before that is missing.
- With about 11 Organizations and a 90-day window, many rates will be empty. Required volume then uses Planning priors, marked as assumed; observed rates never use them.
