# Looker Helper

Describe what you want to measure in plain English. Looker Helper reads your BigQuery table details, plans the metrics with the Cortex LLM, and generates LookML files (views, a model and a dashboard), downloadable as one `.zip` per session.

- **Backend:** Python, FastAPI, PostgreSQL (via SQLAlchemy, with Alembic migrations)
- **Frontend:** plain HTML, CSS and JavaScript modules in `static/`. No npm and no build step.
- **Design:** `wireframe/concept-b.html`

## Run it in Docker (the deployment setup)

```powershell
docker compose up --build        # then open http://localhost:8000
```

The image contains the app and an embedded PostgreSQL 15 (`LH_DB_MODE=embedded`):

- PostgreSQL runs as the `postgres` user. It listens only on a Unix socket inside the container and opens no network port.
- The app runs as the unprivileged `app` user and signs in to the database by its OS identity. There is no database password.
- Everything persistent lives on the `/data` volume: `/data/postgres` holds the database and `/data/app` holds the encryption key.
- Database migrations run automatically when the app starts.
- `docker stop` shuts down the app and then PostgreSQL cleanly.
- `/api/health` checks the database and is used by the image's health check.

Without Compose:

```powershell
docker build -t looker-helper .
docker run -d --name looker-helper -p 8000:8000 -v lh-data:/data --env-file .env looker-helper
```

To build behind a corporate proxy, use `--build-arg BASE_IMAGE=<internal mirror>/python:3.12-slim-bookworm` and `--build-arg PIP_INDEX_URL=<internal PyPI>`.

**Where embedded mode fits.** Use it on a single container with a persistent volume: a VM, a Docker host, or a GKE StatefulSet with one replica. On Cloud Run, or with more than one container, use Cloud SQL. There, the container file system isn't kept between restarts, and each instance would get its own separate database.

## Switching to Cloud SQL

No code changes are needed, only settings. Two ways:

**A. Cloud SQL Python Connector (`LH_DB_MODE=cloudsql`).** Recommended. No sidecar is needed.

```
LH_DB_MODE=cloudsql
LH_CLOUDSQL_INSTANCE=my-project:europe-west2:looker-helper
LH_CLOUDSQL_IP_TYPE=private                 # private | public | psc
LH_DB_NAME=looker_helper
LH_DB_USER=looker_helper
LH_DB_PASSWORD=...                           # or LH_CLOUDSQL_IAM_AUTH=true for IAM login (no password)
LH_SECRET_KEY=...                            # see "Moving the data"
```

The container's service account needs the `Cloud SQL Client` role, plus `Cloud SQL Instance User` if you use IAM login. With IAM login, set `LH_DB_USER` to the service account email without `.gserviceaccount.com`.

**B. Cloud SQL Auth Proxy (`LH_DB_MODE=external`).** Run the proxy as a sidecar, then set `LH_DATABASE_URL=postgresql://looker_helper:PASSWORD@127.0.0.1:5432/looker_helper`. This mode also works with any other PostgreSQL server.

In both modes, the image skips starting the embedded PostgreSQL. Migrations still run at startup. A database lock lets several containers start at once safely.

### Moving the data from the embedded database to Cloud SQL

```powershell
# 1. Export from the running container
docker exec -u app looker-helper pg_dump -h /var/run/postgresql -d looker_helper -Fc -f /data/app/looker_helper.dump
docker cp looker-helper:/data/app/looker_helper.dump .
docker cp looker-helper:/data/app/secret.key .

# 2. Restore into the new, empty Cloud SQL database (e.g. through the Auth Proxy), before starting the app against it
pg_restore --no-owner --no-privileges -d "postgresql://looker_helper:PASSWORD@127.0.0.1:5432/looker_helper" looker_helper.dump
```

3. Set `LH_SECRET_KEY` to the contents of `secret.key`. Without it, the Looker API secrets saved so far can't be decrypted. Then start the app with the Cloud SQL settings above.

## Development and tests

Everything runs in the same image, against its embedded PostgreSQL. You only need Docker Desktop:

```powershell
docker compose --profile dev up --build dev     # http://localhost:8001; your code is mounted and reloads on save
docker compose run --rm --build tests           # all tests, on a fresh throwaway database each run
```

