# BusinessPulse

**Upload sales and operating expense CSVs. See revenue, gross profit, costs and estimated operating results.**

A Django 5.2 application in the public [t-testa/businesspulse repository](https://github.com/t-testa/businesspulse). This is a **synthetic-data beta**, not a certified data platform for real customer information. The owner has verified Render deployment, account separation, persistence after redeployment, and a local restore of the hosted PostgreSQL database. New releases still require review and deployment approval.

## What is already coded

- Account sign-up, sign-in and sign-out using Django authentication.
- One private business workspace per account, with CAD or USD currency chosen during signup.
- UTF-8 sales CSV ingestion with column aliases, validation, atomic imports and duplicate protection.
- Browser-local preview of the first 10 CSV rows, editable column mappings, and reusable workspace-scoped mappings.
- Separate operating expenses, category breakdown, monthly expense trend, and estimated operating result/margin.
- Revenue, gross profit, gross margin, transaction count, average transaction value, purchasing customers.
- Reporting period filters, simple monthly revenue chart, recent import history.
- KPI show/hide preferences and transaction CSV export (with formula-injection protection).
- SQLite development, PostgreSQL production configuration, WhiteNoise, Gunicorn, GitHub Actions tests, Render deployment instructions.
- Reviewed **fictional** sales/expense CSV fixtures and automated regression tests.

**Limits:** Completed sales and operating expense transactions in one currency; exactly one transaction per row. This is **not** a generic arbitrary-data upload engine yet. No Excel, date-format conversion, chart builder, payment processing, refunds, tax, account recovery, rate limiting or email verification. See [product specification](docs/PRODUCT.md).

## Quick start — Windows PowerShell

1. Clone the existing public repository: `git clone https://github.com/t-testa/businesspulse.git`.
2. Run `cd businesspulse` and open a terminal in that directory (the directory containing `manage.py`).
3. Create and activate a Python virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If Python 3.12 is not installed, `py -3` may be used if your installed version is supported by Django 5.2. If PowerShell blocks the activation script, use `cmd.exe` and `.venv\Scripts\activate.bat` or call `.venv\Scripts\python.exe` directly. Do not disable your machine's security policy just for this project.

4. Set up the database, run checks, and launch the development server:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py test
python manage.py runserver
```

5. Open **http://127.0.0.1:8000/signup/** in your browser.
6. Sign up with a test account, choose `sample_data/sales_example.csv`, review the preview and five column dropdowns, then select **Validate and import**.
7. Try changing the reporting period, hiding a KPI, exporting a CSV and uploading the same sample again (no duplicated sales).

### Run with Codex

If you already have Codex connected in VS Code, open this folder and give it the contents of [CODEX_START_HERE.md](CODEX_START_HERE.md). From Codex CLI, open a terminal in the project directory and run `codex`; sign in to your account if needed. Codex should **verify and repair** the starter before adding new functionality. [Codex guidance](https://learn.chatgpt.com/docs/codex/cli).

### Local PostgreSQL with Docker Desktop

Start Docker Desktop using its Linux engine, then run these commands with the virtual environment activated:

```powershell
docker compose up -d --wait postgres
$env:DATABASE_URL = 'postgresql://businesspulse:local-synthetic-only@127.0.0.1:55432/businesspulse'
python manage.py migrate --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
python manage.py runserver
```

The Compose password is public **local-only synthetic-test configuration**, never a staging credential. PostgreSQL 17 listens only on loopback port 55432. Its named Docker volume preserves local data across container restarts. The local database role can create Django's disposable test database; a staging application role should not need that permission.

To return to SQLite, run `Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue`. To stop PostgreSQL without deleting its data, run `docker compose stop postgres`. `docker compose down` also preserves the volume; adding `--volumes` destroys it, so do that only when deliberately discarding synthetic local data. Django does not automatically read `.env` files.

SQLite remains supported for single-user development. PostgreSQL is required for staging and the concurrency tests: SQLite does not implement the row locks used to serialize uploads. CI runs both database configurations with Python 3.12, and the PostgreSQL job also verifies the Linux build/start commands. `.python-version` selects Python 3.12 for Render as well.

### Importing a custom export

Choose a UTF-8, comma-separated CSV. The browser shows all column names and the first
10 rows without sending the file to the server. Recognized names are suggested;
choose a unique source column for transaction ID, date, customer ID, revenue, and
cost. Uncertain suggestions stay blank. Dates must still be YYYY-MM-DD and amounts
must be nonnegative with at most two decimals. Mapping does not change business grain.

Optionally enter **Save mapping as** before importing. A successful import saves
only the five header references under that name in your current workspace. An
existing name is updated only after a successful import. Select it on a later
upload; reordered columns work, but renamed/missing columns must be corrected.
Failed validation retains no file, sales, import history, or new mapping changes;
select the file again to retry. With JavaScript disabled, recognized headers and
existing saved mappings still work, but preview/custom mapping controls require it.

Limits: 2 MB, 5,000 rows, 100 columns, 200 characters per column name. Browser
preview cells are shortened to 200 characters; the server validates original values
in every row, including rows beyond the preview. See [CSV mapping design and verification](docs/CSV_MAPPING.md).

### Running the complete test suite

The basic `python manage.py test` works with production requirements alone; browser
tests are explicitly skipped unless enabled. For full UI coverage, install the
development-only requirements and Chromium (Node.js 18+ runs the small parser suite):

```powershell
python -m pip install -r requirements-dev.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.venv\playwright-browsers"
python -m playwright install chromium
$env:RUN_BROWSER_TESTS = '1'
python manage.py test --noinput
node --test tracker/tests/js/csv-preview.test.cjs
```

Set `DATABASE_URL` to a **disposable synthetic local database** to test PostgreSQL;
remove it to test SQLite. Never use the restored hosted database for development
verification. CI enables browser tests on both databases. Playwright is not installed
by the Render build, which continues to use `requirements.txt`.

### Git and GitHub

Work through branches and pull requests against `main` in [t-testa/businesspulse](https://github.com/t-testa/businesspulse). Review the diff and CI before merging; do not recreate the repository or push directly to `main`. Do **not** publish production secrets, real customer data, databases, or backups. See [the repository exposure audit](docs/SECURITY_AUDIT.md).

## How the application works

```text
Browser (HTML/CSS)
   -> Django routes (businesspulse/urls.py)
   -> Tracker views (tracker/views.py)
   -> CSV validations and metrics (tracker/services.py)
   -> Django ORM models (tracker/models.py)
   -> Local SQLite / hosted PostgreSQL
```

The app does not store raw uploaded CSV files; it stores validated transaction fields. The underlying database must still be treated as confidential, even when the CSV is discarded.

## Example KPI verification

For the included `sales_example.csv` (12 sales, 7 distinct customer IDs):

- **Revenue:** CAD 21,065.00
- **Direct costs:** CAD 12,190.00
- **Gross profit:** CAD 8,875.00
- **Gross margin:** ~42.1%
- **Transactions:** 12
- **Average transaction value:** CAD 1,755.42
- **Purchasing customers:** 7

Select **All time** to verify the entire sample. Some dates will fall outside a 30-day filter as time passes.

## What to do next

- [First Codex task](CODEX_START_HERE.md) — verify, repair, test, explain, and get a working localhost version.
- [Product requirements & KPI grain](docs/PRODUCT.md) — what counts as correct.
- [Deployment runbook](docs/DEPLOYMENT.md) — go live with synthetic data first.
- [Staging readiness results](docs/STAGING_READINESS.md) — PostgreSQL, concurrency, CI, and build verification.
- [Engineering learning log](docs/LEARNING_LOG.md) — learn rather than blindly delegate.
- [Agent guardrails](AGENTS.md) — help Codex respect correctness and scope.

## Launch gate

Code in a repository is **not** a live public application. Deploy only after the project runs and passes tests locally. A private Render sandbox using synthetic data can be published for a few testers after a basic security review. Before using real client data, address account verification/recovery, privacy and retention, abuse prevention, backups and restore, tenant-isolation review, monitoring and operational responsibility.

## v0.3 operating expenses

Use **Expense upload** for `expense_id,date,category,amount`; preview the first
10 rows, review four mapping dropdowns, and optionally save a mapping. Expense
profiles are private to the workspace and separate from sales profiles. The
server validates the entire file before an atomic import. Repeated identical
expense IDs are skipped; conflicting IDs reject the whole import. Raw CSV files
are not retained by Django.

**Exclude direct costs already included in sales COGS.** Use one unique ID per
operating expense, nonnegative amounts in the workspace currency, and YYYY-MM-DD
dates. Consistent category spelling matters. No refunds, taxes, accrual adjustments,
depreciation, currency conversion or accounting certification are included.

The dashboard applies one reporting period to both datasets. No expense imports
means **Not imported**, with operating result/margin unavailable. Once expenses
have been imported, an empty period shows a qualified zero, not a claim of complete
books. Estimated operating margin is N/A when revenue is zero. Expense cards are
always shown separately; existing sales-card preferences remain unchanged.

Synthetic acceptance example: with revenue CAD 3,700 and COGS CAD 2,200, upload
`sample_data/expenses_example.csv` (Rent 600, Software 100, Utilities 50).
Select **All time**: total expenses = CAD 750, estimated operating result =
CAD 750, estimated operating margin = 20.3%. This expense fixture is separate from
`sales_example.csv`, whose revenue remains CAD 21,065; do not expect the CAD 750
operating result when combining it with that different sales fixture.

Apply additive migration `0003_expense_intelligence` only to an approved target.
For development verification, use an isolated synthetic database, never a restored
hosted database. See [expense architecture, tests and rollback](docs/EXPENSE_INTELLIGENCE.md).
