# Linear context

Status: Phases 1–4 implemented and checked after Jeremy's "execute the plan". Verification results and regression-suite limitations are below. Under his subsequent "ship it", release `38e196e` is published to `main`/`dev` and deployed to the existing Workbox package/service. Under his 2026-10-16 instruction to set up the connection, Linear is connected with a Read-only key covering both workspace teams; see the connection receipt below. Live console rendering, source health, cache freshness, and human acceptance remain unverified.

This document covers only the Linear addition. Product requirement and decision record: [FR-13c and decision 125](../prd.md).

The design below records the approved contract and selected auth method.

## Review outcome

Auth selected: Jeremy-owned personal API key restricted to **Read** and required teams, held encrypted by the service. The console holds no key.

Jeremy accepted all three annotations:

1. Show every matching issue except **Done**, **Completed**, **Canceled**, or **Archived**. Backlog, triage, and other nonterminal matching issues remain included.
2. Inspect Customer Request text within matched Customer/issue candidates; no global search over unassociated Requests.
3. Preserve existing Person/Organization inheritance and show why each issue matched.

Names/text if straightforward, exact-key fallback otherwise remains the original direction. The review approved the contract; Jeremy subsequently authorized local Phases 1–4 with "execute the plan", then code publication and existing Workbox package/service deployment with "ship it". Key creation/connection and workspace/team-access changes still require separate authorization.

## Goal

Show every matching Linear issue related to the Person or Organization whose conversation Jeremy is handling, except Done, Completed, Canceled, or Archived, without leaving Universal Inbox. Backlog, triage, and other nonterminal matching issues remain included. Linear remains the task-management authority; Inbox only reads context.

## Where it appears

- Separate Linear section on Thread, Person, Organization, and Case pages, after Attio and Tana.
- Preserve existing Person/Organization context inheritance; Cases inherit context through their People and Organizations. No direct Case-to-issue field, copied issue data, or status synchronization.
- Linear issues never become Threads or Activities.

## Automatic matching

Read matching evidence from issue titles, descriptions, Linear Customer fields, and Customer Request text and relations.

- Match Person email Identities and Organization domains exactly.
- Follow the matching Linear Customer's Customer Requests to their issues.
- Inspect Customer Request text only within matched Customer/issue candidates; no global search over unassociated Requests.
- Also allow exact full-name and company-name phrases. Name-only matches must be unique among local People or Organizations; ambiguous names do not match.
- No fuzzy matching, semantic search, or AI matching. Names-and-text means these straightforward exact phrases, not a new inference system.

## What is shown

Show every matching issue except **Done**, **Completed**, **Canceled**, or **Archived**. Backlog, triage, and other nonterminal matching issues remain included. These Linear exclusions leave Inbox status behavior unchanged. Each compact card shows:

- Issue identifier, title, and state.
- Priority, assignee, project, and updated time when available.
- Relevant Customer Request text excerpt.
- Match evidence explaining why the issue matched.
- An **Open in Linear** link.

No issue comments or closed-issue history feed.

## Access, freshness, and failure

- Jeremy-owned personal Linear API key restricted to **Read** and required teams, held encrypted by the service using the existing Attio credential pattern. The console holds no key.
- Read through Linear's GraphQL API on page open, using the existing five-minute service-cache pattern.
- Report errors in the Linear section and show Linear health in the rail's context section and bottom bar, separately from message Connection health.
- No writes, issue/task creation, comments, notifications, webhooks, or Linear-driven Inbox state changes.

## Review checks

- Exact email/domain evidence surfaces related issues, including backlog and triage.
- A unique full name or company-name phrase can surface an issue; an ambiguous name alone cannot.
- Linked Customer Requests bring the relevant issue and request excerpt into context; text inspection stays within matched Customer/issue candidates.
- Done, Completed, Canceled, and Archived issues and comments stay out of the section.
- A provider failure is visible as a Linear-source error, not a successful empty result.
- Existing Person/Organization inheritance is preserved, and each issue shows why it matched. Cases receive only the context of their People and Organizations; their rolled-up Status stays unchanged.

## Spike findings

