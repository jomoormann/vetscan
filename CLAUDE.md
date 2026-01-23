# Instructions for Claude Code

## CRITICAL: DEPLOYMENT PROCEDURE

**ALWAYS USE THE DEPLOYMENT SCRIPT. NEVER DEPLOY MANUALLY.**

```bash
./deploy/deploy.sh
```

This script automatically:
1. Downloads a local backup of the production database
2. Creates a server-side backup
3. Uploads code (excluding data/)
4. Restarts the vetscan systemd service
5. Verifies database integrity
6. Verifies server is running

**DO NOT** run rsync, systemctl, or ssh commands for deployment manually. Use the script.

---

## Server Information

**VPS Server:** srv1278248.hstgr.cloud (Hostinger VPS)
- IP: 76.13.5.89
- User: root
- App Path: /var/www/vetscan.net/app/
- Service: vetscan.service (systemd - auto-restarts on failure)

---

## CRITICAL: DATABASE PROTECTION

**THE PRODUCTION DATABASE MUST NEVER BE DELETED, OVERWRITTEN, OR MODIFIED DESTRUCTIVELY.**

### Database Locations:
- **Production:** `/var/www/vetscan.net/app/data/vet_proteins.db` (on VPS)
- **Local:** `data/vet_proteins.db` (for development only - NEVER upload to server)

### FORBIDDEN Actions:
- **NEVER** run rsync without `--exclude 'data/'`
- **NEVER** delete the `data/` directory on the server
- **NEVER** copy a local database to the server
- **NEVER** run DROP TABLE, DELETE FROM, or TRUNCATE commands on production

### MANDATORY Backup Rules (enforced by deploy.sh):
1. Download local backup BEFORE deployment → `backups/vet_proteins_*.db`
2. Create server-side backup BEFORE deployment
3. Verify database has data AFTER deployment

### Safe Database Operations:
- ALTER TABLE ADD COLUMN - Safe (adds new fields)
- INSERT - Safe (adds new records)
- UPDATE with WHERE clause - Safe if careful
- Schema migrations that preserve data - Safe

### FORBIDDEN Database Operations (without explicit user approval):
- DROP TABLE
- DELETE FROM (without WHERE)
- TRUNCATE
- Replacing the database file
- Any operation that could lose production data

---

## CRITICAL: DO NOT ADD SIGNAL HANDLERS

**NEVER add signal handlers (SIGTERM, SIGINT, SIGHUP) to web_server.py or any ASGI application running under gunicorn.**

Gunicorn uses these signals to manage worker processes. Custom handlers interfere with gunicorn's process management and cause worker crashes.

This was the root cause of the server crashes on 2026-01-22/23. The fix was to remove all signal handlers.

---

## Deployment

See `deploy/DEPLOYMENT_GUIDE.md` for full deployment instructions.

Quick server management:
```bash
# Status
ssh root@76.13.5.89 'systemctl status vetscan'

# Logs
ssh root@76.13.5.89 'journalctl -u vetscan -f'

# Restart
ssh root@76.13.5.89 'systemctl restart vetscan'
```

---

## Project Structure

- `src/` - Python application code
- `templates/` - Jinja2 HTML templates
- `static/` - CSS, JS, images
- `translations/` - i18n JSON files (en.json, pt.json)
- `data/` - Local database (DO NOT DEPLOY)
- `backups/` - Downloaded production database backups
- `deploy/` - Deployment scripts
- `public_html/` - (Legacy - shared hosting only)
