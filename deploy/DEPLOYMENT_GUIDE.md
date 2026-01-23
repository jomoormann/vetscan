# VetScan Deployment Guide

## Server Information

**VPS Server:** srv1278248.hstgr.cloud (Hostinger VPS)
- IP: 76.13.5.89
- User: root
- App Path: /var/www/vetscan.net/app/
- Service: vetscan.service (systemd)

## How to Deploy

**Run this single command from the project root:**

```bash
./deploy/deploy.sh
```

That's it. The script handles everything automatically:
- Downloads local backup of production database
- Creates server-side backup
- Uploads code (safely excludes data/)
- Restarts vetscan service via systemd
- Verifies database integrity
- Verifies server is running

## NEVER Deploy Manually

**DO NOT** run rsync, ssh, or systemctl commands directly.
**DO NOT** try to "speed up" deployment by skipping steps.
**ALWAYS** use `./deploy/deploy.sh`

The script exists to protect the production database. Manual deployment risks data loss.

---

## Server Management Commands

```bash
# Check service status
ssh root@76.13.5.89 'systemctl status vetscan'

# View live logs
ssh root@76.13.5.89 'journalctl -u vetscan -f'

# View application logs
ssh root@76.13.5.89 'tail -50 /var/www/vetscan.net/app/logs/error.log'

# Restart service
ssh root@76.13.5.89 'systemctl restart vetscan'

# Check database record count
ssh root@76.13.5.89 'cd /var/www/vetscan.net/app && source venv/bin/activate && python3 -c "import sqlite3; conn=sqlite3.connect(\"data/vet_proteins.db\"); print(\"Animals:\", conn.execute(\"SELECT COUNT(*) FROM animals\").fetchone()[0])"'
```

---

## Database Protection

**Location:** `/var/www/vetscan.net/app/data/vet_proteins.db`

### FORBIDDEN:
- DROP TABLE, DELETE FROM, TRUNCATE on production
- Copying local database to server
- Deleting data/ directory on server

### SAFE:
- ALTER TABLE ADD COLUMN
- INSERT, UPDATE with WHERE clause
- Schema migrations that preserve data

---

## Backups

Local backups are stored in: `backups/vet_proteins_YYYYMMDD_HHMMSS.db`
Server backups are stored in: `/var/www/vetscan.net/app/data/vet_proteins.db.backup.*`

---

## Service Configuration

The systemd service is configured at `/etc/systemd/system/vetscan.service`:
- Uses gunicorn with uvicorn workers
- 2 workers for better stability
- Auto-restarts on failure
- Logs to /var/www/vetscan.net/app/logs/

## Nginx Configuration

SSL/TLS is configured at `/etc/nginx/sites-available/vetscan`:
- Certbot handles SSL certificates automatically
- Proxies to gunicorn on port 8000

---

## Issue: Signal Handler Bug (RESOLVED 2026-01-23)

**Root cause of server crashes:** Custom signal handlers in web_server.py interfered with gunicorn's process management. Gunicorn uses SIGTERM/SIGHUP to manage workers - custom handlers intercepted these signals and called sys.exit(0), causing gunicorn to kill workers with SIGKILL.

**Fix:** Removed all custom signal handlers from src/web_server.py. Never add signal handlers to an application running under gunicorn.
