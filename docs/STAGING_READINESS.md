# Milestone 4: staging readiness verification

Date: 2026-11-13. Scope: synthetic data, local Docker and CI only. No Render
service/database was created, no deployment occurred, and no merge was performed.

## Branch and runtime

Fetched `origin`, fast-forwarded local `main` (already at `aa7dba1`), and created
`chore/staging-readiness` with the checkpoint required by AGENTS.md. The PR targets
`main` in [t-testa/businesspulse](https://github.com/t-testa/businesspulse).

Verified environments:

- Windows: Python 3.10.11, Django 5.2.19, SQLite and local PostgreSQL 17.12.
- Local Linux container: Python 3.12.16, Django 5.2.19, PostgreSQL 17.12,
  Gunicorn 23.0.0, WhiteNoise 6.12.0, psycopg 3.3.9.
- CI configuration: Python 3.12, independent SQLite/PostgreSQL 17 jobs, plus a
  PostgreSQL build/start smoke check. Consult the PR checks for hosted CI results;
  the local results below are separately reproducible evidence.

Docker Desktop was installed but initially stopped. It was started using its
existing installation. Sandbox access initially blocked Docker startup and host
loopback database access; rerunning those commands with the permitted local
execution access succeeded. No software reinstall or OS security-policy change
was needed. PostgreSQL is exposed only at `127.0.0.1:55432`.

## Engineering changes and learning points

| Files | Change and reason | Verification |
| --- | --- | --- |
| `tracker/services.py` | Lock the organization row inside the existing atomic transaction before checking sale IDs. A transaction ensures all-or-nothing writes but does not by itself prevent two requests reading the same pre-insert state. Locking an existing organization also covers transaction IDs that do not exist yet. | Real overlapping PostgreSQL connections; identical uploads skip, conflicts reject atomically, separate workspaces proceed independently |
| `tracker/tests/test_concurrency.py` | Three TransactionTestCase regressions use separate connections and `pg_blocking_pids` to prove actual overlap rather than relying on an arbitrary sleep. | Before fix: two uniqueness errors and one pass. After fix: three passes on PostgreSQL; explicitly skipped on SQLite |
| `compose.yaml` | Reproducible local PostgreSQL 17 with health check, loopback binding and a named persistent volume. Public local-only credentials are intentionally disposable. | Fresh migrations, repeated migrations, test DB creation/destruction |
| `.github/workflows/ci.yml` | Separate SQLite/PostgreSQL jobs, migration/check/test steps and Linux build/start smoke. Least-privilege read permission and no persisted checkout credentials. | Same commands run locally; hosted results appear on the PR |
| `.python-version` | Select Python 3.12 on Render, aligning the runtime with CI instead of accepting a changing platform default. | Local Linux Python 3.12 verification |
| `build.sh`, `.gitattributes` | Git mode 100755, LF shell scripts, and fail-fast shell behavior. Windows filesystem permissions alone do not establish executable permission on Linux. | Staged Git mode inspected; actual `./build.sh` executed on Linux |
| `Procfile` | Gunicorn logs to process output; access format includes method/path/status/duration without query strings or client IPs. | Exact command launched by the smoke script |
| `businesspulse/settings.py` | Production startup refuses missing/weak/example secrets, missing/wildcard hosts, and non-PostgreSQL databases; Django logs to process output. SQLite defaults are unchanged in development. | Six isolated settings tests and production-mode HTTP smoke |
| `tracker/tests/test_settings.py` | Import settings in fresh subprocesses with isolated environment variables, so production checks are not masked by Django's test runner overriding DEBUG. | Positive and negative configuration cases on both databases |
| `scripts/verify_staging.sh`, `scripts/smoke_staging.py` | Reusable Linux build/start verification using a generated ephemeral secret and a disposable database. No deployment API calls. | Health, redirect, signup, security headers/cookie, hashed CSS, CSRF rejection and invalid-host rejection |
| `README.md`, `.env.example`, `docs/DEPLOYMENT.md`, this report | Existing public repository, Docker commands, production assumptions, exact results and approval gates. | Instructions exercised; Render commands checked against official documentation |

Actions were updated from checkout v4/setup-python v5 to the current verified
releases [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)
and [setup-python v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0),
pinned to their full Git commit SHAs. Version comments explain each pin.

No model/schema or KPI definition changed. Existing baseline KPI tests remain.

## Exact local results

Commands used the local virtual environment's Python, or Python inside the Linux
container. The Windows PostgreSQL environment was:

```powershell
$env:DATABASE_URL = 'postgresql://businesspulse:local-synthetic-only@127.0.0.1:55432/businesspulse'
```

The container used the same synthetic credentials with host `postgres:5432` on
the private local Compose network. It received only staged Git source files;
local `.env`, SQLite database, virtual environment, and private files were excluded.

| Command/scenario | Result |
| --- | --- |
| `docker compose up -d --wait postgres` | Healthy PostgreSQL 17.12 |
| Initial `python manage.py migrate --noinput` on PostgreSQL | All 19 Django/tracker migrations applied successfully |
| Repeat `migrate --noinput`; `migrate --check` | No pending migrations |
| `python manage.py check` on SQLite and PostgreSQL | No issues |
| `python manage.py makemigrations --check --dry-run` on both | No changes detected |
| Before fix: `python manage.py test tracker.tests.test_concurrency --noinput` | 3 tests: 2 errors from unique-constraint violations, 1 pass |
| After fix: full Windows/PostgreSQL suite | 34 tests passed (11.587 seconds) |
| Full Windows/SQLite suite | 34 discovered: 31 passed, 3 PostgreSQL-only tests skipped (10.737 seconds) |
| Full Linux/Python 3.12/PostgreSQL suite | 34 tests passed (5.095 seconds) |
| `python -m pip check` on Windows and Linux | No broken requirements |
| `./build.sh` on Linux | Dependencies installed, 128 static files copied / 384 post-processed, migrations current |
| `gunicorn businesspulse.wsgi:application --check-config` | Passed |
| Exact Procfile Gunicorn command on local port 8001 | Started, passed HTTP smoke, shut down cleanly |
| Production `python manage.py check --deploy --fail-level ERROR` | Exit 0; W005 and W021 only, intentionally retained |
| `git diff --check` / staged executable mode | Passed; build.sh mode 100755 |

The initial settings regression run exposed an empty-DATABASE_URL KeyError in the
new startup guard. That was fixed to raise a clear configuration error, then the
full suites above passed. A final additional whitespace-only host case strengthens
the same configuration test without changing the number of tests.

The HTTP smoke confirms `/health/` 200, plain-HTTP 301 to HTTPS, signup 200,
hashed CSS 200, secure CSRF cookie, HSTS and frame denial, CSRF-invalid POST 403,
and untrusted Host 400. The intentional 403/400 checks generate expected warning/
error log entries. This emulates a trusted proxy locally; it does not test an
actual Render endpoint or certificate.

## Remaining staging acceptance and risks

- W005 (HSTS subdomains) and W021 (preload) remain visible and intentional until
  domain-wide HTTPS policy has been approved. Do not suppress them or enable
  browser preload merely to make the command silent.
- Real Render HTTPS/proxy behavior, managed DB connectivity/TLS, restart/redeploy
  persistence, backups/restores, log retention, alerts, and rollback need approval
  and verification on the eventual staging service. See DEPLOYMENT.md.
- PostgreSQL READ COMMITTED and all imports passing through the service are
  assumptions of the locking fix. SQLite is sequential development only; direct
  admin/ORM writes bypass the import protocol. Long-running same-workspace imports
  can queue; no broader worker-scaling or load test was performed.
- Dependency ranges still permit patch/minor variation. A lockfile and dependency
  vulnerability audit are separate future work; no vulnerability-free claim is made.
- Public local/CI credentials are not staging secrets. No real customer data or
  production credential was used. Do not reuse these credentials on a server.
- Paid plans, public deployment, merging the PR, and real data remain unapproved.

The readiness changes resolve the concurrency, PostgreSQL-verification and build
mode findings in the earlier historical REVIEW.md; that document remains a record
of the previous milestone rather than the current status.
