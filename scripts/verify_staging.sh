#!/usr/bin/env bash
# Local/CI verification only. DATABASE_URL must point to a disposable test database.
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to a disposable PostgreSQL database}"
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY
DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
export DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost
export PORT=8001
# Test dispatcher startup with nonfunctional credentials and an empty synthetic queue.
# The smoke test never requests delivery; no provider API request is made.
export ACCOUNT_RECOVERY_ENABLED=1 PUBLIC_SIGNUP_ENABLED=0
export RECOVERY_ORIGIN=https://localhost
export EMAIL_BACKEND=tracker.email_backend.ResendBackend
export DEFAULT_FROM_EMAIL='BusinessPulse <recovery@example.test>'
export RESEND_API_KEY=synthetic-nonfunctional-test-key

test -x build.sh
./build.sh
python manage.py check --deploy --fail-level ERROR
python manage.py migrate --check
gunicorn businesspulse.wsgi:application --check-config

# Exercise the exact Procfile command using the smoke-test port above.
start_command="$(sed -n 's/^web: //p' Procfile)"
bash -c "exec $start_command" &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' EXIT
python scripts/smoke_staging.py

# A failed dispatcher must cause its supervisor to exit, failing this smoke check.
sleep 2
kill -0 "$server_pid"
