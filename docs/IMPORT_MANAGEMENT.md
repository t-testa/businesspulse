# Import management and corrections — v0.4

## Request path and ownership

The existing Django/template frontend now has `/imports/` (a combined 25-entry
paginated sales/expense list) and `/imports/<dataset>/<id>/` (inspect and confirm).
The list shows filename, date, dataset, original inserted/skipped counts and status.
The inspect page shows every record created by that import, including inactive
historical rows, plus current vs. after-reversal all-time financial totals.
At most 5,000 rows can be created by the existing CSV upload limit. No raw file is
stored or reconstructed; the page reads validated database fields.

Both routes require login and resolve the organization from the authenticated
owner. Dataset is an allowlisted route value. Every batch and row query is scoped
to that organization; unknown/foreign IDs return 404. Numeric IDs can overlap
between sales and expense tables without sharing ownership. History is not inferred
from CSV filenames or matching business IDs: the existing import_batch foreign key
identifies the creator. A later duplicate-only upload owns no transactions.

GET preview runs in a short transaction holding the same organization row lock as
writers, making its records, financial comparison and revision coherent. It performs
no writes and releases the lock before HTML rendering. Reports use the existing
calculation function with the target batch excluded for the simulated after state.
Amounts are not changed or temporarily deleted to compute a preview. The comparison
is explicitly all time; reversal affects every dashboard period and sales export.

## Reversal and stale confirmations

The owner supplies a reason (1–1,000 trimmed characters), checks explicit confirmation,
and sends a CSRF-protected POST. The signed, timestamped token binds owner, organization,
dataset, batch ID and data_revision. It expires after 30 minutes. The revision advances
inside the transaction on every successful import (including duplicate-only uploads)
and every first reversal. Failed writes roll it back; repeat reversals do not advance it.
This conservative rule can require reconfirming after a duplicate-only import even
though amounts did not change, but avoids ambiguous stale state.

The service locks the organization, verifies ownership and token, and returns a no-op
if that batch was already reversed. For a not-yet-reversed batch it rejects a changed
revision, deactivates only its active owned rows, records the actor/time/reason, and
increments the revision in one transaction. An error rolls back all of these changes.
Stale/invalid confirmations return 409 with a refreshed preview and an unchecked form.
Missing reason/confirmation returns 400. Forged tenant IDs never select another owner.
A valid repeated confirmation preserves the first actor, timestamp and reason, and
cannot deactivate records created by a later corrected upload.

PostgreSQL READ COMMITTED and the shared organization lock serialize imports and
reversals, including IDs with no current active row. Reversal first lets the waiting
corrected import create a new active version. Import first changes the revision,
forcing the waiting reversal to obtain fresh confirmation. Two reversals of the
same batch produce one reversal and one no-op. A failed reversal leaves a waiting
identical upload as a duplicate. SQLite does not implement these row locks and is
for sequential development only.

## Schema and reporting

Migration `0004_import_management` adds:

- Sale.is_active and Expense.is_active, default True for every existing row.
- reversed_at, reversed_by (protected user foreign key) and reversal_reason on
  SalesImport and ExpenseImport. Status is derived from reversed_at, not another flag.
- Organization.data_revision, initially zero.

It replaces the two unconditional organization/business-ID unique constraints with
partial unique constraints where is_active=True. Thus there can be many historical
inactive versions but at most one active version of an ID per organization/dataset.
This is an additive data-preserving schema evolution with deliberate constraint
replacement; it is not an exclusively add-column migration. Existing values,
primary keys, import ownership, counters and mappings remain unchanged. No financial
records are deleted or rewritten to create corrected values.

Imports compare only active versions. Dashboards, all six original sales KPIs,
expense totals/categories/monthly trends and sales CSV export filter is_active=True.
Formula-safe CSV handling is unchanged. Explicit filters keep audit queries able
to see history; future financial queries must also opt into active-only filtering.
There is no new expense-export feature in this milestone.

Expense availability now depends on active expense rows anywhere in the workspace,
not historical imports. Reversing all expenses returns Not imported and N/A operating
estimates even if an unreversed duplicate-only history entry remains. A zero-valued
active row is genuine stored data; active rows outside the period allow a qualified
period zero. This does not establish accounting completeness.

## Migration/release tradeoffs

The migration is atomic on both supported backends. Index changes may lock tables.
Use a controlled release: pause writes/drain old workers, migrate, then serve only
v0.4 code. The existing build-time migration does not itself coordinate that release.
Old v0.3 code would count inactive rows and would not update confirmation revisions.
Do not run it after reversals or treat a code-only rollback as safe.

The last migration operation has a reverse guard, executed first on rollback. It
blocks rollback if inactive records or reversal metadata exist, preserving the audit
columns and avoiding restoration of an incompatible uniqueness rule. Before feature
use, rollback/reapply is tested. After use, retain schema and active filtering and
prefer a forward fix. Do not delete old versions to force rollback. An approved
backup recovery is a separate decision because it can lose subsequent activity.

Django financial and organization admin screens are read-only, preventing edit/delete
bypasses of revision and audit logic. Owner-facing operations remain the supported
write path. Direct database access, shell scripts and custom code are trusted operator
capabilities, not protected against by application-level audit. This is not a tamper-proof
accounting ledger. Reasons, filenames and identifiers are escaped in HTML and should
not contain real sensitive data during the synthetic beta.

## File-by-file change log

