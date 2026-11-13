# VenomLink
## Antivenom Availability and Stock Intelligence System

> Find antivenom. Reduce delays. Prevent stock-outs.

VenomLink is a production-quality USSD, SMS, and web-based antivenom availability and stock intelligence system designed for Nigeria. Built for state health officials, health facility pharmacists, nurses, and patients' relatives operating under emergency conditions.

---

## Purpose

Snakebite envenomation kills and maims thousands of Nigerians annually. A primary cause of preventable mortality is the inability of relatives, nurses, and community health workers to rapidly locate a facility that currently holds antivenom — especially in rural endemic zones (Gombe, Plateau, Taraba, Kaduna states).

VenomLink solves this by providing:

1. **USSD-based stock update** (*384*20#) for facility pharmacists — no smartphone or internet required
2. **USSD/SMS-based antivenom search** for patients, relatives, and CHWs
3. **Deterministic multi-factor facility ranking** (distance + freshness + stock level)
4. **Real-time stock intelligence** (days of cover, expiry risk, potential stockout alerts)
5. **State Official web dashboard** with Leaflet map, operational alerts, and redistribution suggestions

---

## Technical Architecture

```
apps/
    facilities/     — Health Facility and Reporter registry
    inventory/      — Stock transaction audit ledger & case registry
    matching/       — Deterministic antivenom matching engine
    ussd/           — USSD state machine & telco gateway endpoint
    notifications/  — SMS dispatch & audit logging
    analytics/      — Days-of-cover forecasting & alert engine
    dashboard/      — State Official web dashboard views & templates

venomlink/          — Django project settings, URL router, WSGI/ASGI
static/             — CSS design system (venomlink.css), JS (map, dashboard, ussd_simulator)
templates/          — Django HTML templates
```

---

## Installation

### Requirements

- Python 3.11+
- pip / virtualenv
- Git

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/your-org/venomlink.git
cd venomlink

# 2. Create and activate virtual environment
python -m venv venv

# Windows:
venv\Scripts\activate

# Linux / macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env as needed (see Environment Configuration below)
```

---

## Environment Configuration

Copy `.env.example` to `.env` and configure:

| Variable | Default | Description |
|---|---|---|
| `DEBUG` | `True` | Django debug mode |
| `SECRET_KEY` | (insecure default) | Django secret key — **replace in production** |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hosts |
| `DATABASE_URL` | (SQLite default) | Optional PostgreSQL: `postgresql://user:pass@host:5432/dbname` |
| `DEFAULT_RESUPPLY_LEAD_TIME_DAYS` | `3` | Days to resupply assumption |
| `DEFAULT_SAFETY_BUFFER_DAYS` | `2` | Safety cushion days |
| `STALE_DATA_THRESHOLD_HOURS` | `48` | Hours before data marked stale |
| `DEMO_MODE` | `True` | Shows demonstration notice banner |

---

## Database Setup

```bash
# Default: SQLite (zero configuration, works immediately)
python manage.py migrate

# Optional: PostgreSQL (set DATABASE_URL in .env)
# CREATE DATABASE venomlink_db;
# Then run migrate
```

---

## Running Locally

```bash
# 1. Apply migrations
python manage.py migrate

# 2. Load realistic Nigerian demo data (11 facilities, cases, alerts)
python seed_demo_data.py

# 3. Create admin account
python manage.py createsuperuser

# 4. Collect static files (for production / WhiteNoise)
python manage.py collectstatic --noinput

# 5. Start the development server
python manage.py runserver 0.0.0.0:8000
```

Access:
- **Dashboard:** http://localhost:8000/
- **Admin:** http://localhost:8000/admin/ (username: admin, password from createsuperuser)
- **USSD Simulator:** http://localhost:8000/ussd-simulator/
- **90s Demo:** http://localhost:8000/demo-flow/

---

## Running Tests

```bash
python manage.py test apps.tests --verbosity=1
```

**30 tests** covering:
- Facility model properties (stock derivation, freshness, days of cover)
- Inventory transaction services (validation, stock update, case deduction)
- Matching engine (Haversine, scoring functions, ranking logic)
- USSD state machine (registered/unregistered caller menus)
- Dashboard views (HTTP 200 for all core pages)
- REST API endpoints (facility list, search)

---

## USSD Integration

The USSD gateway endpoint is at `/api/ussd/gateway/` (POST).

Expected parameters (Africa's Talking / MTN standard):

| Field | Description |
|---|---|
| `sessionId` | Unique telco session identifier |
| `serviceCode` | USSD code e.g. `*384*20#` |
| `phoneNumber` | Caller's normalised phone number |
| `text` | Accumulated user input (e.g. `1*6*03/27`) |

Response: plain text beginning with `CON ` (continuing) or `END ` (terminate).

**Interactive USSD Simulator** at http://localhost:8000/ussd-simulator/ — test without a telco.

---

## Matching Engine — Ranking Formula

The antivenom matching engine uses a transparent, deterministic multi-factor score:

```
Ranking Score =
  (Distance Score × 0.35) +
  (Freshness Score × 0.35) +
  (Stock Score × 0.30)
```

**Critical rule:** Facilities with zero stock are **never** presented as available results.

Stale data (>48h unconfirmed) is transparently labelled: *"Stock information needs confirmation"*.

---

## Days of Cover Formula

```
Days of Cover = Current Stock ÷ Average Daily Usage (30-day rolling)

If Days of Cover ≤ Resupply Lead Time + Safety Buffer → Alert: POTENTIAL_STOCKOUT
If Days of Cover ≤ (Lead Time + Buffer + 3) → Alert: LOW_STOCK
```

---

## Demo — 90-Second Story Flow

1. **Facility Update:** Pharmacist updates stock to 6 vials via USSD
2. **Patient Search:** Patient's relative dials *384*20#, selects Gombe → Kaltungo → Ranked results returned with SMS confirmation
3. **Dashboard Sync:** State official sees facility showing 6 vials (Confirmed today)
4. **Clinical Surge:** 4 vials administered for snakebite emergency
5. **Alert Triggered:** System generates CRITICAL alert: potential stockout in 2 days
6. **Official Action:** State dashboard shows alert; Redistribution suggestion with donor facility

Access the guided demo at http://localhost:8000/demo-flow/

---

## Security Notes

- Never commit `.env` to version control
- Replace `SECRET_KEY` before any production deployment
- Patient data: **no names, addresses, or phone numbers stored** — only anonymized operational counts
- USSD caller phone numbers are hashed for aggregate analytics, not stored in raw form

---

## Project Status

MVP — Demonstration-ready. All core features operational.

> **Note:** Stock data in this demonstration environment is simulated and must not be used for clinical decisions.

