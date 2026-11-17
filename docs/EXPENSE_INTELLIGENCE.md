# Expense intelligence — v0.3

## Architecture and data grain

The existing Django templates, minimal JavaScript, PostgreSQL/SQLite and authentication
remain in place. Sales still represent completed sales transactions. An Expense is
one operating expense transaction, uniquely identified by `(organization, expense_id)`.
It stores date, required category, two-decimal nonnegative amount and its import batch.
ExpenseImport records the owner, filename, timestamp, inserted count and skipped count.
ExpenseColumnMapping holds only organization-scoped names and source header references.

Separate expense tables prevent mixing operating costs with sales direct costs or
breaking existing sales profile uniqueness. Migration `0003_expense_intelligence`
only creates these three tables, their constraints and the organization/date index.
No existing schema/data is removed or rewritten. Identical IDs in different datasets
or organizations are independent. ID comparison and category grouping are case-sensitive.

Browser selection -> local ten-row preview -> four editable mappings -> one CSRF-protected
POST to `/expenses/upload/` -> shared UploadForm and bounded CSV parser -> organization
row lock -> duplicate/conflict check -> atomic expense/history/optional-profile commit.
The route selects the dataset and the signed-in owner selects the organization; neither
can be overridden by a submitted field. Saved mapping IDs resolve against the appropriate
dataset's organization queryset, even when sales and expense tables have the same numeric PK.

Both import services use the same organization lock before reading IDs. PostgreSQL READ
COMMITTED therefore makes a concurrent uploader wait and re-read committed records, including
IDs absent before the first import. Different organizations proceed independently. Uniqueness
and nonnegative checks provide database backstops. SQLite supports sequential development,
not this concurrent-import guarantee. Direct ORM/admin writes must not bypass import services;
model clean() also detects a mismatched batch organization, but bulk operations do not call it.

Every original row is checked before any write: 2 MB, 5,000 rows, 100 columns, 200-character
headers, UTF-8, CSV structure, exact mapping keys, distinct existing source headers, required
IDs/categories up to 100 characters, valid YYYY-MM-DD dates, nonnegative finite amounts below
1 trillion with at most two decimals. Duplicate IDs within one file are rejected, even if
identical; an identical already-stored ID is skipped. A conflict in date, category or amount
rolls back the whole upload. Mapping-write failures also roll back records/history.
The existing memory-only upload handler prevents Django temporary-file persistence.

## KPI interpretation

Sales and expenses are aggregated separately, avoiding join multiplication. Both querysets
use one period helper: inclusive last 30/90 days or Jan 1 through today; all time includes all
stored dates, preserving existing sales behavior. All six sales definitions/preferences remain.

- Operating expenses: sum of amount; categories group that same filtered dataset.
- Estimated operating result: sales gross profit minus operating expenses.
- Estimated operating margin: result divided by revenue times 100, displayed to one decimal.
  Negative results/margins are valid. Zero revenue gives N/A.
- Monthly trend: last 12 populated expense months in the selected period. Omitted months are
  explicitly not assumed to be zero; amounts remain visible beside the simple bars.

No expense import history means Not imported and unavailable combined estimates. A valid
zero-valued expense counts as data. An imported workspace with no expense rows in the period
shows a qualified zero. Imported data does not certify completeness. Filters and history stay
visible without sales in the period, supporting expense-only workspaces and recovery from an
empty date filter. Expense cards are a separate fixed section, leaving sales preferences intact.

The reviewed fixture is Rent 600 + Software 100 + Utilities 50 = CAD 750. With synthetic
sales revenue 3,700 and cost 2,200: gross profit 1,500; result 750; 750/3,700*100 = 20.3%
rounded. This scenario lives in automated tests, not the existing Mapping Test Company DB.
The existing sales_example.csv has different totals and remains unchanged.

## Files and verification

