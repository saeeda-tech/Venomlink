# VenomLink External Integration Guide (Africa's Talking USSD & SMS)

This document describes the current implementation and deployment requirements for integrating VenomLink with **Africa's Talking** (and compatible Nigerian Telco USSD/SMS gateways).

> **Verification Status Notice:** No live Africa's Talking credentials (`AT_API_KEY`, `USSD_GATEWAY_TOKEN`) or live sandbox/telco endpoints were available during Phase 3 validation. USSD inbound webhook parsing, session/PIN security, and simulator workflows have been verified end-to-end against Django + PostgreSQL test clients; **outbound SMS delivery currently operates in local `SIMULATED` audit mode** (`apps/notifications/sms_gateway.py`) and has **not** been tested against a live external Africa's Talking API endpoint.

---

## 1. Required Credentials & Environment Variables

Defined in `venomlink/settings.py`:

| Environment Variable | Purpose | Default / Example |
|---|---|---|
| `USSD_GATEWAY_TOKEN` | Shared webhook secret sent by the USSD gateway / proxy in `X-Gateway-Token` or `Authorization` (`Bearer`/`Basic`) header | `""` (empty bypasses header check — **must be set in pilot/prod**) |
| `AT_USERNAME` | Africa's Talking application username (`sandbox` or live app username) | `""` |
| `AT_API_KEY` | Africa's Talking REST API key (keep secret; inject via environment/secret manager) | `""` |
| `AT_SENDER_ID` | Registered Alphanumeric Sender ID or shortcode | `""` (e.g. `VenomLink`) |
| `AT_SMS_URL` | Outbound SMS endpoint; auto-selects sandbox when `AT_USERNAME=sandbox` | `https://api.africastalking.com/version1/messaging` or `https://api.sandbox.africastalking.com/version1/messaging` |
| `AT_TIMEOUT_SECONDS` | HTTP request timeout for outbound SMS calls | `10` |
| `USSD_SESSION_TIMEOUT_SECONDS` | Per-session inactivity/lifetime cutoff | `180` |
| `USSD_MAX_PIN_ATTEMPTS` | Consecutive wrong Staff PIN attempts before lockout | `3` |
| `USSD_LOCKOUT_MINUTES` | Lockout duration after failed PIN threshold | `15` |

---

## 2. Required Callback URLs

| Direction | Endpoint | Method | Purpose |
|---|---|---|---|
| **Inbound (AT -> VenomLink)** | `https://<domain>/api/ussd/gateway/` | `POST` | Live USSD session callback (`ussd_gateway_callback`) |
| **Inbound (Optional Dev Simulator)** | `https://<domain>/api/ussd/simulate/` | `POST` | Browser USSD handset simulator; **disabled (`403`) when `DEMO_MODE=False`** |
| **Outbound (VenomLink -> AT)** | `${AT_SMS_URL}` | `POST` | SMS dispatch for antivenom search results and alerts *(see Section 6)* |

Configure the USSD callback URL in the Africa's Talking Dashboard under **USSD -> Service Codes -> Callback URL** pointing to `https://<your-domain>/api/ussd/gateway/`. Ensure TLS termination is handled by your reverse proxy so Africa's Talking connects over HTTPS.

---

## 3. Expected Request & Response Formats (USSD Webhook)

### Inbound Request (`POST /api/ussd/gateway/`)

