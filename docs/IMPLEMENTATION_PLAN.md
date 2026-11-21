# Looker Helper: Implementation Plan

## Status (2026-11-21)

| Phase | Status |
|---|---|
| 0 Foundations | ✅ Done |
| 1 BigQuery and session setup | ✅ Done (demo mode tested; live mode written but not yet run against real BigQuery) |
| 2 LookML generator | ✅ Done |
| 3 Cortex conversation | ✅ Done with the mock. `RealCortex` is waiting for the corporate client code. |
| 4 Results and Home | ✅ Done |
| 5 Settings | ✅ Done |
| 6 Built-in best practices | ✅ Done |
| 7 Looker API | 🟡 Partly done: connection test only. Send to Looker, real numbers, and schedules and alerts are not built. |
| 8 Production hardening | 🟡 Mostly done: header sign-in, per-user isolation, admin role, Docker image with embedded PostgreSQL, Cloud SQL / external PostgreSQL modes, Alembic migrations, health check, README. Audit log and rate limits are not built. |

Changes from the original plan:
- The frontend is plain JavaScript modules with no build step, because npm is blocked.
- The database is PostgreSQL only: embedded in the Docker image by default, switchable to Cloud SQL with `LH_DB_MODE`. Development and tests run in the same image (see README).
- Jinja templates were replaced by Python code that writes the LookML files directly.

Goal: a working web app that turns a plain-English request into LookML files (views, model, dashboard), using BigQuery table details and the Cortex LLM proxy. The UI follows `wireframe/concept-b.html`.

## Guiding decisions

1. **The LLM plans, Python writes the LookML.** Cortex turns the user's request into a structured plan (JSON validated against a schema): which metrics, breakdowns, joins and dashboard tiles. A deterministic Python generator turns that plan plus the BigQuery table details into LookML. Results:
   - field names are always real
   - output is testable
   - the LLM can't produce invalid syntax
2. **Every plan is checked against the session's table details.** Any column the LLM mentions that isn't in the selected tables is rejected and re-asked. A session can only ever touch the project, dataset and tables chosen at setup.
3. **Business users never see options.** Good Looker practice (correct join totals, partition filters, hidden personal data, and so on) is applied automatically and reported in plain language. See Phase 6.
4. **Simple stack, no frontend build step.** FastAPI serves the API and the static HTML/CSS/JS from Concept B. PostgreSQL (embedded in the Docker image, or Cloud SQL) through SQLAlchemy.
5. **Cortex sits behind an interface.** A mock implementation lets everything be built and tested before the real Cortex client is plugged in.

## Tech stack

| Area | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 |
| Storage | SQLAlchemy 2 + PostgreSQL (embedded in the image, or Cloud SQL), Alembic migrations |
| BigQuery | `google-cloud-bigquery`, `google-cloud-resource-manager`; Application Default Credentials |
| LookML | Jinja2 templates per file type, parsed back with the `lkml` package to verify syntax |
| LLM | `CortexClient` interface: `MockCortex` (dev/tests) and `RealCortex` (wraps the corporate client) |
| Secrets | `cryptography` (Fernet) for stored Looker API secrets and Cortex keys |
| Frontend | Plain HTML + CSS + vanilla JS modules, built from Concept B |
| Tests | pytest, httpx TestClient, fixture table metadata |
| Packaging | `pyproject.toml`, `.env` config, Dockerfile (Phase 8) |

## Project layout

```
looker-helper/
  app/
    main.py                 # FastAPI app, static files, routers
    config.py               # settings from env / .env
    db.py, models.py        # SQLAlchemy models
    auth.py                 # current user (dev header now, SSO later)
    api/                    # routers: sessions, chat, packages, bigquery, settings
    services/
      bigquery_meta.py      # list projects/datasets/tables, read table details
      cortex/               # base.py (interface), mock.py, real.py, prompts.py
      planner.py            # request -> validated Plan (via Cortex)
      lookml/               # generator.py, templates/, validate.py, best_practices.py
      packaging.py          # versions + .zip
      looker_api.py         # Phase 7
    schemas/                # Pydantic: Plan, TableMeta, Package, ...
  static/                   # index.html, css/, js/ (from Concept B), assets/brand-logo.svg
  tests/                    # unit + API tests, fixtures/
  docs/
```

## Data model (first version)

- **users**: id, email, display_name, created_at
- **looker_connections**: id, owner_id (null = shared by admin), name, base_url, bq_connection_name, looker_project, folder_layout, client_id, client_secret_enc, is_default, last_test_status
- **sessions**: id, user_id, title, gcp_project, dataset, tables (JSON), looker_connection_id, model_name, status, created_at, updated_at
- **table_snapshots**: session_id, table_name, metadata (JSON: columns, types, descriptions, partitioning, clustering, policy tags, row count)
- **messages**: id, session_id, role, content, kind (text / plan / package), payload (JSON), created_at
- **packages**: id, session_id, version, plan (JSON), checks (JSON), created_at
- **package_files**: package_id, path, content
- **user_settings**: user_id, theme, defaults (JSON)
- **app_settings**: Cortex endpoint, auth method, encrypted key, model, timeout (admin)

## Phases

Each phase ends with something that runs and is tested. **Phases 0–5 make up the working MVP**, Phase 6 makes it good, and Phases 7–8 make it production-ready.

### Phase 0: Foundations
- Repository skeleton, `pyproject.toml`, `.env.example`, config loading, logging.
- FastAPI app serving the Concept B static shell (Home, top bar, theme toggle, Settings sheet) with hash routing.
- PostgreSQL + migrations, with a dev user taken from a header or config.
- `CortexClient` interface plus `MockCortex`.
- pytest setup and a smoke test.