| File | Why it changed / verification |
| --- | --- |
| `tracker/models.py` | Expense, history and profile models; uniqueness/nonnegative/ownership tests |
| `tracker/migrations/0003_expense_intelligence.py` | Additive schema; upgrade/repeat/reverse/reapply tests on both backends |
| `tracker/services.py` | Shared bounded parsing and periods, expense import lock, separate expense aggregates; baseline, invalid-input, filtering, atomicity and concurrency tests |
| `tracker/forms.py` | Dataset-specific organization profile queryset using existing form; forged-profile and namespace tests |
| `tracker/views.py` | Shared upload orchestration and separate history context; upload/rollback/ownership tests |
| `businesspulse/urls.py` | Authenticated expense upload route; login/CSRF/method tests |
| `tracker/static/tracker/csv-upload.js` | Required selection count comes from the page, replacing five-field assumption; sales and expense Chromium tests plus existing Node tests |
| `tracker/templates/tracker/upload.html` | Shared upload form, four expense fields, COGS exclusion and category guidance; browser mapping/save/reuse scenario |
| `tracker/templates/tracker/base.html` | Separate sales and expense navigation links; browser page rendering/mobile-width check |
| `tracker/templates/tracker/dashboard.html` | Keep period controls/history visible with no sales and include expense section; empty-period tests |
| `tracker/templates/tracker/expense_report.html` | Expense cards, missing-data labels, category table, monthly chart and history; report assertions, HTML escaping and Chromium scenario |
| `tracker/tests/test_expenses.py` | Synthetic baseline, amounts/dates/limits, profile reuse/isolation, rollback, missing/zero/negative cases, period boundaries and constraints |
| `tracker/tests/test_expense_concurrency.py` | Real PostgreSQL blocking detection: identical, mixed, conflicting and different-organization imports |
| `tracker/tests/test_expense_migrations.py` | Historical sales/profile preservation and repeated schema operations in disposable test DB only |
| `tracker/tests/test_browser.py` | Real four-dropdown expense preview, no preview upload, duplicate selection blocking, save/reuse, mobile width and expected dashboard |
| `sample_data/expenses_example.csv` | Reviewed synthetic CAD 750 fixture; exact-value test reads this file |
| `.gitignore` | Allow only the named synthetic expense fixture, retaining generic CSV/database/secret exclusions |
| `README.md` | Current setup/product usage and distinction between the two synthetic scenarios |
| `docs/PRODUCT.md` | Expense grain, definitions, completeness semantics and current release scope |
| `docs/CSV_MAPPING.md` | Explain shared flow and separate profile namespace |
| `docs/DEPLOYMENT.md` | Additive migration, staging acceptance and safe code rollback instructions |
| `docs/EXPENSE_INTELLIGENCE.md` | Architecture, changed files, exact verification and known limits |

## Verification — 2026-11-17

Branch `feature/expense-intelligence` started from synchronized main `ea2cdcd`, with a
checkpoint before edits. Local Python 3.10.11 / Django 5.2.19; CI uses Python 3.12.
Only synthetic fixtures were used. Existing SQLite, local/restored hosted databases were
not migrated or modified. PostgreSQL 17 tests used a new disposable container named
`businesspulse-expense-postgres`, loopback port 55434, database `businesspulse_expense`.
SQLite tests used `DATABASE_URL=sqlite:///:memory:`. Both full suites enabled
`RUN_BROWSER_TESTS=1` with locally installed Playwright Chromium.

| Command/check | Exact result |
| --- | --- |
| `python manage.py check` (SQLite and PostgreSQL) | No issues |
| `python manage.py makemigrations --check --dry-run` (both) | No changes detected |
| `python manage.py migrate --noinput` (new disposable PostgreSQL) | All migrations applied; repeat: no migrations to apply |
| `python manage.py migrate --check` (disposable PostgreSQL) | Exit 0 |
| `python manage.py test --noinput` (SQLite + Chromium) | 78 discovered; 70 passed, 8 PostgreSQL lock tests skipped |
| `python manage.py test --noinput` (PostgreSQL + Chromium) | All 78 passed, including 3 browser tests and 8 lock tests |
| Migration integration test, both backends | 0002 -> 0003 -> repeat -> 0002 -> 0003; legacy sales/profiles preserved |
| `node --test tracker/tests/js/csv-preview.test.cjs` | All 6 passed |
| `python -m pip check` | No broken requirements |
| `git diff --check` | Passed |
| `python manage.py check --deploy` (ephemeral production-mode secret/config) | Existing W005 and W021 only: HSTS subdomains/preload not enabled; no development setting changes |

Deliberate CSRF rejection and simulated mapping-write failures log warnings/errors in tests;
these are expected assertions of rejection/rollback, not test failures. The existing CI matrix
already runs both databases, Chromium, Node and Linux Render build/Gunicorn verification, so
no workflow change is necessary. Hosted CI status is reported on the PR.

## Release, rollback and remaining risks

After owner approval, apply 0003 through the existing deployment build. A code rollback to
v0.2 can leave expense tables intact. Reversing 0003 destroys expense data/history/profiles;
never use schema reversal as a casual rollback after importing data. Backups and restore
validation remain required. This change does not merge or deploy anything.

The largest business risk is double-counting direct costs: an expense already included in
sales COGS would reduce results twice. CSV IDs cannot establish accounting equivalence.
The UI warns to exclude COGS, but the owner must supply correctly classified, complete data.
Different IDs for the same economic expense can still double-count. Category spelling/case
can split a category. No refunds, tax, accruals, depreciation, currency conversion, accounting
certification, arbitrary formulas, AI or ERP integration is added.

Existing beta limitations still apply: account recovery/verification, rate limiting, privacy
and retention controls, monitored backups and operational ownership require launch work.
Raw files are not retained by Django, but a proxy may buffer request bodies; stored fields,
filenames and mapping metadata still need privacy controls. Chromium is automated; other
browsers and the actual approved Render release still need acceptance testing. A successful
local migration cannot prove hosted backup, TLS or persistence behavior.
