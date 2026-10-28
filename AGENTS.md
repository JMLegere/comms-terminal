# Comms Terminal — Agent Rules

Jeremy's single-user console for every professional conversation: Gmail, Fastmail, Slack, Quo SMS and Google Calendar land as Threads in one queue, each showing who owes the next reply. Status follows Plain. Outreach runs as Sequences, deals as Pipelines. One SQLite file, one service that holds every secret, one Textual console, and a CLI for agents.

`GLOSSARY.md` defines every term below. Use its words exactly in code, tests, docs and messages to Jeremy, and avoid the words it lists under _Avoid_.

## Where it runs

| Host | What runs there |
|---|---|
| **Oracle** (`ssh oracle`, dev-server-a1) | The live service in Docker container `comms-terminal`. It is the only writer of the live database (`/home/inbox/.local/share/comms-terminal/inbox.db` inside the container). |
| **Jeremy's laptop** | The console on a local copy of the live database (`deploy/comms-terminal-local`, ADR 0004). Every write goes to Oracle over SSH. |
| **Workbox** | Agents and repositories. `comms-terminal` there is a wrapper that runs the live CLI on Oracle (`ssh oracle sudo docker exec -i comms-terminal comms-terminal …`). The old Workbox install is retired; never reinstall it. |

## Concepts

How the parts fit. The glossary has the full definitions and edge cases.

```
Platform ─▶ Connection ─▶ Channel ─▶ Thread ◀── Activity (messages, Phone calls, Meetings)
                                       │
                         Status + Status detail ── Audit entry (why it moved)
                                       │
            Case (one goal, many Threads; Status rolls up from them)
                                       │
            Person ── Identity (email, Slack user, phone) ── Membership ── Organization
                                                                              │
            Sequence ── Step ── Enrollment (one Person, one Organization) ── Task      Pipeline ── Stage, Value, Target
```

- **Thread** is the unit of work. Email threads by headers; Slack and SMS group by quiet time (24 hours Slack, 7 days SMS). Every calendar Event is a Meeting Thread or Block Thread too.
- **Status** is Todo, Snoozed or Done, each with a **Status detail**: Needs first response, Needs next response, Investigating, Draft ready; Waiting for them, Paused for later; Done, Ignored. A send moves a Thread to Waiting for them; their reply, or 4 days with no reply, brings it back to Todo. Snoozes always end at a date. **Draft ready** is shown, never stored: a Todo Thread whose reply Draft is ready to send lists last in Todo, so a reply Draft an agent writes moves its Thread there.
- **Ignored** is Done for noise. **Blocked** flags an Identity so its new Threads start Ignored.
- **Draft** is an unsent message with its own Connection and recipients. A reply Draft belongs to one Thread. A Draft carries a **Draft revision**; **Needs edit** stops it sending until its text changes; **Send outcome unknown** locks it until Jeremy resolves it.
- **Case** groups Threads that serve one goal across Platforms. Jeremy never sets a Case's Status.
- **Person** is a set of Identities. **Organization** holds People through Memberships that follow company email domains; Jeremy's own changes win. A person acting on their own account gets a **Sole proprietorship**.
- **Sequence** is ordered Steps (touches Jeremy does, Actions the console does, Ifs that choose a path) with On start and On completion. An **Enrollment** runs one Person through it for one Organization: Active, Paused, Finishing or Finished. Any inbound message from the Person pauses it. Each Step becomes a **Task** for that Enrollment; an email Task makes a Draft and is done when the Draft is sent.
- **Pipeline** (Revenue, Fundraising, Talent) places each Organization at a **Stage** with a **Value**, and works backwards from a **Target** to the **Constraint** and This week's actions. **Unattended** Organizations sit in an open Stage with nothing bringing them back.
- **Context sources** (Attio, Tana, Linear) show read-only context beside Threads, People, Organizations and Cases. They hold no Threads.
- **Calendar** lays out Events, Tasks, waking Snoozes, scheduled Drafts and Enrollment starts. **Respond** sets Jeremy's Invite state through Google Calendar.

## Working with the app as an agent

Jeremy works in the console; agents read and prepare work through the CLI. On Workbox run the wrapper (`comms-terminal …`); from elsewhere use `ssh oracle 'sudo docker exec -i comms-terminal comms-terminal …'`. Every command prints Markdown unless it says otherwise.

