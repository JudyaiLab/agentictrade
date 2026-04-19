#!/usr/bin/env bash
# Production startup script — loads .env and starts uvicorn
set -euo pipefail

cd "$(dirname "$0")"

# Load .env (skip comments and empty lines)
if [ -f .env ]; then
    set -a
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        # Strip leading/trailing whitespace from key
        key="$(echo "$key" | xargs)"
        [ -z "$key" ] && continue
        export "$key=$value"
    done < .env
    set +a
fi

# Production: use SQLite (DATABASE_URL in .env is for future PG migration)
unset DATABASE_URL 2>/dev/null || true

# Production security: disable Swagger UI
export ACF_DISABLE_DOCS=1

echo "Starting AgenticTrade API on port 8092..."
exec python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8092
