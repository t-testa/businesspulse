# BusinessPulse

**Upload a sales CSV. Automatically see business KPIs.**

A runnable Django 5.2 starter for a production-oriented SMB analytics application. This is a **synthetic-data beta**, not a certified data platform for real customer information.

## What is already coded

- Account sign-up, sign-in and sign-out using Django authentication.
- One private business workspace per account, with CAD or USD currency chosen during signup.
- UTF-8 sales CSV ingestion with column aliases, validation, atomic imports and duplicate protection.
- Revenue, gross profit, gross margin, transaction count, average transaction value, purchasing customers.
- Reporting period filters, simple monthly revenue chart, recent import history.
- KPI show/hide preferences and transaction CSV export (with formula-injection protection).
- SQLite development, PostgreSQL production configuration, WhiteNoise, Gunicorn, GitHub Actions tests, Render deployment instructions.
- Sample **fictional** sales CSV and initial Django tests.

**Limits:** Only completed sales transactions in one currency; exactly one transaction per row. This is **not** a generic arbitrary-data upload engine yet. No Excel, chart builder, payment processing, refunds, tax, account recovery, rate limiting, email verification or data mapping UI. See [product specification](docs/PRODUCT.md).

## Quick start — Windows PowerShell

1. Download the starter archive and extract it to a project folder you control, for example `C:\Users\<you>\Projects\businesspulse`.
2. Open VS Code or a PowerShell terminal inside the extracted `businesspulse_mvp` directory (the directory containing `manage.py`).
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
6. Sign up with a test account, upload `sample_data/sales_example.csv` and open the dashboard.
7. Try changing the reporting period, hiding a KPI, exporting a CSV and uploading the same sample again (no duplicated sales).

### Run with Codex

If you already have Codex connected in VS Code, open this folder and give it the contents of [CODEX_START_HERE.md](CODEX_START_HERE.md). From Codex CLI, open a terminal in the project directory and run `codex`; sign in to your account if needed. Codex should **verify and repair** the starter before adding new functionality. [Codex guidance](https://learn.chatgpt.com/docs/codex/cli).

### Git and GitHub

Create your own **private** GitHub repository (suggested repo name: `businesspulse`). Then, from inside the project folder:

```powershell
git init
git add .
git commit -m "feat: initial BusinessPulse sales KPI vertical slice"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/businesspulse.git
git push -u origin main
```

This assumes you already created the empty repository on GitHub and authenticated Git (e.g. via your credential manager). Do **not** publish production secrets, real customer data, or `db.sqlite3`.

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
- [Engineering learning log](docs/LEARNING_LOG.md) — learn rather than blindly delegate.
- [Agent guardrails](AGENTS.md) — help Codex respect correctness and scope.

## Launch gate

Code in a repository is **not** a live public application. Deploy only after the project runs and passes tests locally. A private Render sandbox using synthetic data can be published for a few testers after a basic security review. Before using real client data, address account verification/recovery, privacy and retention, abuse prevention, backups and restore, tenant-isolation review, monitoring and operational responsibility.
