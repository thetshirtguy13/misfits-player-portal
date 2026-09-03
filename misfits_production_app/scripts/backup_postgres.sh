#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL is required}"
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump "$DATABASE_URL" | gzip > "backups/misfits-$stamp.sql.gz"
echo "Created backups/misfits-$stamp.sql.gz"
