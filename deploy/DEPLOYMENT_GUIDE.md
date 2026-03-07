# VetScan Deployment Guide

## Server Information

**VPS Server:** srv1278248.hstgr.cloud (Hostinger VPS)
- **IP:** 76.13.5.89
- **User:** root
- **Domain:** vetscan.net
- **App Path:** /var/www/vetscan.net/app/
- **Database:** /var/www/vetscan.net/app/data/vet_proteins.db
- **Service:** vetscan.service (systemd, auto-restarts on failure)
- **Web Server:** nginx with SSL (Let's Encrypt)

---

## How to Deploy

**Run this single command from the project root:**

```bash
./deploy/deploy.sh
```

The script handles everything automatically:
1. Downloads local backup of production database
2. Creates server-side backup
3. Uploads code (safely excludes data/, .env, logs/)
4. Restarts vetscan service via systemd
5. Verifies database integrity (fails if empty)
6. Verifies server is running

### NEVER Deploy Manually

- **DO NOT** run rsync, ssh, or systemctl commands for deployment
- **DO NOT** skip backup steps to "speed up" deployment
- **ALWAYS** use `./deploy/deploy.sh`

The script exists to protect the production database. Manual deployment risks data loss.

---

## Backup System

### Automatic Backups (During Deployment)

Every deployment creates two backups automatically:

1. **Local backup** (downloaded to your machine):
   ```
   backups/vet_proteins_YYYYMMDD_HHMMSS.db
   ```

2. **Server-side backup** (on VPS):
   ```
   /var/www/vetscan.net/app/data/vet_proteins.db.backup.YYYYMMDD_HHMMSS
   ```

### Manual Backup

To download a backup without deploying:

```bash
./deploy/backup_db.sh
```

This downloads the production database to `backups/vet_proteins_YYYYMMDD_HHMMSS.db`

### Restore from Backup

**On VPS (server-side backup):**
```bash
ssh root@76.13.5.89
cd /var/www/vetscan.net/app/data
# List available backups
ls -la vet_proteins.db.backup.*
# Restore (replace TIMESTAMP with actual backup name)
cp vet_proteins.db.backup.TIMESTAMP vet_proteins.db
systemctl restart vetscan
```

**From local backup:**
```bash
# Copy local backup to VPS
scp backups/vet_proteins_YYYYMMDD_HHMMSS.db root@76.13.5.89:/var/www/vetscan.net/app/data/vet_proteins.db
# Restart service
ssh root@76.13.5.89 'systemctl restart vetscan'
```

### Backup Locations Summary

| Location | Path | Purpose |
|----------|------|---------|
| Local machine | `backups/vet_proteins_*.db` | Downloaded before each deployment |
| VPS server | `/var/www/vetscan.net/app/data/vet_proteins.db.backup.*` | Created on server before each deployment |

---

## Server Management Commands

```bash
# Check service status
ssh root@76.13.5.89 'systemctl status vetscan'

# View live systemd logs
ssh root@76.13.5.89 'journalctl -u vetscan -f'

# View application error logs
ssh root@76.13.5.89 'tail -50 /var/www/vetscan.net/app/logs/error.log'

# View access logs
ssh root@76.13.5.89 'tail -50 /var/www/vetscan.net/app/logs/access.log'

# Restart service
ssh root@76.13.5.89 'systemctl restart vetscan'

# Check database record count
ssh root@76.13.5.89 'cd /var/www/vetscan.net/app && source venv/bin/activate && python3 -c "import sqlite3; conn=sqlite3.connect(\"data/vet_proteins.db\"); print(\"Animals:\", conn.execute(\"SELECT COUNT(*) FROM animals\").fetchone()[0])"'

# SSH into VPS
ssh root@76.13.5.89
```

---

## Database Protection

**Production database:** `/var/www/vetscan.net/app/data/vet_proteins.db`

### FORBIDDEN Operations:
- DROP TABLE, DELETE FROM, TRUNCATE on production
- Copying local database to server (overwrites production data)
- Deleting data/ directory on server
- Any rsync without `--exclude 'data/'`

### SAFE Operations:
- ALTER TABLE ADD COLUMN (adds new fields, preserves data)
- INSERT (adds new records)
- UPDATE with WHERE clause (modifies specific records)
- Schema migrations that preserve existing data

---

## Service Configuration

**Systemd service:** `/etc/systemd/system/vetscan.service`

- **Process manager:** gunicorn with uvicorn workers
- **Workers:** 2 (for stability and concurrent requests)
- **Auto-restart:** Yes (RestartSec=5)
- **Bind:** 127.0.0.1:8000 (nginx proxies to this)
- **Logs:** /var/www/vetscan.net/app/logs/
- **Gunicorn version:** pinned to 24.1.1 in `requirements.txt`
- **Worker recycling:** disabled (`max_requests=0`) to avoid SIGCLD-related worker replacement issues
- **Access logs:** include timestamps via `--access-logformat`

To modify the service:
```bash
ssh root@76.13.5.89 'nano /etc/systemd/system/vetscan.service'
ssh root@76.13.5.89 'systemctl daemon-reload && systemctl restart vetscan'
```

After updating gunicorn settings or versions:
```bash
ssh root@76.13.5.89 'cd /var/www/vetscan.net/app && source venv/bin/activate && pip install -r requirements.txt'
ssh root@76.13.5.89 'systemctl daemon-reload && systemctl restart vetscan'
```

---

## Nginx Configuration

**Config file:** `/etc/nginx/sites-available/vetscan`

- SSL certificates managed by Certbot (Let's Encrypt)
- HTTP automatically redirects to HTTPS
- Proxies all requests to gunicorn on port 8000
- Static files served directly from /var/www/vetscan.net/app/static/

To modify nginx:
```bash
ssh root@76.13.5.89 'nano /etc/nginx/sites-available/vetscan'
ssh root@76.13.5.89 'nginx -t && systemctl reload nginx'
```

---

## Troubleshooting

### Site is down

1. Check if service is running:
   ```bash
   ssh root@76.13.5.89 'systemctl status vetscan'
   ```

2. Check logs for errors:
   ```bash
   ssh root@76.13.5.89 'journalctl -u vetscan -n 50'
   ```

3. Restart the service:
   ```bash
   ssh root@76.13.5.89 'systemctl restart vetscan'
   ```

### Unhandled signal: cld / worker timeouts

**Symptoms:** `Unhandled signal: cld` followed by `WORKER TIMEOUT` in `logs/error.log`, then a service restart.

**Mitigations in place:**
- Gunicorn pinned to 24.1.1 (includes SIGCLD fix in 24.1.0)
- Worker recycling disabled (`max_requests=0`)

**If you re-enable recycling:**
- Keep gunicorn at 24.1.0+ and monitor for SIGCLD warnings
- Ensure access logs include timestamps to correlate with slow requests

### Database is empty after deployment

1. Check server-side backups:
   ```bash
   ssh root@76.13.5.89 'ls -la /var/www/vetscan.net/app/data/vet_proteins.db.backup.*'
   ```

2. Restore from most recent backup (see Restore from Backup section)

### Cannot SSH to server

- Verify your SSH key is set up: `ssh-add -l`
- Check if VPS is reachable: `ping 76.13.5.89`
- Contact Hostinger support if VPS is unreachable

---

## Known Issues (Resolved)

### Signal Handler Bug (2026-01-23)

**Problem:** Server kept crashing repeatedly after deployment.

**Root cause:** Custom signal handlers in web_server.py intercepted SIGTERM/SIGHUP signals that gunicorn uses to manage workers. This caused gunicorn to kill workers with SIGKILL.

**Fix:** Removed all custom signal handlers from src/web_server.py.

**Prevention:** Never add signal handlers (SIGTERM, SIGINT, SIGHUP) to ASGI applications running under gunicorn.
