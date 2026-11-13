# Deploying BusinessPulse to Render (synthetic beta first)

## Preflight: locally

- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py test`
- `python manage.py runserver` and manually test signup, login, upload, KPI settings and CSV export.
- Run `python manage.py check --deploy` with `DJANGO_DEBUG=0`, proper `DJANGO_SECRET_KEY` and allowed hosts. Investigate all warnings; some may be environment-specific.
- Push to a **private GitHub repository** with CI green; do not commit `.env` or SQLite.

## Render deployment (managed infrastructure, no AWS required for v0.1)

1. In Render, create a **managed PostgreSQL database**, selecting a paid persistent plan for dependable use. Choose the same region as the app and record the **internal** database URL. Confirm your backup and retention plan.
2. New > Web Service > connect the GitHub repository; use the Python runtime and main branch.
3. Build Command: `./build.sh` (installs dependencies, collects static files, and migrates the database).
4. Start Command: `gunicorn businesspulse.wsgi:application --bind 0.0.0.0:$PORT`.
5. In environment variables set:
   - `DJANGO_DEBUG=0`
   - `DJANGO_SECRET_KEY=` a fresh securely-generated random secret (never print or commit it)
   - `DATABASE_URL=` the internal managed PostgreSQL connection string
   - `DJANGO_ALLOWED_HOSTS=` the actual app hostname, e.g. `businesspulse-sandbox.onrender.com`
   - `DJANGO_CSRF_TRUSTED_ORIGINS=` the corresponding HTTPS origin, e.g. `https://businesspulse-sandbox.onrender.com`
6. Deploy. Verify `/health/`, login/signup pages and static CSS over HTTPS. Import sample CSV.
7. Redeploy with a small non-schema change, check saved sale rows remain intact and test a rollback procedure.
8. Monitor logs. Configure alerts, backup restoration tests, and dependency updates before inviting users.

**Caution:** Migrations in `build.sh` simplify a single-instance beta; they are not a zero-downtime migration strategy. Before scaling, move database changes into a controlled release step and add backward-compatible schema migration practices. Render plans/features can change: verify pricing, availability and access to backups before selecting a plan.

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

Official guidance: https://render.com/docs/deploy-django and https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/
