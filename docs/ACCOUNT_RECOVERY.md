# Secure account recovery â€” v0.5

Synthetic staging only. This release does not deploy, provision email services,
modify hosted databases, or verify ownership of historical email addresses.

## Request path and guarantees

Login links to Forgot password. Every syntactically valid request receives the same
redirect and message, including unknown, inactive, password-disabled, ambiguous,
throttled and delivery-disabled accounts. The HTTP request never queries users or
contacts an email service: it applies shared limits and queues an address. A small
dispatcher looks up eligible users separately and uses Django's PasswordResetForm
and standard token generator. Exactly one case-insensitive matching account must
exist; otherwise no email is sent. Mail contains a link to RECOVERY_ORIGIN, never
an origin supplied by the request Host header.

Links expire 30 minutes after generation. Django moves the token into the session
before displaying the password form. Password validation, CSRF and no-cache
responses remain enabled. Successful reset invalidates all prior reset links and
existing authenticated sessions; it does not log the user in. Authenticated
password change requires the old password and preserves only the current session.
PostgreSQL locks the user while checking and consuming a token, so concurrent
submissions cannot both succeed. Neither flow recreates users or workspaces.

Token-bearing URLs retain no-referrer, even for invalid tokens and CSRF errors.
An explicit allowlist gives token-free forms same-origin referrers so browser CSRF
checks work. Cross-origin referrers are suppressed in either case. App log messages
redact reset paths; the production launcher omits paths from Gunicorn access logs.
Render/provider infrastructure logs are outside application control: restrict access
and retention, and never paste reset links into tickets or analytics.

## Shared abuse controls

PostgreSQL counters use row locks and HMAC keys, not process-local memory. Each
request consumes global buckets first; denied/unknown requests also consume budget.
Limits use fixed windows, not rolling windows or calendar billing periods:

| Scope | Limit |
| --- | --- |
| Reset requests, whole application | 90/day; 2,000/30-day window |
| Reset requests, socket peer | 10/hour |
| Reset requests, normalized address | 3/hour; 10/day |
| Application login/change/reset password submissions, whole application | 1,200/hour |
| Those password submissions, socket peer | 30/10 minutes |

Reset throttling stays generic. Password submissions return 429 when throttled.
REMOTE_ADDR is used; untrusted X-Forwarded-For cannot bypass limits. On Render it
may represent a shared proxy, grouping legitimate users together. Validate this
under real staging traffic before increasing limits or implementing trusted proxy
parsing. These deliberately conservative controls suit a small synthetic beta.
They do not prevent distributed denial of service or protect every Django admin
authentication endpoint. Admin access must remain restricted to trusted operators.
SQLite remains supported locally; concurrent-process guarantees require PostgreSQL.

## Email ownership: activation prerequisite

Existing signup accepts an email without verifying possession. A typo, reassigned
mailbox, or maliciously registered address could give that mailbox's owner control
of an existing account. Recovery cannot prove that historical registration was
legitimate. Django's User.email also lacks a uniqueness constraint. Duplicate
addresses fail closed rather than sending links for multiple accounts.

PUBLIC_SIGNUP_ENABLED defaults to 0 in production and 1 locally. Hosted signup
returns 404 and its links are hidden; existing accounts and passwords are untouched.
Do not enable public signup until a verified-email enrollment flow is implemented.
The original signup form, if explicitly re-enabled, still reveals duplicate email
addresses and does not verify ownership. This release does not claim to fix that
legacy flow. Before enabling recovery, review each synthetic user's address through
an independently trusted channel, resolve ambiguous addresses with owner approval,
and confirm control of the acceptance account's mailbox. Do not mass-edit emails or
create replacement workspaces to solve recovery problems.

## Local development (no actual delivery)

After installing requirements and migrating your local development database:

```powershell
python manage.py migrate
python manage.py runserver
```

In another terminal with the same local database/environment:

```powershell
python manage.py process_recovery
# Or drain existing requests once:
python manage.py process_recovery --once
```

DEBUG defaults to console mail and enabled recovery; use the console link only
with synthetic accounts. RECOVERY_ORIGIN defaults to http://127.0.0.1:8000; adjust
it if using another local port. Tests use in-memory mail and mocked provider calls.
Environment variables are not automatically read from .env.

## Provider choice and operator setup