Content-Type: `application/x-www-form-urlencoded` (standard Africa's Talking webhook format).

| Field | Accepted Keys | Required | Validation (`apps/ussd/views.py`) |
|---|---|---|---|
| Session ID | `sessionId` or `session_id` | Yes | Non-empty, max length `128` |
| Service Code | `serviceCode` or `service_code` | Yes | Non-empty (e.g. `*384*20#`) |
| Caller MSISDN | `phoneNumber` or `phone_number` | Yes | Non-empty, max length `30`; normalised to `+234...` |
| Accumulated Input | `text` | Optional | Defaults to `""`, max length `500`, `*`-delimited steps |

### Outbound Response

- Content-Type: `text/plain; charset=utf-8`
- Continuing menu: `CON <menu prompt>`
- Terminal response: `END <final message>`

---

## 4. Authentication & Webhook Validation

1. **HTTP Method Enforcement:** Non-`POST` requests (e.g. `GET`) to `/api/ussd/gateway/` are rejected with `HTTP 405 Method Not Allowed`.
2. **Gateway Authentication (`_verify_gateway_auth`):**
   - Reads `settings.VENOMLINK_CONFIG['USSD_GATEWAY_TOKEN']`.
   - Extracts `X-Gateway-Token` (`HTTP_X_GATEWAY_TOKEN`) or `Authorization` (`HTTP_AUTHORIZATION`, stripping `Bearer ` or `Basic ` prefix).
   - Validates using constant-time `hmac.compare_digest` (`HTTP 403` `"END Unauthorised USSD gateway request."` on mismatch or missing header).
   - **Operational requirement:** Because Africa's Talking webhooks do not send custom headers directly unless configured via Basic Auth in the callback URL (`https://user:token@domain/api/ussd/gateway/`) or an upstream gateway proxy, configure either Basic Auth credentials matching `USSD_GATEWAY_TOKEN` or an upstream API gateway/reverse proxy rule injecting `X-Gateway-Token` after validating Africa's Talking source IPs.
3. **CSRF Exemption:** `ussd_gateway_callback` uses `@csrf_exempt` because telco callbacks are stateless server-to-server HTTP POSTs.
4. **Staff Authentication inside USSD (`UssdSessionHandler`):**
   - Facility stock updates and case reporting require an **active, facility-linked `Reporter`**, a **4-digit PBKDF2-hashed Staff PIN**, **session phone binding** (`UssdSessionState`), **180-second session timeout**, **replay rejection**, and **3-strike / 15-minute lockout**.
   - Public emergency search requires no Staff PIN and never modifies inventory.

---

## 5. Error Handling & Logging Security

- Missing required fields or oversized fields (`len(sessionId) > 128`, `len(phoneNumber) > 30`, `len(text) > 500`) return `HTTP 400 Bad Request`.
- Missing/invalid gateway token returns `HTTP 403` plain text (`END Unauthorised USSD gateway request.`).
- USSD request bodies containing Staff PINs (`text`) are **never** written to application logs.
- Expired batch dates (`MM/YY` <= current month boundary) are rejected cleanly during USSD stock updates without mutating inventory.

---

## 6. Outbound SMS Dispatch & Delivery Logging

### Current Implementation State (`apps/notifications/sms_gateway.py`)

- Phone numbers are normalised via `normalise_nigerian_phone()` into `+234[789][01]XXXXXXXX` E.164 format.
- `send_sms(recipient, message, purpose, facility)` currently writes an `SmsLog` record with `status=SmsStatus.SIMULATED` and emits an application log line, without performing an external HTTP POST to `AFRICASTALKING_SMS_URL`.
- **Audit Storage (`apps/notifications/models.py` — `SmsLog`):** Records `recipient`, `message`, `facility` FK, `purpose` (`SEARCH_RESULT`, `LOW_STOCK_ALERT`, `STOCKOUT_ALERT`, `CONFIRMATION_REMINDER`), `status` (`DELIVERED`, `QUEUED`, `FAILED`, `SIMULATED`), `gateway_id` (`max_length=100`), and `timestamp`.
- **Log Access Control (`apps/notifications/views.py`):** `SmsLogListAPIView` is protected by `DemoModeOrAuthenticated`; when `DEMO_MODE=False`, anonymous callers receive `403`, `STATE_OFFICIAL` sees only logs for facilities in their assigned state, and `FACILITY_ADMIN`/`FACILITY_REPORTER` see only logs for their own facility.

### Checklist Before Enabling Live Africa's Talking SMS

1. Provision an Africa's Talking account (Sandbox for UAT, Live Nigeria account with approved Sender ID for pilot/production).
2. Implement the live HTTP client branch inside `apps/notifications/sms_gateway.py` using `requests.post(settings.AFRICASTALKING_SMS_URL, headers={'apiKey': settings.AFRICASTALKING_API_KEY, 'Accept': 'application/json'}, data={'username': settings.AFRICASTALKING_USERNAME, 'to': normalised_phone, 'message': message.strip(), 'from': settings.AFRICASTALKING_SENDER_ID or None}, timeout=settings.AFRICASTALKING_TIMEOUT_SECONDS)` whenever `AFRICASTALKING_USERNAME` and `AFRICASTALKING_API_KEY` are configured, persisting the returned `SMSMessageData.Recipients[0].messageId` into `SmsLog.gateway_id` and mapping errors/exceptions to `SmsStatus.FAILED` without raising unhandled exceptions into USSD transactions.
3. Verify live delivery receipts (or add an AT Delivery Report callback URL if status progression from `QUEUED` -> `DELIVERED`/`FAILED` is needed).

---

## 7. Sandbox Test Configuration Guide (Without Live Credentials)

1. Set `DEMO_MODE=True` **only** on a staging/test instance if you want to exercise the browser simulator (`/demo/`, `/api/ussd/simulate/`). On pilot/production keep `DEMO_MODE=False`.
2. Test the live webhook contract locally via `curl` against `POST /api/ussd/gateway/`:

```bash
# Public antivenom availability search via USSD callback
curl -X POST http://127.0.0.1:8000/api/ussd/gateway/ \
  -H "X-Gateway-Token: <YOUR_USSD_GATEWAY_TOKEN>" \
  -d "sessionId=at-test-001&serviceCode=*384*20#&phoneNumber=+2348091122334&text=1*1*1"
```

Expected response: `END VenomLink Results (SMS Sent): ...` plus a new `AntivenomSearch` row and a `SmsLog` row (`status=SIMULATED`).
