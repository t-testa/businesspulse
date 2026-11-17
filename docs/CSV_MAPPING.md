# CSV column mapping — v0.2

## User flow and request path

1. The authenticated upload page sends alias definitions and only that
   organization's saved header mappings to the browser, using Django's escaped
   `json_script` output.
2. Selecting a file reads up to the 2 MB file limit locally. A small CSV parser
   understands BOM, comma separators, doubled quotes, quoted newlines and CRLF.
   It displays all detected headers and at most 10 data rows. Preview cells are
   truncated at 200 characters for display only. File changes invalidate stale
   asynchronous reads. Preview performs no HTTP upload and uses no localStorage.
3. The browser suggests only unambiguous aliases. Five dropdowns let the owner
   correct them or apply a saved mapping. Incomplete or duplicate selections
   disable the button. CSV contents are inserted with `textContent`, never HTML.
4. One normal CSRF-protected multipart POST sends the original file and mapping
   to `upload_sales`. No preview endpoint, file token, temporary-file workflow,
   React, external parsing service, or new application API is introduced.
5. `UploadForm` validates the mapping JSON and resolves saved IDs only through
   the current organization's queryset. `parse_sales_upload` independently
   validates headers, the exact five required mapping keys, distinct existing
   source columns, and every original CSV row, including rows after the preview.
6. Only then does the existing import service obtain the PostgreSQL organization
   lock and check duplicates/conflicts. Sales, import history, and optional saved
   mapping changes share one outer transaction. A conflict or failed metadata
   write rolls back the whole operation. KPI calculations are unchanged.

Server validation is the trust boundary. The preview is a convenience, not proof
that a file is importable. An attacker can bypass JavaScript; tests submit forged,
missing, malformed and cross-workspace mappings directly to the server.

## Saved mappings and migration

`tracker.0002_column_mapping` adds `ColumnMapping` with:

- `organization`: foreign key; deleting the organization deletes its mappings.
- `name`: at most 100 characters, trimmed by the form.
- `columns`: JSON object mapping the five required fields to source header names.
- `updated_at`: last successful save time.
- A database unique constraint on `(organization, name)`.

Only header references are stored, never preview rows or raw CSV bytes. Name
matching is case-sensitive. Saving an existing name replaces its references after
a successful import; selecting a profile alone never overwrites it. Different
organizations can use the same name independently. Reordering source columns
works because mappings use exact names, not positions. Missing/renamed columns
must be corrected. When explicit dropdown values and a saved ID are submitted,
the corrected values win, but the saved ID must still belong to the organization.

Apply with `python manage.py migrate --noinput` after release approval. This is
an additive table and does not rewrite sales, imports, or KPI preferences. Code
rollback can leave the table unused. Reversing only `0002` drops saved mappings;
sales and import history remain. Do not reverse `0001_initial` as a feature rollback.

## File handling and limits

One `BoundedMemoryUploadHandler` replaces Django's default memory/disk combination.
It counts received bytes regardless of claimed Content-Length and aborts files
over 2 MB or requests with multiple files. There is no Django temporary-file
fallback. The upload view rejects aborted requests before import. Tests explicitly
disable the normal memory threshold and fail if a disk handler is invoked.

Limits remain 5,000 rows, nonnegative two-decimal amounts, YYYY-MM-DD dates, and
one completed transaction per ID per organization. This release additionally
bounds preview/mapping width to 100 columns and header length to 200 characters.
Extra columns are ignored as business fields, but structural row errors still
reject the file. Empty or equivalent duplicate headers are rejected. Choosing
an explicit mapping can resolve two different alias names such as `revenue`
and `amount`; the user must decide which one has the intended meaning.

## Files changed

