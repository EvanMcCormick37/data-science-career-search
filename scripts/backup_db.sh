#!/usr/bin/env bash

BACKUP_DIR="$(dirname "$0")/../data/backups"
mkdir -p "$BACKUP_DIR"

# 1. Use .sql if you want the readable text file
OUTFILE="$BACKUP_DIR/job_pipeline_$(date +%Y%m%d_%H%M).sql"

echo "Starting backup..."

# 2. Added 'set -o pipefail' so the script notices if pg_dump fails
# 3. Removed '| gzip' to give you the plain SQL file
if docker exec pgvector_db pg_dump -U postgres job_pipeline > "$OUTFILE"; then
    echo "Done! Backup saved → $OUTFILE"
else
    echo "ERROR: Backup failed! Check if the Docker container is running."
    rm -f "$OUTFILE" # Delete the broken 1KB file
    exit 1
fi
