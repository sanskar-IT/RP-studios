# Self-hosted deployment

## Docker Compose

The default deployment runs PostgreSQL with pgvector, applies Alembic migrations, starts the FastAPI service, and serves the built React studio through nginx:

```text
docker compose up --build
```

Open `http://localhost:5173`. PostgreSQL is exposed on `5432` for local administration; the API is available at `http://localhost:8000`. The API container runs `alembic upgrade head` before starting, which applies the static baseline, the vertical-slice columns, and the production constraints.

## Local development

Start PostgreSQL, install Python and Node dependencies, then run the API and web process separately:

```text
python -m pip install -e ".[dev]"
alembic upgrade head
uvicorn apps.api.app.main:app --reload
npm install
npm run web:dev
```

The default provider is the deterministic heuristic provider. To use a compatible endpoint, configure the environment variables described in `docs/PROVIDERS.md`.

## Configuration

Copy `.env.example` to `.env` for local development. Keep provider keys in the environment or a secure local secret mechanism. Do not place credentials in project exports, prompts, or source-controlled files.

The deterministic suite does not require a model provider. To run the opt-in live smoke test, configure the provider variables and run `pytest -m smoke`; it is skipped when the endpoint is not configured.

`NARRATIVE_AUTO_CREATE_SCHEMA` defaults to `false`. The API does not create tables on startup, and `alembic upgrade head` is the only supported way to build the schema. `apps.api.app.db.init_db` remains available for a throwaway local database when the flag is set explicitly.

## Migration chain

The chain is explicit and reviewable, and it no longer derives the deployed schema from the models:

| Revision | Content |
| --- | --- |
| `0001_initial` | Static baseline: 22 tables, their indexes, foreign keys, and unique constraints, plus `CREATE EXTENSION IF NOT EXISTS vector` on PostgreSQL |
| `0002_vertical_slice` | Adds `scenes.staging_revision`, `scenes.approved_staging_revision`, and `generations.checkpoint_node_id`, each only when the column is absent |
| `0003_production_constraints` | Adds `(timeline_id, sequence)` uniqueness for `timeline_nodes` and `events` and a partial unique index that allows one current scene per timeline |
| `0004_context_reliability` | Adds memory scope/activity columns, commitment timeline columns, and generation trace columns, each only when absent; backfills legacy memory scope from class |
| `0005_narrative_director` | Creates `director_plans` and its indexes and foreign keys; adds `projects.authority_mode` and `scenes.authority_mode`, each only when absent; extends `director_intents` with the interpreted-intent and Director columns |

The guards in `0002_vertical_slice`, `0003_production_constraints`,
`0004_context_reliability`, and `0005_narrative_director` exist for databases
that were created before the static baseline, through `Base.metadata.create_all`.
Because those revisions inspect the live schema, `alembic upgrade head --sql`
cannot generate an offline script for them; generate and review the DDL against a
real database instead.

Existing rows take the defaults rather than requiring a backfill: a project or
scene without an explicit `authority_mode` resolves to `director_assisted`, which
is the mode that does not gate ordinary turns. A run upgraded mid-flight therefore
behaves as it did before the Director rather than stopping to ask for approval
on every direction.

SQLite is supported for local development and for the deterministic suite. It does not enforce foreign keys unless asked, so the application and the test engine both enable `PRAGMA foreign_keys=ON`.

## Opt-in PostgreSQL suite

The deterministic suite runs on SQLite only. Database behavior that SQLite does not reproduce, such as immediate foreign key enforcement, pgvector types, and partial unique indexes, is covered by a separate suite that runs against a real PostgreSQL database.

Point `NARRATIVE_TEST_DATABASE_URL` at a disposable PostgreSQL database and run:

```text
pytest -m postgres
```

With the Compose database already running:

```text
docker compose up -d postgres
```

On Windows PowerShell:

```powershell
$env:NARRATIVE_TEST_DATABASE_URL = "postgresql+psycopg://narrative:narrative@localhost:5432/narrative_test"
pytest -m postgres
```

On macOS or Linux:

```text
export NARRATIVE_TEST_DATABASE_URL="postgresql+psycopg://narrative:narrative@localhost:5432/narrative_test"
pytest -m postgres
```

The suite:

- skips without an error when the variable is unset, is not a `postgresql` URL, or the database cannot be reached;
- applies `alembic upgrade head` to the target database once per session, so the database must exist and must be one you are willing to write to;
- wraps every test in a transaction that is rolled back, so no test data survives the session;
- leaves the schema in place when the session ends, so the migration test can compare it with the models.

The tests that require `NARRATIVE_TEST_DATABASE_URL` are exactly the tests in
`tests/test_postgres.py` (marked `pytest.mark.postgres`): the migration
idempotency and parity tests, core persistence, JSON round-trip, foreign keys,
rollback, timeline fork, production constraints, memory persistence, the
reliability-column round-trip, and the fork scope/linkage test. The SQLite
suite is complete without them.

The database must be created before the suite runs. The suite never creates or drops the database itself, and it never drops the schema.
