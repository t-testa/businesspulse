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
