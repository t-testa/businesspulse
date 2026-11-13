# Public repository exposure audit — 2026-11-13

## Scope and result

Reviewed all local Git references, both original commits, every stored object
(including objects outside branch history), commit attribution, and all 39 tracked
files present at the start of this audit. The original object inventory contained
38 unique blobs, 19 trees, and 2 commits. Git reflogs, local snapshot references,
ignored runtime artifacts, and GitHub Actions configuration were also inspected.
No Git remote was configured, and no push or other publication was performed.

No live credentials, access tokens, private keys, real customer records, database
files, database dumps, or binary attachments were found in tracked files or
historical file versions. This is a source-exposure review, not a claim that the
application is ready to process real customer data.

## Findings and remediation

| Finding | Assessment | Action |
| --- | --- | --- |
| Personal absolute Windows path in the prior review report | Unnecessary local identity/path disclosure | Replaced with a generic source-folder description in current files, historical commits, and local snapshot trees |
| Personal Git display-name attribution | Normal Git metadata, but unnecessary personal attribution for this publication review; emails were already placeholder/noreply addresses | Rewrote both unpublished commits with `BusinessPulse Contributor <contributor@example.invalid>` attribution, retaining dates and commit messages |
| Limited ignore rules | SQLite was ignored, but other database formats, dumps, private keys, logs, exports, and uploads could be accidentally added | Expanded `.gitignore`; retained explicit exceptions for the root `.env.example` and the fictional sample CSV |
| Development-only Django secret fallback | Deliberately public development value, not a discovered live credential | Left unchanged; DEBUG=False already requires an externally supplied secret. Never use a public example/fallback value in production |
| Example environment variables and PostgreSQL URL | Clearly labeled placeholders with no real endpoint or password | Left unchanged |
| Password literals and example email in tests | Synthetic accounts created in the disposable test database | Left unchanged |
| Sample sales CSV | Fictional transaction/customer identifiers with dates and amounts; no names, addresses, contact information, or payment credentials | Kept as the explicitly allowed fixture |

History rewriting changes commit IDs. The old reflogs were expired and obsolete
objects pruned, so removing the path was not limited to a new tip commit.
Local application snapshot trees were sanitized as well. No backup branch retains
the old history. No remote copies were available to inspect; this audit cannot
establish whether someone previously copied the original source elsewhere.

## Verification method

- Enumerated refs, commits, tracked paths, reflogs, and all stored object types.
- Examined historical blob contents with a local Python scanner using private-key,
  provider-token, credential-URL, secret-assignment, email, personal-path, binary,
  database-signature, and high-entropy candidate checks. Matches were reported by
  file/rule/line rather than printing potential secret values, then reviewed.
- Rechecked all stored objects after rewriting for private keys, provider tokens,
  JWT-like strings, SQLite signatures, personal paths, and unexpected author or
  committer identities: no findings.
- Confirmed tracked files are text and local `.venv`, SQLite database, caches, and
  generated static files are ignored rather than tracked.
- Verified 22 representative sensitive/generated paths are ignored and 4 required
  source/fixture paths remain allowed with `git check-ignore --no-index`.
- Ran `git fsck --full --no-reflogs` and `git diff --check` successfully.

This was a custom local scan plus manual review, not a commercial secret-scanner
certification or dependency vulnerability audit. Pattern/entropy scanning cannot
prove that every possible secret format is absent. No discovered live credential
requires rotation based on these findings.

## Before any future public push

- Review staged changes and newly introduced history for sensitive content;
  `.gitignore` is an accident-prevention measure, not an access control, and
  `git add -f` can override it. Review any additional data fixture before allowing it.
- Keep raw customer exports, local databases, secrets, uploads, and backups outside
  tracked source. A sanitized CSV is not automatically safe just because IDs replace names.
- Use private environment configuration and a fresh secret for any deployed app.
- Keep Git attribution privacy settings intentional. This review uses a generic
  local identity; a verified GitHub noreply identity can be chosen before future work.
- Choose an appropriate license deliberately if public reuse is intended. No
  license was added or assumed during this audit.
- Review the operational/security blockers in `REVIEW.md` and `DEPLOYMENT.md`.
  Public source availability does not make the application production-ready.

Only repository hygiene and audit documentation changed in this assignment.
Application behavior, dependencies, database schema, and deployment settings did
not change. Existing tests were not rerun because no executable application or
test code changed.
