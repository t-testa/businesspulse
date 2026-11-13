# First Codex assignment (copy this entire block into Codex)

You are my implementation partner for BusinessPulse. I'm the product owner learning real software delivery. This is my first production-oriented app. READ `AGENTS.md`, `README.md`, `docs/PRODUCT.md` and `docs/DEPLOYMENT.md` before you change anything.

**Milestone 1: get the supplied code running on my Windows machine and make it trustworthy.**

1. Inspect the repo and explain the architecture at a level I can follow. Do not rewrite the stack.
2. Create `.venv`, install `requirements.txt`, run Django system checks and `makemigrations --check --dry-run`, and run tests. Address any failing tests/migration drift with minimal changes. Report exactly what passed.
3. Run the development server. I will create a test account and upload `sample_data/sales_example.csv` myself. Give me the localhost URL, and show me what I should validate manually.
4. Calculate the example CSV KPIs manually, compare them with app results, and add a test for the baseline fixture values.
5. Verify duplicate-upload behavior, invalid-row atomicity, CSV formula-safe export, and account isolation. Add regression tests for issues.
6. Review security assumptions for **synthetic-beta-only** use and list blockers for real customer data.
7. Make a clean Git commit. Ask before creating paid services, deploying publicly, changing infrastructure, or handling real client data.

When finished, explain in plain English: files changed; architecture; test commands/results; what I personally learned; what remains to launch.

**Do not implement AI, cross-ERP integration, Excel upload, custom formulas, billing, or React yet.** We will add upload column mapping as the next focused milestone after the vertical slice works.
