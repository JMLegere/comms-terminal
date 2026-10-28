# Acceptance features

Cucumber-style Gherkin, run by pytest-bdd (`tests/test_features.py` binds every `.feature` here).

- One product domain per feature file (threads, people, organizations, cases, email, slack, sync). Name the file after the domain. Use `GLOSSARY.md` terms only.
- A scenario lives in the domain whose rule it tests. Split a scenario that spans domains.
- Write steps against the interface in `docs/design/api-and-database.md`, worded as plain sentences: the four `inbox` calls (Ingest, Act, Reply, Wake), the adapters (Email Parse, Slack Parse, Email Sender, Slack Sender), records, and fields in Title Case words (`When Ingest receives a new email from "ana@acme.com"`). Write booleans as prose (`is blocked`, `is not blocked`), and quote literal values (`Status "Todo"`).
- Map each Title Case name to its code name once, in the feature description (`Ingest (inbox.ingest)`).
- Use only names from `GLOSSARY.md`, `docs/prd.md`, and `docs/design/api-and-database.md`. When a scenario needs a name the PRD lacks, add it to the PRD first.
- Assert exact stored values: Plain enum names for Status and Status Detail (`Status "TODO" and Status Detail "CREATED"`), `"BROKEN"`. Never combined or approximate ones.
- Tag each scenario with its PRD acceptance scenario (`@AS-n`) and requirements (`@FR-n`, `@NFR-n`).
- Write the scenario first. Keep it tagged `@wip` until its steps pass, then remove the tag.
- `uv run pytest` runs everything not `@wip`. `uv run pytest -m wip` shows the red scenarios.
- `uv run pytest --generate-missing --feature features/<file>.feature` prints step skeletons.
- Put step definitions in `tests/steps/` and register each module in `tests/conftest.py` via `pytest_plugins`.
- When a PRD acceptance scenario changes, change its feature scenario in the same commit.

See /AGENTS.md for project-wide rules.
