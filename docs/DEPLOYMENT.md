# VenomLink Deployment, Hardening & Recovery Guide

Operational runbook for deploying VenomLink in a **controlled pilot** or **production** environment backed by PostgreSQL, WhiteNoise static asset serving, and a TLS-terminating reverse proxy.

---

## 1. Runtime Prerequisites

- Python 3.11+ (tested on Python 3.14 + Django 6.1.3)
- PostgreSQL 15+ or 16+ (verified against **PostgreSQL 16.4**)
- Python dependencies from `requirements.txt` (`Django`, `djangorestframework`, `django-cors-headers`, `whitenoise`, `psycopg[binary]`, `gunicorn`, `waitress`, `requests`)
- Reverse proxy capable of TLS termination (`nginx` or `caddy`)

> **Important:** SQLite is **prohibited** when `DEBUG=False` (`settings.py` raises `ImproperlyConfigured`). PostgreSQL is mandatory in pilot/production so `SELECT ... FOR UPDATE` row-level locks prevent negative stock and race conditions.

---

## 2. Production PostgreSQL Setup

Create a dedicated least-privilege PostgreSQL role and database (adjust host, strong password, and SSL settings):

```sql
CREATE ROLE venomlink WITH LOGIN PASSWORD '<STRONG_DB_PASSWORD>';
CREATE DATABASE venomlink_db OWNER venomlink;
REVOKE ALL ON DATABASE venomlink_db FROM PUBLIC;
GRANT ALL PRIVILEGES ON DATABASE venomlink_db TO venomlink;
```

Connection string format:

```text
DATABASE_URL=postgresql://venomlink:<STRONG_DB_PASSWORD>@127.0.0.1:5432/venomlink_db?sslmode=require
```

- `DB_CONN_MAX_AGE=60` keeps persistent connections warm without exhausting PostgreSQL slots.
- If running PostgreSQL and Django on the same host without local PostgreSQL TLS certificates, use `?sslmode=prefer` or `DB_SSLMODE=prefer`. For remote DB servers, enforce `sslmode=require` (or `verify-full`).

---

## 3. Environment Variable Reference

Copy `.env.example` and provide secure values (prefer systemd `EnvironmentFile` or secret manager over `.env` on shared hosts). Critical variables verified by `venomlink/settings.py`:

| Variable | Pilot / Production Requirement | Notes |
|---|---|---|
| `DEBUG` | `False` | Enables HSTS/cookie flags, disables `BrowsableAPIRenderer` & traceback HTML |
| `SECRET_KEY` | Random >= 50 chars, no placeholder | App refuses to start if shorter than 50 chars or matching default |
| `ALLOWED_HOSTS` | Explicit hostnames (no `*`) | e.g. `venomlink.gombe.health.gov.ng` |
| `CSRF_TRUSTED_ORIGINS` | `https://<domain>` | Comma-separated origins with scheme |
| `DATABASE_URL` | `postgresql://...` | Required when `DEBUG=False` |
| `CACHE_BACKEND` | `db` (multi-worker) or `locmem` | Run `python manage.py createcachetable` when using `db` |
| `DEMO_MODE` | `False` | Disables `/demo/`, `/api/ussd/simulate/`, and public SMS logs (`403`) |
| `USSD_GATEWAY_TOKEN` | Random high-entropy token | Enforced on `POST /api/ussd/gateway/` (`X-Gateway-Token` / `Authorization`) |
| `NUM_PROXIES` | `1` (or count of trusted proxies) | Ensures `X-Forwarded-For` IP parsing for rate limits (`60/min` search, `10/min` PIN) |
| `TRUST_PROXY_SSL_HEADER` | `True` (behind TLS proxy only) | Honours `X-Forwarded-Proto: https` |
| `SECURE_SSL_REDIRECT` | `True` (exempts `/healthz/`, `/readyz/`) | Keep `True` in HTTPS prod |
| `SECURE_HSTS_SECONDS` | `3600` initially, raise to `31536000` after TLS check | `settings.py` defaults to `3600` when `DEBUG=False` |

---

## 4. Database Migrations, Cache Table & Static Files

