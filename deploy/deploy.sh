#!/bin/bash
# VetScan Deployment Script for VPS
# This script ENFORCES the correct deployment procedure including mandatory backups
#
# Usage: ./deploy/deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# VPS Server Details
SSH_USER="root"
SSH_HOST="76.13.5.89"
REMOTE_APP_DIR="/var/www/vetscan.net/app"

# Function to run SSH commands
run_ssh() {
    ssh "$SSH_USER@$SSH_HOST" "$@"
}

cd "$PROJECT_DIR"

echo "=============================================="
echo "  VetScan VPS Deployment Script"
echo "=============================================="
echo ""

# Step 1: MANDATORY - Download backup of production database
echo "STEP 1: Downloading production database backup (MANDATORY)"
echo "----------------------------------------------"
./deploy/backup_db.sh
echo ""

# Step 2: MANDATORY - Create server-side backup
echo "STEP 2: Creating server-side backup (MANDATORY)"
echo "----------------------------------------------"
BACKUP_NAME="vet_proteins.db.backup.$(date +%Y%m%d_%H%M%S)"
run_ssh "cp $REMOTE_APP_DIR/data/vet_proteins.db $REMOTE_APP_DIR/data/$BACKUP_NAME"
echo "Server backup created: $BACKUP_NAME"
echo ""

# Step 3: Upload app files
echo "STEP 3: Uploading application files"
echo "----------------------------------------------"
rsync -avz --progress \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude 'venv' --exclude '.env' --exclude 'data/' \
  --exclude 'uploads/' --exclude 'logs/' --exclude '.DS_Store' \
  --exclude 'public_html' --exclude 'backups/' --exclude '.claude' \
  . "$SSH_USER@$SSH_HOST:$REMOTE_APP_DIR/"
echo ""

# Step 4: Restart the service using systemd
echo "STEP 4: Installing/updating Python dependencies"
echo "----------------------------------------------"
run_ssh "cd $REMOTE_APP_DIR && source venv/bin/activate && pip install -r requirements.txt"
echo "Dependencies updated"
echo ""

# Step 5: Restart the service using systemd
echo "STEP 5: Restarting VetScan service"
echo "----------------------------------------------"
run_ssh 'systemctl restart vetscan'
echo "Service restarted"
sleep 3
echo ""

# Step 6: MANDATORY - Verify database has data
echo "STEP 6: Verifying database integrity (MANDATORY)"
echo "----------------------------------------------"
ANIMAL_COUNT=$(run_ssh "cd $REMOTE_APP_DIR && source venv/bin/activate && python3 -c \"import sqlite3; conn=sqlite3.connect('data/vet_proteins.db'); print(conn.execute('SELECT COUNT(*) FROM animals').fetchone()[0])\"")
echo "Animals in database: $ANIMAL_COUNT"

if [ "$ANIMAL_COUNT" -eq "0" ]; then
    echo ""
    echo "!!! WARNING: DATABASE IS EMPTY !!!"
    echo "Something may have gone wrong. Check the backup files."
    exit 1
fi
echo ""

# Step 7: Verify server is running
echo "STEP 7: Verifying server is running"
echo "----------------------------------------------"
run_ssh 'systemctl is-active vetscan && echo "VetScan service: RUNNING" || echo "VetScan service: NOT RUNNING"'
HTTP_STATUS=$(curl -skL -o /dev/null -w "%{http_code}" https://vetscan.net/login)
echo "HTTP Status: $HTTP_STATUS"
echo ""

echo "=============================================="
echo "  Deployment Complete!"
echo "=============================================="
echo ""
echo "Backups created:"
echo "  - Local: backups/vet_proteins_*.db"
echo "  - Server: data/$BACKUP_NAME"
echo ""
echo "Server management commands:"
echo "  - Status:  ssh root@76.13.5.89 'systemctl status vetscan'"
echo "  - Logs:    ssh root@76.13.5.89 'journalctl -u vetscan -f'"
echo "  - Restart: ssh root@76.13.5.89 'systemctl restart vetscan'"
echo ""
