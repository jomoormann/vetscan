#!/bin/bash
# Download a backup of the production database before deployment
# Usage: ./deploy/backup_db.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/vet_proteins_$TIMESTAMP.db"

# VPS Server details
SSH_USER="root"
SSH_HOST="76.13.5.89"
REMOTE_DB="/var/www/vetscan.net/app/data/vet_proteins.db"

echo "=== VetScan Database Backup ==="
echo "Timestamp: $TIMESTAMP"
echo ""

# Create backup directory if needed
mkdir -p "$BACKUP_DIR"

# Download the database
echo "Downloading production database from VPS..."
scp "$SSH_USER@$SSH_HOST:$REMOTE_DB" "$BACKUP_FILE"

# Verify the backup
if [ -f "$BACKUP_FILE" ]; then
    SIZE=$(ls -lh "$BACKUP_FILE" | awk '{print $5}')
    echo ""
    echo "Backup successful!"
    echo "  File: $BACKUP_FILE"
    echo "  Size: $SIZE"

    # Quick integrity check
    TABLES=$(sqlite3 "$BACKUP_FILE" ".tables" 2>/dev/null || echo "")
    if [ -n "$TABLES" ]; then
        ANIMAL_COUNT=$(sqlite3 "$BACKUP_FILE" "SELECT COUNT(*) FROM animals;" 2>/dev/null || echo "?")
        echo "  Animals in backup: $ANIMAL_COUNT"
    else
        echo "  WARNING: Could not verify database contents"
    fi
else
    echo "ERROR: Backup failed - file not created"
    exit 1
fi

echo ""
echo "=== Backup Complete ==="
