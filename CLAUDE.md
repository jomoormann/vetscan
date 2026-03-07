# Instructions for Claude Code

## ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         VetScan Architecture                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  LOCAL DEVELOPMENT                     PRODUCTION (VPS)                 │
│  ─────────────────                     ────────────────                 │
│                                                                         │
│  ┌─────────────────┐                   ┌─────────────────────────────┐  │
│  │ Your Machine    │    deploy.sh      │ VPS: 76.13.5.89             │  │
│  │                 │ ────────────────► │ srv1278248.hstgr.cloud      │  │
│  │ Code: /vet_...  │                   │                             │  │
│  │ DB: data/*.db   │                   │ ┌─────────────────────────┐ │  │
│  └─────────────────┘                   │ │ nginx (port 443/80)     │ │  │
│         │                              │ │ SSL via Let's Encrypt   │ │  │
│         │ git push                     │ └───────────┬─────────────┘ │  │
│         ▼                              │             │ proxy         │  │
│  ┌─────────────────┐                   │             ▼               │  │
│  │ GitHub          │                   │ ┌─────────────────────────┐ │  │
│  │ jomoormann/     │                   │ │ gunicorn (port 8000)    │ │  │
│  │ vetscan         │                   │ │ + uvicorn workers       │ │  │
│  └─────────────────┘                   │ │ systemd: vetscan.service│ │  │
│                                        │ └───────────┬─────────────┘ │  │
│                                        │             │               │  │
│                                        │             ▼               │  │
│                                        │ ┌─────────────────────────┐ │  │
│                                        │ │ FastAPI Application     │ │  │
│                                        │ │ /var/www/vetscan.net/app│ │  │
│                                        │ └───────────┬─────────────┘ │  │
│                                        │             │               │  │
│                                        │             ▼               │  │
│                                        │ ┌─────────────────────────┐ │  │
│                                        │ │ SQLite Database         │ │  │
│                                        │ │ data/vet_proteins.db    │ │  │
│                                        │ └─────────────────────────┘ │  │
│                                        └─────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## CURRENT PRODUCTION SERVER (USE THIS)

| Property | Value |
|----------|-------|
| **Type** | Hostinger VPS |
| **Hostname** | srv1278248.hstgr.cloud |
| **IP Address** | 76.13.5.89 |
| **SSH User** | root |
| **SSH Port** | 22 (default) |
| **SSH Command** | `ssh root@76.13.5.89` |
| **Domain** | vetscan.net |
| **App Path** | /var/www/vetscan.net/app/ |
| **Database** | /var/www/vetscan.net/app/data/vet_proteins.db |
| **Service** | vetscan.service (systemd) |
| **Web Server** | nginx with SSL (Let's Encrypt) |
| **App Server** | gunicorn with uvicorn workers |
| **Workers** | 2 |
| **Auto-restart** | Yes (systemd Restart=always) |

---

## OLD SHARED HOSTING (DO NOT USE)

**This server is DEPRECATED. Do not deploy to it.**

| Property | Value |
|----------|-------|
| **Type** | Hostinger Shared Hosting |
| **IP Address** | 82.198.229.40 |
| **SSH User** | u618294093 |
| **SSH Port** | 65002 |
| **SSH Key** | deploy/vetscan_ssh_key |
| **App Path** | ~/domains/vetscan.net/app/ |
| **Status** | DEPRECATED - DNS no longer points here |

**Why we migrated:** Shared hosting had process management issues. Gunicorn workers were being killed by the hosting provider, causing repeated server crashes.

---

## GITHUB REPOSITORY

| Property | Value |
|----------|-------|
| **URL** | https://github.com/jomoormann/vetscan |
| **Branch** | main |
| **Owner** | jomoormann |

### What's in GitHub:
- All application source code (`src/`, `templates/`, `static/`, `translations/`)
- Deployment scripts (`deploy/deploy.sh`, `deploy/backup_db.sh`)
- Documentation (`CLAUDE.md`, `deploy/DEPLOYMENT_GUIDE.md`)
- Configuration files (`requirements.txt`, `gunicorn.conf.py`)

### What's NOT in GitHub (gitignored):
- `.env` (contains API keys and passwords)
- `data/` (database files)
- `backups/` (database backups)
- `uploads/` (user uploaded files)
- `logs/` (application logs)
- `deploy/vetscan_ssh_key` (old SSH key)
- `venv/` (Python virtual environment)

---

## CRITICAL RULES

### 1. DEPLOYMENT

**ALWAYS USE:**
```bash
./deploy/deploy.sh
```

**NEVER:**
- Run rsync manually
- SSH and restart services manually for deployment
- Skip backup steps
- Deploy to the old shared hosting (82.198.229.40)

### 2. DATABASE PROTECTION

**Production database location:** `/var/www/vetscan.net/app/data/vet_proteins.db`

**NEVER:**
- Copy local database to server
- Run DROP TABLE, DELETE FROM, TRUNCATE on production
- Delete the data/ directory on server
- Run rsync without `--exclude 'data/'`

**ALWAYS:**
- Create backups before any deployment (deploy.sh does this automatically)
- Use ALTER TABLE ADD COLUMN for schema changes
- Use UPDATE with WHERE clause for modifications

### 3. SIGNAL HANDLERS

**NEVER add signal handlers to web_server.py or any ASGI application.**

Gunicorn uses SIGTERM/SIGHUP to manage workers. Custom signal handlers interfere with this and cause crashes. This was the root cause of server crashes on 2026-01-22/23.

---

## SERVER MANAGEMENT QUICK REFERENCE

```bash
# SSH into VPS
ssh root@76.13.5.89

# Check service status
ssh root@76.13.5.89 'systemctl status vetscan'

# View live logs
ssh root@76.13.5.89 'journalctl -u vetscan -f'

# View error logs
ssh root@76.13.5.89 'tail -50 /var/www/vetscan.net/app/logs/error.log'

# Restart service
ssh root@76.13.5.89 'systemctl restart vetscan'

# Check database
ssh root@76.13.5.89 'cd /var/www/vetscan.net/app && source venv/bin/activate && python3 -c "import sqlite3; conn=sqlite3.connect(\"data/vet_proteins.db\"); print(\"Animals:\", conn.execute(\"SELECT COUNT(*) FROM animals\").fetchone()[0])"'

# Check for zombie workers
ssh root@76.13.5.89 'ps aux | grep gunicorn'

# Check slow request logs
ssh root@76.13.5.89 'grep -i "slow" /var/www/vetscan.net/app/logs/error.log | tail -20'

# Run server monitor manually
ssh root@76.13.5.89 'python3 /opt/server-monitor/monitor.py'
```

---

## DEPLOYMENT FLOW

```
1. Developer runs: ./deploy/deploy.sh
                        │
                        ▼
2. backup_db.sh downloads production DB to backups/
                        │
                        ▼
3. Server-side backup created on VPS
                        │
                        ▼
4. rsync uploads code (excludes data/, .env, logs/)
                        │
                        ▼
5. systemctl restart vetscan
                        │
                        ▼
6. Verify database has data (fail if empty)
                        │
                        ▼
7. Verify HTTP 200 from https://vetscan.net/login
```

---

## PROJECT STRUCTURE

```
vet_protein_app/
├── src/                    # Python application code
│   ├── web_server.py       # FastAPI application entry point
│   ├── app.py              # Application factory
│   ├── models.py           # Data models
│   ├── database/           # Database layer
│   ├── api/                # API routes
│   └── middleware/         # Request middleware
├── templates/              # Jinja2 HTML templates
├── static/                 # CSS, JS, images
├── translations/           # i18n JSON files (en.json, pt.json)
├── data/                   # Local database (NEVER DEPLOY)
├── backups/                # Downloaded production DB backups
├── deploy/                 # Deployment scripts
│   ├── deploy.sh           # Main deployment script
│   ├── backup_db.sh        # Database backup script
│   ├── DEPLOYMENT_GUIDE.md # Deployment documentation
│   └── vetscan_ssh_key     # OLD shared hosting key (deprecated)
├── CLAUDE.md               # THIS FILE - instructions for Claude
├── requirements.txt        # Python dependencies
└── gunicorn.conf.py        # Gunicorn configuration
```

---

## TECHNOLOGY STACK

| Layer | Technology |
|-------|------------|
| **Frontend** | Jinja2 templates, vanilla JS |
| **Backend** | FastAPI (Python) |
| **Database** | SQLite |
| **ASGI Server** | uvicorn (via gunicorn workers) |
| **Process Manager** | gunicorn |
| **Service Manager** | systemd |
| **Web Server/Proxy** | nginx |
| **SSL** | Let's Encrypt (certbot) |
| **Hosting** | Hostinger VPS |
| **Version Control** | GitHub |

---

## INCIDENT HISTORY

### 2026-01-22/23: Server Crash Loop

**Symptom:** Server repeatedly crashed after deployment, returning empty responses.

**Root Cause:** Custom signal handlers in `web_server.py` intercepted SIGTERM/SIGHUP signals that gunicorn uses to manage workers. When gunicorn sent SIGTERM to recycle a worker, the custom handler called `sys.exit(0)`, causing gunicorn to think the worker was unresponsive and kill it with SIGKILL.

**Fix:** Removed all custom signal handlers from `src/web_server.py`.

**Prevention:** Never add signal handlers to ASGI applications running under gunicorn.

### 2026-01-23: Migration to VPS

**Reason:** Shared hosting (82.198.229.40) had unreliable process management. Migrated to dedicated VPS (76.13.5.89) with systemd for automatic restarts.

### 2026-01-25/27: Zombie Worker Issue

**Symptom:** Site became unresponsive. Gunicorn master process was running but workers were defunct (zombie processes).

**Root Cause:** Gunicorn workers timed out (requests exceeding 120-second timeout) and became zombies. Gunicorn logged "Unhandled signal: cld" and failed to spawn replacement workers. This is a known issue with gunicorn + uvicorn workers.

**Immediate Fix:** Restart the vetscan service (`systemctl restart vetscan`).

**Preventive Measures Implemented:**
1. **Server Monitor** (`/opt/server-monitor/monitor.py`) - Runs every 5 minutes via systemd timer, checks both vetscan and joestdigital services. Auto-restarts vetscan when zombie workers are detected. Sends email alerts to jo.moormann@gmail.com.
2. **Swap Space** - Added 2GB swap to prevent OOM kills under memory pressure.
3. **Request Timing Middleware** - Added `TimingMiddleware` to `web_server.py` that logs slow requests (WARNING for ≥10s, INFO for ≥5s) to help identify which endpoints cause timeouts.

**To investigate slow requests:**
```bash
ssh root@76.13.5.89 'grep -i "slow" /var/www/vetscan.net/app/logs/error.log'
ssh root@76.13.5.89 'journalctl -u vetscan | grep -i slow'
```

---

## SERVER MONITORING

A monitoring system runs on the VPS to detect and auto-fix service issues.

| Property | Value |
|----------|-------|
| **Script** | `/opt/server-monitor/monitor.py` |
| **Timer** | `server-monitor.timer` (every 5 minutes) |
| **Alert Email** | jo.moormann@gmail.com |
| **Services Monitored** | vetscan.service, joestdigital.service |

**What it checks:**
- Service running status (systemctl is-active)
- HTTP response on localhost endpoints
- Zombie workers (for gunicorn)

**Auto-fix behavior:**
- When zombie workers are detected on vetscan, automatically restarts the service
- Sends `[AUTO-FIX]` email notification

**Email alert types:**
- `[AUTO-FIX]` - Issue detected and automatically resolved
- `[ALERT]` - Issue that couldn't be auto-fixed (requires manual intervention)
- `[RECOVERED]` - Service returned to healthy state

**Useful commands:**
```bash
# Check monitor timer status
ssh root@76.13.5.89 'systemctl list-timers server-monitor.timer'

# Run monitor manually
ssh root@76.13.5.89 'python3 /opt/server-monitor/monitor.py'

# View monitor logs
ssh root@76.13.5.89 'journalctl -u server-monitor.service -n 20'

# Check current swap usage
ssh root@76.13.5.89 'free -h'
```

---

## COMMON MISTAKES TO AVOID

1. **Deploying to wrong server** - Always use `./deploy/deploy.sh` which targets the VPS
2. **Using old SSH credentials** - VPS uses `root@76.13.5.89`, not the old shared hosting
3. **Overwriting production database** - Never copy local DB to server
4. **Adding signal handlers** - Gunicorn manages signals, don't interfere
5. **Manual deployment** - Always use the deployment script for backups
6. **Skipping backups** - The deploy script enforces backups for a reason
7. **Ignoring zombie workers** - If `ps aux | grep gunicorn` shows `<defunct>` processes, restart the service immediately
8. **Long-running requests** - Avoid synchronous operations that could exceed the 120-second worker timeout

---

## CONTACTS & RESOURCES

- **GitHub:** https://github.com/jomoormann/vetscan
- **Production Site:** https://vetscan.net
- **VPS Provider:** Hostinger
- **Deployment Guide:** `deploy/DEPLOYMENT_GUIDE.md`
