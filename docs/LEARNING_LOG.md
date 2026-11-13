# Owner's engineering learning log

After each Codex session, personally answer these five questions:

1. **What changed?** Name the feature, file(s) and Git commit.
2. **Why was it designed this way?** Name one trade-off.
3. **How did we verify it?** Name the test and the failure it prevents.
4. **What could go wrong in production?** Security, correctness, reliability or cost.
5. **Could I explain or modify it without Codex?** If not, write down one thing to practice.

## Milestone 1 — runnable application

- [ ] I can explain request -> URL router -> view -> ORM -> template.
- [ ] I can explain the transaction grain and why duplicate IDs matter.
- [ ] I can run `python manage.py test` and interpret a failed test.
- [ ] I can explain what a DB migration does and how to reverse one.
- [ ] I personally logged in, uploaded sample data, and checked one KPI by hand.

## Milestone 2 — hosted beta

- [ ] I can explain DNS, TLS/HTTPS, WSGI/Gunicorn and the hosting service.
- [ ] I can explain local SQLite vs managed production PostgreSQL.
- [ ] I can locate production logs without asking Codex.
- [ ] I can explain which variables contain secrets and why.
- [ ] I can perform a deployment, verify retention, and describe a rollback.