Render Free blocks outbound SMTP ports 25, 465 and 587. This implementation uses
Resend's HTTPS email API, which needs no extra Render service. Its advertised free
tier is suitable for this tiny beta; verify current limits and billing settings
before signup. No account, paid plan, DNS record or API key is created by this PR.
An owned, verified sending domain is required; if none is available, domain costs
need approval. Other HTTPS providers would need a backend adapter; SMTP is suitable
only for environments that permit it.

After owner approval, configure the provider's required SPF/DKIM DNS records, verify
the domain, use a sending-only API key scoped to that domain, and disable click/open
tracking for password messages. Use a sender address on that domain. Never commit
keys, real email addresses, reset messages or database dumps. Provider delivery
records contain recipient addresses and are subject to its retention policy.

References: [Render Free limits](https://render.com/docs/free),
[Resend pricing](https://resend.com/pricing),
[domain verification](https://resend.com/docs/dashboard/domains/introduction),
[send-email API](https://resend.com/docs/api-reference/emails/send-email),
[Django authentication](https://docs.djangoproject.com/en/5.2/topics/auth/default/),
[Django referrer/CSRF guidance](https://docs.djangoproject.com/en/5.2/ref/middleware/#referrer-policy).

## Approved deployment procedure â€” do not execute during development

1. Review/merge the PR only with owner approval. Keep Render auto-deploy controlled.
   Record the current v0.4 commit and take a database backup; verify restore into a
   separate local database. Record workspace/user IDs, sales/expense totals and
   import/reversal counts for the synthetic acceptance account and a second account.
2. Leave ACCOUNT_RECOVERY_ENABLED=0 and PUBLIC_SIGNUP_ENABLED=0 initially. Apply
   additive tracker migration 0005_account_recovery through the existing reviewed
   build process (`./build.sh`). It only creates a queue and rate-limit table; it
   does not change users, organizations, financial tables or migration 0004.
   Existing v0.4 workers remain schema-compatible during this additive migration.
3. Change the Render Start Command to `python scripts/start_service.py`. Updating
   Procfile alone does not change a dashboard-configured command. The launcher runs
   Gunicorn and, when recovery is enabled, the dispatcher on the same service. If
   either child exits unexpectedly, the launcher stops the other and exits for
   Render restart. Check build logs for `Applying tracker.0005_account_recovery... OK`
   and verify `python manage.py migrate --check` / `showmigrations tracker` using an
   approved operator connection (Free has no web shell/one-off jobs).
4. Audit historical email ownership as above and configure the approved provider.
   Set these secrets/settings in Render, retaining existing production settings:

   ```text
   ACCOUNT_RECOVERY_ENABLED=1
   PUBLIC_SIGNUP_ENABLED=0
   RECOVERY_ORIGIN=https://<your-exact-staging-host>
   EMAIL_BACKEND=tracker.email_backend.ResendBackend
   DEFAULT_FROM_EMAIL=BusinessPulse <recovery@your-verified-domain>
   RESEND_API_KEY=<secret sending-only key>
   ```

   DEBUG must remain 0, HTTPS/secure cookies enabled, and DATABASE_URL must reference
   persistent PostgreSQL. Keep DJANGO_SECRET_KEY stable. Production refuses enabled
   recovery without HTTPS origin, Resend backend, key and non-placeholder sender.
   Configuration cannot itself verify domain ownership or API-key validity.
5. After the approved restart, check health and logs, request a reset for the
   existing synthetic acceptance account and verify actual receipt. Follow the link,
   choose a validated password, confirm the old password fails, and sign in with
   the new password. Confirm original user/workspace IDs, sales, expenses, audit
   histories, mappings, KPIs and second-account isolation. Reusing the link must fail.
   Test password change and a deliberately unknown email's identical public response.
6. Keep recovery disabled if delivery/domain/account review fails. Do not solve
   delivery failures by enabling console email in production or logging message bodies.

## Delivery and recovery limitations

The database queue stores only an email and timestamps, never passwords/tokens/raw
CSV. Requests expire after 15 minutes. The dispatcher deletes a claim before sending
to avoid coupling public request timing to account lookup or provider latency. This
is at-most-once dispatch: a crash/timeout can lose mail, and failures are not retried
automatically. Request a fresh link after diagnosing provider configuration/quota.
Provider timeout is 10 seconds. Parallel dispatchers claim jobs with SKIP LOCKED.

Render Free sleeping/restarts can delay or expire requests; this is not a delivery
SLA. Cleanup runs with the dispatcher; expired rows can remain physically stored
while the service sleeps or delivery is disabled, and in database backups until
backup retention expires. Run `process_recovery --once` with delivery disabled to
purge expired entries during an approved maintenance session. Global fixed-window
limits are not a provider billing guarantee. Provider quotas remain the final cap.

If activation fails, set ACCOUNT_RECOVERY_ENABLED=0 and restart using the approved
deployment process. This stops new delivery and rejects outstanding reset links.
For code rollback, return to the recorded v0.4 commit while leaving 0005 tables in
place; passwords already changed remain changed and financial records remain intact.
Do not reverse migration 0004 or restore a whole database just to undo this feature.
Only remove 0005 after all new workers stop and the operator explicitly accepts loss
of queued requests/rate counters. A later re-enable can process unexpired queued
requests; discard them explicitly in an approved maintenance session if required.

Never send a temporary password to an unverified address. If mailbox access is lost,
an operator must establish ownership independently before an approved email repair
or Django `changepassword` operation. Keep the same user ID and workspace; never
ask for the user's password. Mailbox compromise, email-change verification, MFA,
public verified signup, production proxy-aware anti-abuse monitoring and durable
delivery retries remain outside this synthetic beta release.

## Change map and verification

- settings/URLs/context processor/views: opt-in delivery, canonical origin, routes,
  hosted signup gate, template precedence and hidden signup links.
- recovery.py/recovery_views.py: queued generic requests, database rate limits,
  standard Django password handling and concurrent user locks.
- email_backend.py/recovery_logging.py/recovery_headers.py: HTTPS provider adapter,
  generic error logs, credential-path redaction and token-safe browser headers.
- models.py/migration 0005: two additive non-financial tables, no user backfill.
- process_recovery command and package initializers: bounded-lived queue processing.
- start_service.py/Procfile/.gitattributes: supervised co-located dispatcher, safe
  access logs and LF line endings for the Linux startup command.
- verify_staging.sh/smoke_staging.py: exercise enabled dispatcher startup with fake
  credentials, closed hosted signup, reset headers and existing security checks.
- registration templates and base navigation: request, confirmation, completion and
  password-change pages, plain-text email and login/navigation links.
- recovery tests: token validity/expiry/reuse, validation, CSRF, session invalidation,
  enumeration, limits, canonical host, failure handling, account/data preservation,
  PostgreSQL races, migration roundtrip and real Chromium recovery journey.
- existing migration regression: restore latest schema after the deliberate 0004
  rollback guard fails, because Django may already have unapplied additive 0005.
- settings tests: isolated-process checks for secure hosted defaults/configuration.
- README/PRODUCT/DEPLOYMENT/.env.example: setup, scope and activation requirements.

See the PR verification record for exact commands and results. Live provider delivery,
Render process scheduling and historical account ownership require the approved
staging acceptance check; automated local tests cannot establish those facts.

## Local verification record — 2026-11-17

All database work used disposable local databases, never hosted/restored data.
Windows Python 3.10.11/Django 5.2.19; PostgreSQL 17; Linux smoke Python 3.12.

| Command / configuration | Result |
| --- | --- |
| `python manage.py test --noinput`, SQLite in-memory, RUN_BROWSER_TESTS=1 | 121 discovered: 106 passed, 15 PostgreSQL-only skipped |
| Same command, disposable PostgreSQL, RUN_BROWSER_TESTS=1 | 121 passed, no skips |
| Chromium within each full run | All 5 browser scenarios passed |
| `node --test tracker/tests/js/csv-preview.test.cjs` | 6 passed |
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| `python -m pip check` | No broken requirements |
| Repeated PostgreSQL `migrate --noinput`; `migrate --check` | No migrations to apply; passed |
| `showmigrations tracker` | 0001 through 0005 marked applied |
| Linux `bash scripts/verify_staging.sh` | Build, collectstatic, migrations, Gunicorn configuration, supervised web/dispatcher startup, smoke and shutdown passed |
| `check --deploy --fail-level ERROR` in Linux smoke | No errors; existing security.W005 and W021 warnings only |
| `git diff --check` | Passed |

W005 concerns HSTS on all subdomains; W021 concerns browser preload. Those require
an explicit domain-wide HTTPS decision and were intentionally not enabled here.
The provider API was mocked or not called; actual mail delivery is unverified.

Bugs found during implementation: admin templates shadowed account templates
(fixed template search precedence); blanket no-referrer broke Chromium form origins
(fixed with an explicit token-free form allowlist); CRLF in Procfile broke Linux
startup (fixed LF attributes). The browser/build checks caught these before review.
