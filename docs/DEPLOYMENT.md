# Deploying BusinessPulse to Render (synthetic beta first)

**Preparation only:** creating any Render service/database, enabling public deployment,
or selecting a paid plan requires the owner's approval. This runbook does not
authorize deployment. Repository: [t-testa/businesspulse](https://github.com/t-testa/businesspulse).
Disable automatic deployments when staging is eventually created until the owner
has approved the deployment workflow. A public staging URL is not private access control.

## Preflight: locally

- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py test`
- `python manage.py runserver` and manually test signup, login, upload, KPI settings and CSV export.
- Run `python manage.py check --deploy` with `DJANGO_DEBUG=0`, proper `DJANGO_SECRET_KEY` and allowed hosts. Investigate all warnings; some may be environment-specific.
- Open a pull request in the existing public repository and require both SQLite
  and PostgreSQL CI jobs to pass; do not commit `.env`, databases, or real exports.
- Run the PostgreSQL setup in README.md. On Linux with a disposable PostgreSQL
  `DATABASE_URL`, `bash scripts/verify_staging.sh` runs the actual build/start
  commands, creates an ephemeral test secret, and exercises the local HTTP service.
  It migrates that database, so never point this verification script at staging or
  production. It uses port 8001, then stops its Gunicorn process automatically.

## Render deployment (managed infrastructure, no AWS required for v0.1)

1. In Render, create a **managed PostgreSQL database**, selecting a paid persistent plan for dependable use. Choose the same region as the app and record the **internal** database URL. Confirm your backup and retention plan.
2. New > Web Service > connect `t-testa/businesspulse`; use the Python runtime and
   the reviewed/approved commit on main. `.python-version` selects the latest
   Python 3.12 patch, matching the CI major/minor. Do not override it with a
   different `PYTHON_VERSION` without testing that version.
3. Build Command: `./build.sh` (installs dependencies, collects static files, and migrates the database).
4. Start Command (the command after `web:` in Procfile):

   ```sh
   gunicorn businesspulse.wsgi:application --bind 0.0.0.0:${PORT:-8000} --access-logfile - --error-logfile - --access-logformat '%(m)s %(U)s %(s)s %(L)s'
   ```

   Set the health-check path to `/health/`. It is a process-liveness endpoint,
   not a database readiness check. Verify a database-backed signup/login/upload
   as part of the staging acceptance test.
5. In environment variables set:
   - `DJANGO_DEBUG=0`
   - `DJANGO_SECRET_KEY=` a fresh securely-generated random secret (never print or commit it)
   - `DATABASE_URL=` the internal managed PostgreSQL connection string
   - `DJANGO_ALLOWED_HOSTS=` the actual app hostname, e.g. `businesspulse-sandbox.onrender.com`
   - `DJANGO_CSRF_TRUSTED_ORIGINS=` the corresponding HTTPS origin, e.g. `https://businesspulse-sandbox.onrender.com`
   - Optionally `DB_SSL_REQUIRE=1` to require database TLS. Internal Render
     connections support optional TLS; external Render database connections
     require it. Use the internal URL for the same-region staging service.
     `sslmode=require` encrypts the connection but does not verify the server's
     identity like `verify-full` with an appropriate CA configuration does.
6. Deploy. Verify `/health/`, login/signup pages and static CSS over HTTPS. Import sample CSV.
7. Redeploy with a small non-schema change, check saved sale rows remain intact and test a rollback procedure.
8. Monitor logs. Configure alerts, backup restoration tests, and dependency updates before inviting users.

**Caution:** Migrations in `build.sh` simplify a single-instance beta; they are not a zero-downtime migration strategy. Before scaling, move database changes into a controlled release step and add backward-compatible schema migration practices. Render plans/features can change: verify pricing, availability and access to backups before selecting a plan.

## Configuration and operating assumptions

- With `DJANGO_DEBUG=0`, startup requires PostgreSQL, explicit allowed hosts (or
  Render's supplied hostname), and a secret at least 50 characters long with
  sufficient character variety. Example/low-complexity keys are rejected. These
  checks catch mistakes; generate the key randomly and store it only as a secret,
  never in Git, build output, screenshots, or the public PR. `.env.example` is
  documentation and Django does not load `.env` automatically.
- HTTPS redirects, secure session/CSRF cookies, a one-hour HSTS policy, nosniff,
  and frame denial are enabled in production mode. The app trusts Render's
  `X-Forwarded-Proto` header: it must sit behind a proxy that strips/replaces
  client-supplied forwarding headers. Do not expose Gunicorn directly to clients.
- `check --deploy` intentionally reports W005 and W021: HSTS subdomain coverage
  and browser preloading remain off until domain ownership and every affected
  subdomain's HTTPS behavior have been checked. They are not silenced, and they
  are not reasons to enable irreversible domain-wide policy prematurely.
- Persist application data in managed PostgreSQL, never the web service's local
  filesystem. Local Docker volumes are for development only. Confirm the chosen
  staging plan's retention, backup capabilities, and expiry behavior before paying
  or inviting testers. Restore a backup into a separate database and validate
  counts/KPIs before accepting real data.
- Django warnings/errors and Gunicorn process logs go to process output. Access
  logs record method, path, status, and duration, omitting query strings, client
  IPs, cookies, and bodies. Error traces can still contain sensitive context:
  restrict log access and retention, and never add CSV/credential logging.
  Alerting and log-retention policy still need owner configuration.
- PostgreSQL imports lock one organization row before checking transaction IDs.
  Concurrent uploads for that organization wait and then see committed data;
  different organizations can import independently. Keep database isolation at
  Django's default READ COMMITTED, and route all application imports through
  this service. Direct admin/scripts are outside that serialization protocol.
  SQLite does not supply these row locks and is only for sequential development.
- No schema changes are introduced by this milestone. A code rollback uses the
  last reviewed commit, without dropping tables. Future schema/data migrations
  need their own reversible rollout plan; never reverse the initial migration
  to roll back an application release containing customer data.

## Approval still needed before launch

Confirm resource names, region, PostgreSQL version (17 is tested here), plan/cost,
secret ownership, manual deployment controls, backup/restore procedure, and alert
owner. Then validate HTTPS forms, two-account isolation, duplicates, concurrent
uploads, restart/redeploy persistence, and rollback on the real staging service.
Local Docker/CI verification cannot prove Render-specific network, TLS, persistence,
or backup behavior. Keep all staging datasets synthetic.

## Environment separation

- Local SQLite (synthetic development) is acceptable, but **never use SQLite as the persistence layer on ephemeral cloud hosting**.
- Use separate development and production credentials, databases and service accounts.
- Do not commit production secrets or client CSVs to Git.
- A public URL is not a production security approval; beta-test synthetic data until privacy, recovery and abuse safeguards are implemented.

## First deployment checklist

- [ ] CI green; no pending migrations
- [ ] HTTPS works; DEBUG off; strong secret outside repository
- [ ] Static CSS loads; POST forms work under HTTPS with CSRF
- [ ] Database survives app restart/redeploy
- [ ] Two test users cannot view each other's transactions
- [ ] Duplicate upload does not affect totals
- [ ] Export does not contain another company's data
- [ ] Managed DB backups enabled and a restore rehearsed
- [ ] Monitoring / alert path checked
- [ ] Documented process for reverting a bad deployment

Official references: [Render Django](https://render.com/docs/deploy-django),
[Render PostgreSQL connections](https://render.com/docs/postgresql-creating-connecting),
[Render Python versions](https://render.com/docs/python-version), and
[Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).
