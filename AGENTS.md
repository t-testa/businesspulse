# Agent instructions — BusinessPulse

You are working on a **Django 5.2 LTS + PostgreSQL** SMB sales KPI product. First read `README.md`, `docs/PRODUCT.md`, `docs/DEPLOYMENT.md` and existing tests before changing code.

## Boundaries

- Never rewrite the app into a new frontend/backend stack without user approval.
- The grain is one **completed sales transaction** per `transaction_id` per organization. Never sum duplicated join rows or silently accept conflicting duplicate IDs.
- Always scope sales and imports to the authenticated user's organization; add tests for cross-tenant isolation.
- KPI formulas must be deterministic and covered by unit/integration tests. No arbitrary Python or SQL supplied by users.
- Treat uploaded CSVs as hostile: validate byte count, rows, headers, numeric values, dates and IDs. Do not execute user strings or retain the raw file.
- Do not introduce paid third-party APIs, AWS resources, production data or public deployments without the owner approving credentials and costs.
- Never commit database files, real CSVs, tokens, private keys, or `.env` files.
- Keep code focused and readable: small migrations, descriptive commits, and tests for regressions.

## Workflow

1. Inspect existing project state and Git status.
2. Create a branch and checkpoint commit before significant edits.
3. Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test` and reproduce a UI scenario.
4. Fix bugs if any, and explain the root cause to the owner.
5. Make only the smallest change that meets the requested acceptance criteria.
6. After changes, provide a concise change log, test results, and any unverified behavior.

## Learning contract

The owner is learning full-cycle software engineering. Explain the request path, architecture decisions, security implications, tests, and how to deploy/revert. Do not hide issues or say production-ready without verifying the requirements in `docs/DEPLOYMENT.md`.
