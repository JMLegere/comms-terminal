# Fundraising and Talent Pipelines hold People

Status: accepted (2026-11-04). Amends [ADR 0006](0006-console-owns-organization-stage.md) and [ADR 0008](0008-sequences-set-stage.md).

ADR 0008 kept every Stage on an Organization and gave an angel or candidate a Sole proprietorship. Working the Fundraising Pipeline on 2026-11-04 showed that this does not fit how money is raised. East Valley "does not invest as a firm - they invest individually"; NBIF's cheque is worked through Laïla, who works for NBIF but is not a Sole proprietorship; Atlantic Growth Solutions' interest is carried by Chris, its founder. Jeremy decided: "fundraising pipeline should be based on people", Talent too, and Revenue stays on Organizations. A Fundraising or Talent card names the Person and every Organization they are a member of, so "Laïla Thériault · NBIF" still says where the cheque comes from without a second field.

## Considered Options

- **Keep Organizations and create a Sole proprietorship for every investor.** Rejected: it calls Laïla a person acting on her own account, and the Glossary's Sole proprietorship would mean two things.
- **A Person card with a separate "invests for" Organization.** Rejected as unneeded: Memberships already say which Organizations a Person belongs to.
- **Each card is either a Person or an Organization.** Rejected: one Pipeline would mix two kinds of entry and its rates would count unlike things.

## Consequences

- The Pipeline log keeps one subject per Pipeline: an Organization in Revenue, a Person in Fundraising and Talent. The migration moves every Fundraising and Talent entry on a Sole proprietorship to its Person and drops the firm entries that were already undone; a firm entry still live stops the migration.
- Sole proprietorships stay, for a Revenue customer who buys on their own account and for Enrollments that name one.
- A Sequence Action or If on Fundraising or Talent acts on the Enrollment's Person and needs no Organization.
- The Person page shows and sets Fundraising and Talent (p Stage, v Value; s stays enroll). The Organization page keeps Revenue only.