| Files | Purpose |
| --- | --- |
| `tracker/models.py`, `tracker/migrations/0002_column_mapping.py` | Organization-scoped saved metadata and database uniqueness |
| `tracker/services.py` | Validate explicit mappings without changing row rules, deduplication, locks, or KPI calculations; return resolved headers for saving |
| `tracker/forms.py`, `tracker/views.py` | Scoped profile choices, JSON/name validation, one-post orchestration and atomic metadata save |
| `tracker/upload_handlers.py`, `businesspulse/settings.py` | Bound uploads in memory with no disk fallback |
| `tracker/templates/tracker/upload.html`, `tracker/static/tracker/csv-upload.js`, `tracker/static/tracker/styles.css` | Local preview, suggestions, dropdowns, saved mapping selection, accessible status messages and responsive layout |
| `tracker/tests/test_mapping.py` | Server validation, isolation, profiles, atomicity, CSRF, and memory-only upload regressions |
| `tracker/tests/test_concurrency.py` | Existing lock tests plus mapped overlapping new/duplicate upload coverage |
| `tracker/tests/test_browser.py`, `tracker/tests/js/csv-preview.test.cjs` | Real Chromium flow and pure preview/parser regressions |
| `requirements-dev.txt`, `.github/workflows/ci.yml` | Development-only Playwright; Chromium and Node tests in both database jobs |
| `README.md`, `docs/PRODUCT.md`, `docs/DEPLOYMENT.md`, this document | Current feature contract, setup, migration/rollback and verification |

## Verification — 2026-11-16

Started from synchronized `main` at `8c9f071`, on `feature/csv-column-mapping`.
All fixtures are synthetic. The existing SQLite file, existing Docker database,
and locally restored hosted PostgreSQL database were not inspected or migrated.
PostgreSQL verification used a separate disposable PostgreSQL 17 container on
loopback port 55433 and a new `businesspulse_mapping` database. SQLite tests used
an in-memory database. Browser tests used Django's temporary test server/database.

| Check | Result |
| --- | --- |
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No changes detected after generating committed migration 0002 |
| Full PostgreSQL suite with `RUN_BROWSER_TESTS=1` | 55 tests passed, including 2 Chromium scenarios and 4 real-connection concurrency tests |
| Full SQLite suite with `RUN_BROWSER_TESTS=1` | 51 passed; 4 PostgreSQL lock tests explicitly skipped |
| `node --test tracker/tests/js/csv-preview.test.cjs` | 6 passed |
| PostgreSQL migration forward, reverse 0002, reapply, repeat | Passed; synthetic sale/import preserved during reversal; repeated migration was a no-op |
| `python -m pip check` / `git diff --check` | Passed |
| Production-mode upload page and collected hashed JavaScript | HTTP 200 for both |
| `python manage.py check --deploy` with ephemeral production-mode configuration | Only the existing intentional W005/W021 HSTS warnings |

Browser scenarios verify preview sends no POST, 10 rows/all headers, inert HTML
cells, alias suggestions, duplicate selection prevention, manual corrections,
mobile page width, one import POST, profile persistence/reuse, duplicate no-op,
missing source names, and an invalid eleventh data row rejected by the server.
Server tests also force a mapping-write failure and confirm rollback of sales
and history; its deliberate error log is expected, not a test failure.
An initial browser test implementation queried Django's ORM inside Playwright's
event loop; assertions were moved outside that loop without disabling Django's
async-safety checks, then the browser tests passed.

## Remaining risks and explicit limits

- Actual deployment and migration on Render remain unapproved; no merge/deploy
  was performed. Check PR CI and manually accept this flow on staging after approval.
- The proxy/platform may buffer request bodies independently. Memory-only here
  describes Django's upload handler, not an infrastructure retention guarantee.
  Concurrent requests consume memory; ingress limits and rate limiting remain
  operational work. Logs must not include uploaded data or credentials.
- A semantically wrong but structurally valid mapping can produce wrong business
  results. The owner must review the preview and source definitions. There is no
  formula language, automatic data transformation, Excel, date conversion, or
  refund/line-item reconciliation in this release.
- Saved names/headers are persistent metadata. Avoid personal information in
  names or headers. Deletion/renaming UI, profile quotas, and audit/version history
  are deferred; existing-name saves intentionally replace the previous mapping.
- A failed POST requires reselecting the local file. JavaScript is required for
  preview/custom dropdowns; recognized headers and saved profiles still work
  without it. Chromium is automated; other browsers have not been exhaustively tested.
- SQLite remains for sequential development; concurrency guarantees depend on
  PostgreSQL READ COMMITTED and all imports using the existing service lock.

## v0.3 reuse

Expense upload reuses the same form, template, preview script, upload handler and
bounded CSV parser. Field count now comes from the page's dataset configuration:
five fields for sales, four for expenses. Separate ExpenseColumnMapping rows keep
sales names/profiles unchanged; routes determine the dataset, never a posted
organization or dataset parameter. See [expense intelligence](EXPENSE_INTELLIGENCE.md).