- **Dev data** is kept in its own `lh-dev-data` volume, separate from the app's `lh-data` volume.
- **Tests** run on an in-memory `/data`, so nothing is left behind.
- **The database tests** reset the schema, so they only run inside the test container.
- **On the laptop**, `.\.venv\Scripts\python -m pytest` runs just the generator and planner tests and skips the rest. Install those dependencies with `pip install -r requirements-dev.txt`.

The default settings use **demo mode**: built-in sample tables and a stand-in for Cortex. The whole flow works without Google Cloud or Cortex access.

## Connecting the real services

| What | How |
|---|---|
| **BigQuery** | Set `LH_METADATA_MODE=live`. The app uses Application Default Credentials (`gcloud auth application-default login`, or a service account on the server). It reads table details and runs dry runs only. It never reads table data. Set `LH_GCP_BILLING_PROJECT` if dry runs should be billed to a specific project. |
| **Cortex** | Add the corporate client call in `app/services/cortex/real.py` (only `chat()` is needed). Then set `LH_CORTEX_MODE=real` and fill in Settings › AI assistant. Prompts are in `app/services/cortex/prompts.py`. |
| **Sign-in** | The app reads the user's email from the `LH_AUTH_HEADER` request header (IAP's `X-Goog-Authenticated-User-Email` by default). Leave `LH_DEV_USER_EMAIL` empty in production. Admins are listed in `LH_ADMIN_EMAILS`. |
| **Looker** | Users add connections in Settings or in the new-session dialog. API client ID and secret are optional. When given, "Test" signs in to the Looker API and checks that the BigQuery connection exists. Secrets are encrypted with `LH_SECRET_KEY` (or a generated `data/secret.key`). |
| **Logo** | Put the official logo file at `static/assets/brand-logo.svg`. It replaces the neutral mark automatically. |

All settings are listed in `.env.example`.

## How it works

1. **Setup.** The user picks a project, dataset and tables. Cortex pre-ticks the tables the request needs. The table details are saved with the session, and the session only uses those tables.
2. **Plan.** Cortex turns the request into a JSON plan (`app/schemas/plan.py`). `app/services/planner.py` rejects any table or column that isn't in the session and retries once.
3. **Build.** `app/services/lookml/generator.py` writes the LookML. It does this with plain Python code, not the LLM. It applies these practices automatically, without asking the user:
   - every view has a primary key, so totals stay correct across joins
   - large partitioned tables get a default date filter
   - results are cached until the data changes (datagroup)
   - personal-data columns are hidden behind an access grant
   - data tests are included
   - "vs last year" measures, cross-filtering and drill-downs on the dashboard
4. **Check.** Each file is parsed with `lkml`, and every field reference is checked. The cost of a refresh is estimated with a BigQuery dry run. Results are shown to the user in plain language on the Checks tab.

## Project layout

```
app/
  main.py, config.py, db.py, models.py, auth.py, security.py
  api/          meta (BigQuery), sessions (chat, packages, downloads), connections, settings
  schemas/      table details and the plan
  services/
    metadata/   demo.py (sample tables), live.py (BigQuery), pii.py
    cortex/     base.py, mock.py + mock_logic.py (stand-in), real.py (TODO: corporate client), prompts.py
    lookml/     relations.py, generator.py, validate.py, report.py
    planner.py, chat.py, builder.py, packaging.py, looker_api.py
static/         index.html, css/app.css, js/*.js
tests/
```

## Not built yet

- Sending files straight to a Looker project (Git branch and pull request), real numbers in Preview, and schedules and alerts. These need Looker API access (Phase 7 of `docs/IMPLEMENTATION_PLAN.md`).

## Changing the database schema

Edit `app/models.py`, then generate a migration against a fresh embedded PostgreSQL and review it.
The new file appears in `migrations/versions/` because that folder is mounted into the test container.

```powershell
docker compose run --rm --build tests sh -c "alembic upgrade head && alembic revision --autogenerate -m 'describe the change'"
```

The app applies new migrations automatically the next time it starts, in every database mode.
