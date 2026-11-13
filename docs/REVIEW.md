# BusinessPulse local reliability review — 2026-11-13

## Scope and source

The selected `KPI Dashboard` workspace contained an empty Git repository. Source was
copied from the locally downloaded `businesspulse_mvp_starter/businesspulse_mvp` folder.
The downloaded copy and its database were not modified. Its SQLite database,
Python caches, and runtime files were not copied. This review therefore covers
the source now committed in this workspace, not an independently verified running
instance. No public deployment, infrastructure changes, or real customer data were used.

Local environment: Windows, Python 3.10.11, Django 5.2.19, SQLite. Dependencies
were installed from `requirements.txt` into this workspace's ignored `.venv`.
CI specifies Python 3.12; that interpreter and PostgreSQL were not exercised here.

## Architecture and data boundaries

URLs in `businesspulse/urls.py` dispatch to `tracker/views.py`. Views authenticate
the user, obtain their organization, validate forms, call services, and render
Django HTML templates. `tracker/services.py` parses CSVs, saves imports, and
calculates the existing KPI definitions using ORM aggregates. No stack or KPI
definition was changed.

- `Organization`: one workspace per owner, currency, and visible KPI choices.
- `SalesImport`: organization, uploading user, filename, and inserted/duplicate counts.
- `Sale`: organization, import batch, transaction/customer IDs, sale date, revenue,
  and cost. Database constraints enforce unique transaction IDs within an
  organization and nonnegative revenue/cost. An organization/date index supports reports.

Parsing completes before persistence. The save service wraps import history and
sale insertion in one database transaction. Identical existing transactions are
skipped; differing values for an existing ID reject the entire import. Reuploads
still produce an import-history record. Duplicate IDs within one file are rejected.

Dashboard aggregates, history, exports, uploads, and preferences select the
organization through the authenticated owner. Submitted organization/user IDs do
not determine ownership. The same transaction ID can belong to separate organizations.

## Bugs and fixes

1. **Fresh-checkout tests failed:** 14 original tests ran with 10 errors because
   Django's test runner disables DEBUG and the production static-file backend
   expected a generated manifest. Request test classes now override only static
   storage with Django's plain static storage. Application settings remain
   unchanged. Separately, `collectstatic` and a DEBUG=False signup render verified
   the actual manifest backend and hashed stylesheet URL.
2. **Ambiguous headers silently selected a value:** a file containing both
   `revenue` and `amount` could silently discard one of those columns. The parser
   now rejects multiple aliases for a required field even when its canonical
   name is present. Remove the unwanted column and retry. A regression test first
   reproduced the incorrect successful upload, then passed after the fix.
3. **Service accepted an unrelated uploading user:** HTTP views were already
   scoped correctly, but direct service calls could create import history with
   another workspace's user. The service now rejects that mismatch before writing.
   A regression test reproduced the gap and verifies no sales or history are saved.

Only `tracker/services.py`, `tracker/tests/test_imports.py`, and this new review
document differ from the supplied-source checkpoint. Models, migrations, KPI
formulas, templates, dependencies, and development settings are unchanged.

## Verification

Run commands below with `.\.venv\Scripts\python.exe` instead of `python` if the
virtual environment is not activated.

| Command/check | Result |
| --- | --- |
| `python manage.py test` | 25 tests passed, 8.629 seconds; prior to collectstatic, WhiteNoise emitted a non-fatal missing-staticfiles-directory warning |
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| `python manage.py collectstatic --noinput` | 128 files copied, 384 post-processed |
| Signup request with DEBUG=False and real manifest backend | HTTP 200; hashed stylesheet referenced |
| `python -m pip check` | No broken requirements |
| `git diff --check` | Passed |

The suite includes populated-workspace isolation in both directions, forged
organization/user parameters, preference isolation, same-ID use across workspaces,
mixed new/duplicate uploads, conflicting duplicates, invalid-row atomicity,
formula escaping through the export endpoint, malformed/missing/duplicate headers,
invalid dates/IDs/amounts, UTF-8 validation, size rejection, and the 5,000-row boundary.

The sample fixture is uploaded through the HTTP test client and dashboard context
is checked against fixed values: revenue CAD 21,065.00; gross profit CAD 8,875.00;
margin 42.1%; 12 transactions; average CAD 1,755.42; 7 customers. Stored costs
total CAD 12,190.00. These assertions protect the existing definitions.

### Migration repeatability

`tracker/migrations/0001_initial.py` and its package initializer are committed in
the source checkpoint. No additional migration is needed. Against a temporary,
empty SQLite database, the following sequence passed:

1. `migrate --noinput`: all Django and tracker migrations applied.
2. `migrate --noinput`: no migrations to apply.
3. `migrate tracker zero --noinput`: initial tracker migration reversed.
4. `migrate --noinput`: tracker migration reapplied.
5. `migrate --check` and `showmigrations tracker`: no pending migrations; initial migration marked applied.

The temporary database was removed after verification. **Do not reverse the initial
migration against a database containing data you want to keep:** it drops the
tracker tables. An initial verification command used a Windows absolute path with
forward slashes in its SQLite URL and failed to open the temporary database; the
successful rerun used a relative workspace URL. This was a verification-path
issue, not migration drift.

## Deployment warnings and remaining issues

`python manage.py check --deploy` under current development settings reported:

- `security.W004`: HSTS duration not enabled.
- `security.W008`: HTTPS redirect not enabled.
- `security.W012`: session cookie not secure-only.
- `security.W016`: CSRF cookie not secure-only.
- `security.W018`: DEBUG is on.

These settings were not changed. Existing configuration enables those controls
when DEBUG is disabled, but actual HTTPS/proxy behavior, host configuration, and
secret provisioning still require deployment verification. The static-manifest
smoke check is not a production security approval.

Open findings, distinct from passing local tests:

- **Concurrent uploads:** the lookup-then-insert sequence can race. Database
  uniqueness prevents duplicate totals, but a competing request may receive an
  unhandled IntegrityError (or a SQLite lock error). Sequential idempotency is
  verified; concurrent behavior needs a PostgreSQL regression test and explicit
  serialization/retry handling before multi-worker use.
- **Database-level ownership is incomplete:** `Sale.clean()` checks import-batch
  organization consistency, but ordinary ORM saves/bulk writes do not call it.
  The current import service builds consistent relationships; arbitrary scripts
  or future write paths must not assume the database enforces this relationship.
- **Admin is privileged across workspaces:** staff with model permissions can
  access multiple organizations. Do not grant customer accounts admin access.
- **Upload transport limits:** parsing enforces 2 MB, but Django can spool larger
  multipart uploads to temporary disk before form validation. Raw files are not
  retained by application code; transport-level request limits still need review.
- **Deployment build script:** `build.sh` is committed with mode 100644 although
  the runbook invokes `./build.sh`. Verify executable mode and Linux line endings
  before deploying. No hosting/build configuration was changed in this review.
- **Operational launch gates remain:** PostgreSQL/production CI verification,
  concurrent-import handling, verified backups/restores, monitoring, account
  verification/recovery, abuse controls, privacy/retention/deletion processes,
  and an operational rollback plan. Requirements use version ranges rather than
  a locked dependency set. No dependency vulnerability audit was performed.

Browser interaction was not performed; signup, upload, dashboard, preferences,
and export scenarios were exercised through Django's HTTP test client. No existing
account or real client database was inspected. Continue with synthetic data only.