| File | Purpose / verification |
| --- | --- |
| `tracker/models.py` | Active state, reversal audit and revision fields; partial uniqueness tests |
| `tracker/migrations/0004_import_management.py` | Data-preserving upgrade and audit-loss rollback guard; migration tests on both databases |
| `tracker/import_management.py` | Coherent read-only previews, signed confirmations, scoped atomic reversals; authorization, stale/expiry, rollback and concurrency tests |
| `tracker/services.py` | Active-only duplicate checks and financial queries; shared before/after calculation and revision increments; existing KPI and new acceptance tests |
| `tracker/forms.py` | Bounded reason, signed token and required confirmation; direct forged/incomplete POST tests |
| `tracker/views.py` | Scoped combined pagination, inspect/POST workflow, active-only export; request/browser tests |
| `businesspulse/urls.py` | Import list/detail routes; ownership, methods and login tests |
| `tracker/admin.py` | Read-only organizations/financial records to prevent bypasses; permission regression test |
| `tracker/templates/tracker/imports.html` | Combined management list/status and pagination |
| `tracker/templates/tracker/import_detail.html` | Exact records, all-time financial impact, audit and explicit confirmation |
| `tracker/templates/tracker/base.html` | Imports navigation |
| `tracker/templates/tracker/dashboard.html` | Recent sales history links and status |
| `tracker/templates/tracker/expense_report.html` | Recent expense links/status and active-data availability wording |
| `tracker/tests/test_import_management.py` | 13 tests for acceptance, sales/expense reversal, corrected IDs, duplicates, ownership, stale tokens, audit, rollback, constraints and admin |
| `tracker/tests/test_reversal_concurrency.py` | Four PostgreSQL tests, each exercising both datasets and actual blocking in separate connections |
| `tracker/tests/test_import_management_migrations.py` | Upgrade/default/preservation/repeat/unused rollback tests and used-schema rollback rejection |
| `tracker/tests/test_expense_migrations.py` | Restore latest schema after testing older migrations, preventing cross-test schema leakage |
| `tracker/tests/test_browser.py` | Chromium inspect/confirm/reversed audit and missing-expense scenario; existing upload scenarios retained |
| `README.md` | Owner instructions, corrected reimport and rollback warning |
| `docs/PRODUCT.md` | v0.4 grain, active coverage and acceptance rules |
| `docs/DEPLOYMENT.md` | Controlled release, index-lock implications and safe recovery constraints |
| `docs/IMPORT_MANAGEMENT.md` | Architecture, learning notes, tradeoffs and exact results |

## Verification — 2026-11-17

Development used a new PostgreSQL 17 container `businesspulse-import-management-postgres`
on loopback port 55435 and database `businesspulse_import_management`. SQLite used
an in-memory database. Only synthetic data was used; hosted/restored databases and
existing local databases were not altered. Local Python 3.10.11/Django 5.2.19;
GitHub Actions uses Python 3.12 and the existing SQLite/PostgreSQL matrix.

| Check | Result |
| --- | --- |
| Baseline `python manage.py test --noinput` before changes | 78 discovered, 67 passed, 11 browser/PostgreSQL skips |
| Final SQLite `python manage.py test --noinput`, RUN_BROWSER_TESTS=1 | 98 discovered; 86 passed, 12 PostgreSQL-only skips |
| Final PostgreSQL `python manage.py test --noinput`, RUN_BROWSER_TESTS=1 | All 98 passed, including 4 Chromium tests and 12 concurrency tests |
| `python manage.py check`, both backends | No issues |
| `python manage.py makemigrations --check --dry-run`, both | No changes detected |
| Disposable PostgreSQL `migrate --noinput`, then repeat and `migrate --check` | Initial migrations passed; repeat no-op; check exit 0 |
| `node --test tracker/tests/js/csv-preview.test.cjs` | All 6 passed; no new JavaScript needed |
| `python -m pip check` | No broken requirements |
| `git diff --check` | Passed |
| Production-mode `check --deploy` with ephemeral secret | Existing W005/W021 HSTS subdomain/preload warnings only; settings unchanged |

The required Demo Company fixture retained 12 active sales, preserved original and
duplicate expense histories, made all three original expenses inactive, returned
expenses to Not imported, and left mappingtest's CAD 750 untouched. Reimport produced
three active and three inactive expense records without changing the first audit.
All six original sales KPI regression tests remain green. Browser tests reproduce
inspection, checkbox confirmation, reversal/audit display and missing-data reporting.

During implementation, the full suite caught a Windows encoding error introduced
when adding a separator to existing templates. The files were normalized to UTF-8
and line endings repaired; both full suites subsequently passed. Expected rejection
logs and deliberately injected metadata-write failures are test assertions, not
remaining failures. The existing CI also verifies Linux build/Gunicorn commands;
review its status on PR #4 before any approval to merge or deploy.

## What to learn and what remains

A business ID identifies the current logical transaction; a database row ID identifies
one retained version. Import provenance is ownership, while duplicate counters are
observations. A transaction guarantees all-or-nothing writes; a row lock also orders
competing requests; a revision token prevents applying an old human decision to a
new financial picture. All three address different failure modes.

Soft deactivation preserves evidence but requires every financial read to filter
active rows. Partial uniqueness enables corrections without losing history, at the
cost of stricter release/rollback planning and growing audit storage. This release
supports whole-batch reversal, not editing individual rows or reactivating history.
Large histories, audit retention policy, rate limiting, account recovery, operational
monitoring and privacy controls remain beta launch work. Real Render acceptance,
backup/persistence verification and deployment require owner approval. Cross-COGS
classification and completeness risks from v0.3 remain unchanged.