- **Auth — public documentation verified**
  - [Linear API keys](https://linear.app/docs/api-and-webhooks) can be restricted to **Read** and specific teams. The earlier claim that personal keys cannot be read-only was wrong.
  - Selected: a Jeremy-owned personal key restricted to **Read** and required teams, held as an encrypted service credential. This matches the single-user service and existing Attio pattern without OAuth registration or refresh-token machinery.
  - OAuth remains valid for delegated consent or a future multi-user integration. It is not required merely because the service runs unattended.
  - Jeremy selected **Read-only API key** after the corrected comparison, replacing the earlier OAuth choice. Workspace policy, exact team coverage, Customer Request access with a restricted key, and key-expiration behavior have not been verified.
- **FR-13c and decision 125 — contract review complete**
  - Confirmed: settled screens, read-only boundary, matching-issue cards, Customer Request context, and Case inheritance.
  - Confirmed original conditional scope: names/text if straightforward, exact keys otherwise. At spike time, console requests and the shared cache carried only emails/domains. The implementation retains that socket payload, resolves eligible names in the service, and includes them in its cache key.
  - Public schema verified: `issues` supports `title`/`description` filters, `or`, cursor pagination, and `includeArchived`. Exact email/domain/name phrases can use the same candidate query; substring hits still need exact token/phrase checks locally.
  - Public schema verified: `customerNeeds` supports an exact `customer.domains.some.eq` filter and cursor pagination; each result exposes `content`, `body`, and an optional `issue`. Follow matched Customer Requests to issues without scanning the whole workspace.
  - Customer Request filters expose no `body`/`content` text comparator. Accepted boundary: inspect text within the matched Customer/issue candidate set; no global search over unassociated Requests.
  - Accepted issue scope: every matching issue except Done, Completed, Canceled, or Archived. Backlog, triage, and other nonterminal matching issues remain included. Archived state is separate from completion/cancellation; Inbox status behavior stays unchanged.
  - Existing pages combine Person and Organization evidence. Accepted: preserve that inheritance and show why each issue matched.
  - Show issue-linked Customer Requests; project-only Requests do not supply an issue card. Fail the section visibly on provider/GraphQL errors rather than treating partial failures as a successful empty result.
  - These were documentation/schema findings, not a runtime proof. No private workspace query, authentication, credential action, or implementation occurred during the spike.

## Phased implementation plan

Jeremy authorized local Phases 1–4 with "execute the plan", then code publication and existing Workbox package/service deployment with "ship it". Phase 5 account actions, key creation/connection, and workspace/team-access changes remain separately gated.

- **Phase 0 — Contract review — complete**
  - Auth choice resolved: use the selected Read-only, required-team-restricted personal key.
  - All three FR-13c/decision 125 annotations accepted: every matching issue except Done, Completed, Canceled, or Archived; bounded Customer Request text inspection; existing inherited context and match provenance. Backlog and triage remain included. Preserve the original names-if-straightforward/exact-key fallback direction.
  - Public-schema checks are complete; required team scope, restricted-key Customer Request coverage, and actual workspace responses still need authorized connection checks.
  - Exit met: auth and all three annotations accepted; contract/plan review complete. Jeremy authorized local Phases 1–4.
- **Phase 1 — Access — complete locally**
  - Implement access with the selected Read-only, required-team-restricted personal key and a small stdlib GraphQL client. No OAuth registration or refresh-token machinery.
  - Keep secrets service-side using the existing encrypted-credential pattern; handle authorization failures without printing provider tokens or raw secret-bearing responses.
  - Verify authentication and GraphQL error handling against fake endpoints; no live account setup yet.
- **Phase 2 — Matching — complete locally**
  - Implement exact email/domain evidence and matching Customer-to-Request-to-issue traversal with bounded queries and complete pagination.
  - Use the verified title/description filter route for exact name phrases if wiring unique local names stays straightforward; otherwise use the accepted exact-key fallback.
  - Deduplicate matching issues, excluding Done, Completed, Canceled, or Archived; retain match evidence and include relevant Request excerpts. No comments, closed-history feed, fuzzy matching, or global search index.
- **Phase 3 — Integration — complete locally**
  - Add the local Linear route, five-minute cache, source registry entry, renderer, and separate source health.
  - Include every matching input in request/cache keys; preserve stale-page-result rejection and Attio/Tana behavior.
  - Show Linear after Attio and Tana on all four existing page types; preserve Case/Thread status and reply behavior.
- **Phase 4 — Verification — executed; full regression suite not green**
  - Cover exact matches, ambiguous names, bounded Customer Request text, pagination, Done/Completed/Canceled/Archived exclusions, backlog/triage inclusion, inherited context/match provenance, cache expiry, provider errors, and page changes.
  - Run complete affected test modules and the regression suite.
  - Inspect rendered narrow/wide, loading, empty, and failure states; present the implemented UI on Mani for human review.
- **Phase 5 — Connection — done 2026-10-16**
  - Obtain separate authorization for the exact key creation/connection, workspace, and required-team scope before creating or connecting the Jeremy-owned personal API key restricted to **Read**.
  - Reviewed package deployment is complete under Jeremy's subsequent "ship it" authorization; see the deployment receipt below. Provider connection is complete; see the connection receipt below.
  - Install a credential only after separate authorization for the exact key, workspace, and team scope. Then verify live workspace identity, effective access, representative matches, cache behavior, and source health.

## Local implementation evidence — 2026-10-15

- Implemented [`linear.py`](../../src/universal_inbox/linear.py), the private `POST /linear` route, optional encrypted `linear-token` credential/drop-in, names-aware five-minute service cache, and Linear cards/health on all four page types. No new dependency, database schema, message Connection, or OAuth machinery.
- Matching uses exact emails/domains and unique local full-name/company-name phrases, including ordinary paired Markdown formatting. Customer-name candidates tolerate whitespace, then require normalized exact equality. Customer Request discovery stays bounded; lifecycle exclusions apply through both issue and Request paths.
- Linear sections refetch after local or external database changes; the service reuses its cache when matching inputs are unchanged. Old address/name-revision results cannot replace current answers. Attio/Tana socket payloads and cache behavior stay unchanged.
- Focused checks: `uv run pytest -q tests/test_linear.py tests/test_linear_service.py tests/test_linear_console.py tests/test_tana.py tests/test_attio.py tests/test_serve.py --tb=short` — **225 passed**.
- Full suite: `uv run pytest -q --tb=short` — **570 passed, 3 failed**. The two grouping failures (`test_ingest_groups_a_senders_messages_within_2_hours_into_one_thread` and `test_channel_top_level_groups_until_an_hour_quiet`) also fail on unchanged `efa91a6`. The reconciliation UI failure (`test_message_expansion_trim_and_recipients_follow_native_identity`) passed on that baseline and on a subsequent current-source run of both `test_linear_console.py` and `test_reconcile_console.py` (**43 passed**). Its full-suite intermittency remains unresolved; this is not a green full-suite claim. Unrelated tests were not changed.
- Standards/spec re-review closed three findings: whitespace-sensitive Customer candidate filtering, formatted-email false negatives, and stale local-name context. Regression checks cover the real query predicates and actual Unix-socket/name-resolution/cache consumer, not only renderer fakes.
- Render proof: 19 synthetic Textual captures at 220, 160, 100, and 80 columns, plus loading/empty/error states, in temporary `/tmp/linear-context-proof-u9g3_wez/`. Opened and inspected representative captures for all four page types, all four widths, and all three transient states. Thread properties, including all Context sources, retain their existing collapse below the wide layout; narrower Person/Organization/Case pages still show Linear.
- Presented a current-source, isolated `Linear demo (fixtures)` in Herdr (`wQ:p1X`); source hashes, active workspace/tab/pane, zoom, and visible issue/match/Request/link markers were verified. The fixture uses no real inbox data or provider credential. Human review/acceptance remains separate.
- During the earlier local implementation verification above, no Linear key was created or installed; no private workspace query, installer execution, service restart, external write, commit, or deployment occurred. Restricted-key workspace/team/Customer Request coverage and live links/health remain Phase 5 checks in the [runbook](../runbook.md#3e-connect-linear-context-later-authorization).
- Subsequent release authority and execution: Jeremy's "ship it" authorized code publication and the existing Workbox package/service deployment, now completed as recorded below. Key creation/connection and workspace/team-access changes remain separately gated. The test counts above describe the earlier local source, before release integration.
- Release integration preserves published upstream `a41d18d`, including agent CLI/Markdown commands and Attachment/Inline image definitions. Added a fixture-backed actual CLI check that fresh Thread Markdown includes Linear; no upstream agent-command code needed changes.
- Post-rebase checks: the same six focused modules plus `tests/test_copy_for_agent.py` and `tests/test_reconcile_console.py` — **240 passed, 1 failed**. The failure was `test_domain_literal_message_controls_keep_exact_identity_through_merge`'s date-header assertion; it passed on an isolated release rerun. Three isolated unchanged-`a41d18d` runs also passed, so this failure is intermittent and is **not proven pre-existing**. No unrelated test was changed, and no green full-suite claim is made.
- Regenerated 19 merged-release fixture captures in temporary `/tmp/linear-context-proof-438yiswb/`; inspected the 220-column Thread, 100-column Person, and 80-column Case renders. These source renders preceded the installed-artifact verification recorded below.

## Deployment receipt — 2026-10-15

- Under Jeremy's "ship it", release `38e196e8bc07d24c22543f0b150ca0427f47d04c` was published to `main`/`dev` and deployed to the existing Workbox uv-tool package/service. All **19 packaged Python/SQL source files** match that release.
- The earlier noninteractive sudo check required authentication. After Jeremy completed real sudo authentication, a normal package reinstall and existing service restart completed. `universal-inbox.service` is active/running; `/run/universal-inbox/inbox.sock` remains **0600** and the live database remains **schema 11**. `deploy/install.sh` was not run.
- Live private-socket checks: `POST /linear` with `{}` returned **503** (`Linear is not connected`); an explicit `{"emails":null}` returned **400** through safe array validation.
- Linear remains unconnected: its credential drop-in is absent. No Linear key creation/installation, account/team permission change, private Linear workspace query, or Linear issue/task/comment write occurred. Exact key/workspace/required-team authorization and live identity, access, Customer Request, match, link, cache, and health verification remain Phase 5 gates.
- Installed-package UI proof imported `Console` from the installed uv-tool package, with provenance asserted. Generated 19 synthetic fixture captures in temporary `/tmp/linear-context-proof-p3swqg_6/`; opened and inspected Thread 220, Person 100, Organization 160, Case 80, and 220-column loading/empty/error renders. The inherited narrow-Thread properties-collapse limitation remains. This establishes installed fixture rendering; real-provider matches, live links, and human acceptance remain unverified.

## Connection receipt — 2026-10-16

- Jeremy asked Claude to take control of his Chrome and set up the Linear connection. Claude created the personal API key "Universal Inbox" in the `agentmaple` workspace with **Read** as its only permission and **All teams you have access to**, which is both teams (Engineering `DEV`, Business `BIZ`). Claude copied the key with Linear's copy button and never read or displayed its value.
- Jeremy pasted the key into a hidden prompt and ran a Linear-only staging script with sudo: `systemd-creds encrypt --name=linear-token` into the service credential store, `shred` of the staged copy, the `linear.conf` drop-in from `main`, `daemon-reload`, and a service restart. `deploy/install.sh` was not run, because the repository's `universal-inbox.service` differed from the installed unit and the installer would have overwritten it.
- `universal-inbox.service` is active with the `linear.conf` drop-in. Linear's key list shows "Universal Inbox" with 1 permission, 2 teams, and last use on 2026-10-16.
- Live private-socket checks: `POST /linear` with `{}` returned **200** `{"issues":[]}`. A request for an active customer's domain returned 12 nonterminal issues across Now, Triage, In Review, Dev Deployed, and Prod Deployed states, each with match reasons and an Open in Linear link. Two of those issues matched through the Linear Customer and its Customer Requests, which confirms Customer Request access with this key. A request for a customer whose linked issues are all completed, canceled, or duplicates returned no issues, as the exclusion rule requires.
- No Linear write occurred. Live console rendering on real pages, Linear source health in the rail and bottom bar, five-minute cache freshness, key expiration behavior, and human acceptance remain unverified.

## Sources

- [Universal Inbox FR-13c and decision 125](../prd.md).
- [Console context-source presentation](console.md#context-sources).
- [Linear OAuth](https://linear.app/developers/oauth-2-0-authentication).
- [Linear Customers and Customer Requests API](https://linear.app/developers/managing-customers).
- [Linear Customer Requests](https://linear.app/docs/customer-requests).
- [Linear API-key permissions and team restrictions](https://linear.app/docs/api-and-webhooks).
- [Linear filtering](https://linear.app/developers/filtering) and [pagination](https://linear.app/developers/pagination).
- [Linear GraphQL schema](https://api.linear.app/graphql): unauthenticated `__type` metadata checked for `IssueFilter`, `CustomerNeedFilter`, `CustomerNeed`, `NullableCustomerFilter`, `StringArrayComparator`, `StringItemComparator`, and root query arguments; no workspace records queried.
- Local request/cache seams: [`ContextSection` and `socket_context`](../../src/universal_inbox/tui.py), [`handle_linear`](../../src/universal_inbox/serve.py), and [`tana.Context`](../../src/universal_inbox/tana.py). The socket still carries email/domain keys; Linear adds service-resolved eligible names to its cache key and a local database revision to its UI key.