| Need | Command |
|---|---|
| The queue | `comms-terminal threads [Todo\|Snoozed\|Done\|Ignored\|All]` |
| One Thread with its messages, Attachments and Attio/Tana/Linear context | `comms-terminal thread ID` |
| Drafts, then the Connections a new Draft can use | `comms-terminal drafts` |
| One Draft | `comms-terminal draft ID` |
| Change a Draft | `comms-terminal draft ID --revision N --body -` (also `--to`, `--cc`, `--subject`, `--needs-edit`) |
| Start a reply Draft | `comms-terminal draft new --thread ID --body -` |
| Start a Draft for a new Thread | `comms-terminal draft new --connection ID --to … --subject … --body -` |
| Due Tasks with their Person and Draft | `comms-terminal tasks` |
| Enroll a Person | `comms-terminal enroll PERSON SEQUENCE [--start DATE\|DAYS] [--org DOMAIN\|sole] [--to EMAIL]` |
| Enrollments | `comms-terminal enrollment list [PERSON]`, `resume ID`, `start ID DATE`, `finish ID REASON` |
| Sequences | `comms-terminal sequence list`, `show NAME`, `apply - [--dry-run]` (Markdown on stdin), `new`, `rename`, `sends-from`, `archive` |
| One Step | `comms-terminal step add\|set\|move\|delete …` |
| Calendar | `comms-terminal events [--from] [--to] [--attendee] [--query] [--last N] [--json]` |
| Pipelines | `comms-terminal pipeline revenue\|fundraising\|talent`; `pipeline target\|stage\|value …` |

`comms-terminal <command> --help` lists every flag. Jeremy can also press `y` on a Thread or Draft to copy the same Markdown for you.

### Rules

- **Nothing sends from an agent.** The CLI never sends, completes a Task, or Responds to an invite. Prepare Drafts; Jeremy reviews and sends them from the console.
- **Draft edits need the revision you read.** Read the Draft, then pass `--revision N`. A stale revision is refused; read again rather than forcing it.
- **Jeremy may have the Draft open.** An edit bumps the revision and his pane shows "Save conflict". Tell him before you change a Draft he is working, and after any edit name each changed Draft so he reloads it with `ctrl+r`.
- **Name things by title.** In anything Jeremy reads, name Threads, Drafts and Tasks by subject and who they are with; an ID may follow in parentheses.
- **Live writes need Jeremy's go.** Enrolling, finishing Enrollments, applying Sequences, setting Stages, Values or Targets all change the live database. Get his explicit go for that change, run `sequence apply --dry-run` first, and read the result back afterwards.
- **No opt-out lines.** Never add a "reply stop" or other opt-out line to a Draft or a Step brief. Keep the Agent Maple Inc. signature and mailing address.
- **Slack Drafts** take `--to "#channel <C0123>"`; the ID must sit inside `<…>`.
- **Scripts against the live database** read it read-only (`file:…inbox.db?mode=ro`) and write only through `comms_terminal` functions, never raw SQL. The `oracle-server` skill has the recipe.
- **After a push to `main`, run no `docker exec` for a few minutes.** That includes the Workbox wrapper. An exec during the deploy makes the updater refuse and the deploy fails. Watch `/var/lib/comms-terminal-deploy/status/deployment.json` on the Oracle host instead.

## Development

```sh
uv sync
uv run python scripts/seed_demo.py --db /tmp/demo.db   # synthetic data
uv run comms-terminal --db /tmp/demo.db                  # console on it; ? lists keys
uv run pytest                                            # unit, console and BDD scenarios
uv run pytest -m wip                                     # scenarios not built yet
```

- Branch from `dev`. **A push to `main` deploys to Oracle** (`docs/runbook.md` §8); push to `main` only with Jeremy's go.
- Feature first: write or change the scenario in `features/` (`features/AGENTS.md`), then the code. PRD names come before scenario names.
- Business rules live in `inbox.py`, `sequences.py`, `pipeline.py` and the adapters; `tui.py` renders and calls them. A function that writes the database declares itself in `writers.py` so the laptop console can run it remotely.
- Schema changes: bump `SCHEMA_VERSION` and add `MIGRATIONS[n]` in `inbox.py`, update `schema.sql`, and add the inverse to `tests/schema_history.py`. The updater validates migrations against a live backup before it switches.
- UI changes: render them with Textual's test driver and check the states in `docs/design/console.md` §5 before calling them done.
- Architecture changes get an ADR in `docs/adr/`. Discovery notes and PRDs live in `.agents/`.
- Secrets stay in the service. Never print credentials, environments or `docker inspect` output beyond named safe fields.

## Map

| Path | What |
|---|---|
| `src/comms_terminal/` | The app (`src/AGENTS.md`) |
| `features/` | Gherkin acceptance scenarios (`features/AGENTS.md`) |
| `tests/` | pytest, BDD steps in `tests/steps/` |
| `deploy/` | Oracle container runtime, updater, systemd units, laptop launcher, Email Worker (`deploy/AGENTS.md`) |
| `scripts/` | Demo data, OAuth sign-ins, one-time imports, Cloudflare setup |
| `storybook/` | Composition mocks and native console captures |
| `docs/` | PRD, requirements, ADRs, design, runbook |
| `GLOSSARY.md` | Domain terms |

## AGENTS.md files

Each AGENTS.md covers its own directory and ends with a pointer here. Read the existing file before you change it, keep its rules, and add one when a new directory needs its own rules.
