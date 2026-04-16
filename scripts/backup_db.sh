#!/usr/bin/env bash
# Usage: bash scripts/backup_db.sh
# Dumps job_pipeline to data/backups/job_pipeline_YYYYMMDD_HHMM.sql.gz
BACKUP_DIR="$(dirname "$0")/../data/backups"
mkdir -p "$BACKUP_DIR"
OUTFILE="$BACKUP_DIR/job_pipeline_$(date +%Y%m%d_%H%M).sql.gz"
docker exec pgvector_db pg_dump -U postgres job_pipeline | gzip > "$OUTFILE"
echo "Backup saved → $OUTFILE"