**Done when:** `uvicorn app.main:app` opens the Concept B home page in the browser, and the tests pass.

### Phase 1: BigQuery and session setup
- Endpoints to list the user's projects, the datasets in a project, and the tables in a dataset (with descriptions and row counts).
- Read table details: columns, types, nested fields, descriptions, partitioning and clustering, and policy tags. Store a snapshot on the session so the session uses only those tables.
- New-session dialog wired to these endpoints (2 steps: data, then Looker connection).
- Create a session, list sessions on Home, reopen a session.

**Done when:** a user can pick a real project, dataset and tables, and a session is saved with their table details.

### Phase 2: LookML generator (no LLM yet)
- `Plan` schema: metrics (sum / count / count_distinct / average / ratio), breakdowns, time grains, joins, filters, dashboard tiles.
- Generator:
  - **Views:** dimensions by BigQuery type, `dimension_group` for date and time columns, primary key detection, measures, labels and descriptions copied from BigQuery.
  - **Model:** connection, includes based on the folder layout, explores and joins with relationships.
  - **Dashboard:** `.dashboard.lookml` with KPI, line and bar tiles laid out automatically.
- Validation: parse every file with `lkml`, and check that every field reference exists.
- Packaging: versioned files, plus a `.zip` that follows the connection's folder layout.
- Unit tests using fixture table details, including Orders, Order items and Customers.

**Done when:** a hand-written plan JSON produces valid LookML files and a downloadable .zip.

### Phase 3: Cortex conversation
- `RealCortex` adapter that wraps the corporate Cortex client. **Needs the Cortex client code.**
- Prompts:
  - **Suggest tables:** pre-tick the likely tables from the user's request.
  - **Request → Plan:** JSON output only, with the table details of the selected tables in the context.
  - **Change request → updated Plan:** for example, "leave out cancelled orders".
  - **Explain:** answer questions about tables and metrics in plain English.
- Plan validation against the table details, with one automatic retry when it fails. Clear error messages.
- Chat API and UI: user message → plan card ("Build it" / "Change something") → build → package card. A "Checking your tables…" status while Cortex works.

**Done when:** typing "Revenue by region for each month, as a dashboard" produces a plan, and confirming it produces a package.

### Phase 4: Results and Home
- Package sheet:
  - **Preview:** a sketch of the dashboard layout with sample numbers.
  - **Metrics:** each metric in plain English.
  - **Checks:** results in plain language and where each number comes from.
  - **Files:** tabs per file, versions, compare, copy.
- Home shelf of session package cards: thumbnail from the dashboard layout, status, .zip download, search and filter.
- Session history: every build creates a new package version.

**Done when:** the full Concept B flow works end to end, from Home to download.

### Phase 5: Settings
- Looker connections: personal and admin-shared, create / edit / test / remove, secrets encrypted, one default. Adding a connection inside the session dialog uses the same API.
- Cortex settings (admin): endpoint, sign-in method, key, model, timeout, advanced options, test.
- Appearance (theme) and defaults for new sessions.

**Done when:** connections and Cortex can be configured from the UI with no config file edits.

**→ Working MVP complete.**

### Phase 6: Built-in best practices (applied automatically, no options)
- **Correct totals across joins:** detect when a join multiplies rows, and make sure every view has a primary key.
- **Large tables:** a default date filter on the partition column (e.g. last 12 months) via `conditionally_filter`.
- **Caching:** a `datagroup` that refreshes when the table changes.
- **Personal data:** hide columns with BigQuery policy tags (or matching name patterns) behind an access grant.
- **Data checks:** `test:` blocks such as unique primary key and non-negative amounts.
- **Dashboard extras:** compare with last year (`period_over_period`), cross-filtering, drill fields.
- **Cost estimate:** a BigQuery dry run of the dashboard's queries, shown in plain words in Checks.

**Done when:** generated packages include all of the above, and the Checks tab reports each item.

### Phase 7: Looker API integration (optional, needs API credentials)
- Validate the generated project in Looker dev mode.
- Send to Looker: push to a development branch and open a pull request.
- Show real numbers in Preview, using dev-mode queries.
- Schedules and alerts from the Preview panel. Views per package on Home.

### Phase 8: Production hardening
- Corporate sign-in (SSO / IAP headers), with each user seeing only their own data.
- Each user's BigQuery access is enforced, either through the user's own credentials or through a service account with checks.
- Audit log, rate limits, clear error pages, keeping sessions for a set time.
- Dockerfile, deployment docs, admin guide, user guide.

## Risks and how we handle them
| Risk | Mitigation |
|---|---|
| LLM output is inconsistent | JSON-only plan with schema validation, one retry, and deterministic LookML generation |
| Wrong totals across joins | Primary key detection and fan-out checks (Phase 6, with basic checks in Phase 2) |
| BigQuery cost from big tables | Read metadata only (no data scans), plus a default partition filter |
| Secrets exposure | Encrypted at rest and never returned to the browser |
| Cortex client differences | Adapter interface; the mock keeps development unblocked |

## Needed from the team
- The **Cortex client code** and an example call (needed in Phase 3).
- **How users sign in** and **where the app runs** (each person's own machine, or a shared server). This decides whose BigQuery credentials are used.
- A test project or dataset in BigQuery, and a Looker dev instance (for Phase 7).
- The official logo file for `static/assets/brand-logo.svg`.