Run these steps cleanly before starting or restarting application workers:

```bash
# 1. Verify production config sanity (0 issues expected when HTTPS/DB/Secret env vars are set)
python manage.py check --deploy

# 2. Apply schema migrations
python manage.py migrate --noinput

# 3. Create database cache table (required if CACHE_BACKEND=db)
python manage.py createcachetable

# 4. Collect static assets into STATIC_ROOT (staticfiles/) for WhiteNoise
python manage.py collectstatic --noinput
```

With `DEBUG=False`, `venomlink/settings.py` automatically configures `WHITENOISE_USE_FINDERS = False` and `WHITENOISE_AUTOREFRESH = False` so WhiteNoise serves immutable assets directly from `staticfiles/`.

---

## 5. Application Startup Instructions

### Linux (Production — Gunicorn)

Bind Gunicorn to localhost behind Nginx/Caddy (`--workers` typically `2 * CPU + 1`):

```bash
gunicorn venomlink.wsgi:application \
  --bind 127.0.0.1:8000 \
  --workers 3 \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -
```

### Windows (Pilot / Controlled Server — Waitress)

`gunicorn` does not support Windows sockets natively; use `waitress` (included in `requirements.txt`):

```powershell
.\venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8000 --threads=8 venomlink.wsgi:application
```

---

## 6. Reverse Proxy & HTTPS Configuration (Nginx Example)

Terminate TLS at Nginx/Caddy and forward requests to `127.0.0.1:8000`. Do **not** expose Waitress/Gunicorn directly to the public internet.

```nginx
server {
    listen 80;
    server_name venomlink.example.gov.ng;

    # Local container/load-balancer HTTP probes
    location ~ ^/(healthz|readyz)/$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name venomlink.example.gov.ng;

    ssl_certificate     /etc/letsencrypt/live/venomlink.example.gov.ng/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/venomlink.example.gov.ng/privkey.pem;

    client_max_body_size 2M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Set `TRUST_PROXY_SSL_HEADER=True` and `NUM_PROXIES=1` in the application environment when using this Nginx configuration.

---

## 7. Health-Check Endpoints & Verification

Implemented in `venomlink/health.py` and exempt from `SECURE_SSL_REDIRECT`:

- **Liveness:** `GET /healthz/`
  - Expected: `HTTP 200`, `{"status": "ok", "service": "venomlink"}`
- **Readiness:** `GET /readyz/`
  - Probes default DB (`SELECT 1`) and Django `cache` (`set`/`get`).
  - Healthy: `HTTP 200`, `{"status": "ready", "database": "ok", "database_vendor": "postgresql", "cache": "ok"}`
  - Degraded: `HTTP 503`, `{"status": "unavailable", "database": "error"|"ok", ...}` (no stack traces or credentials exposed)

Verification commands:

```bash
curl -fsS http://127.0.0.1:8000/healthz/
curl -fsS http://127.0.0.1:8000/readyz/
```

---

## 8. Backup & Recovery Procedure

### Automated Logical Backup (`pg_dump`)

Run nightly (and **always** before any schema migration or bulk seed):

```bash
pg_dump --format=custom --no-owner --no-privileges \
  --file="backups/venomlink_db_$(date +%Y%m%d_%H%M%S).dump" \
  "postgresql://venomlink:<STRONG_DB_PASSWORD>@127.0.0.1:5432/venomlink_db"
```

Store encrypted off-host copies in restricted storage; never store unencrypted dumps inside the served web root.

### Recovery Drill (`pg_restore`)

1. Stop application workers so no concurrent writes occur during restore.
2. Restore into a fresh database (recommended) or clean target DB:

```bash
createdb -O venomlink venomlink_db_restore
pg_restore --no-owner --role=venomlink --dbname=venomlink_db_restore \
  backups/venomlink_db_<TIMESTAMP>.dump
```

3. Validate row counts (`Facility`, `InventoryTransaction`, `SnakebiteCase`, `Alert`), run `python manage.py migrate --check`, switch `DATABASE_URL` or swap database names, restart workers, and verify `/readyz/` returns `HTTP 200`.
