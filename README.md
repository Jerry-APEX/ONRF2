# One Nation Reggae Festival Vol.2

English, mobile-first ticket verification portal for **customer testing only**.

Existing Render service: <https://onrf2-online-ticket-test.onrender.com>

## Experience

- **SCAN TICKET:** live rear-camera preview, continuous local QR recognition, manual token fallback, gate selection and prominent entry results. Sound and optional vibration respect browser support.
- **TEST QR CODES:** eight authenticated, printable sample cards and server-side **SIMULATE SCAN** buttons for testing with one phone.
- **ADMIN PANEL:** six unique test tickets, check-in counts, rejected scans, latest 100 activity records, test backup export and confirmed test reset.
- Cream, burgundy, festival red, green and golden yellow styling; geometric patterns and text-based festival branding. No animal illustrations. All assets, fonts and QR decoding code are served by this application.

## Data and security boundaries

This application is locked to **TEST MODE**. Setting `APP_MODE` to anything other than `test` prevents startup. There is no production import or activation endpoint.

The eight original sample QR payloads, serial numbers, types and initial statuses are preserved exactly. Samples 2 and 8 share one token and one admission. Sample 7 is intentionally unknown, so eight cards represent **six database tickets**. No 13,000-ticket production dataset was present in the source checkout; none is imported by this application.

Only `onrf2_test_*` database tables are created, changed or reset. An existing SQLite demo's known test entries can be copied into these tables once, without changing its original tables. Credentials remain in server environment variables. Browser scripts contain no built-in QR tokens or staff passwords; authenticated sample QR images necessarily encode their public test tokens.

The server uses a conditional `UPDATE … WHERE status='unused' RETURNING …` in the same transaction as its audit insert. PostgreSQL row locks or SQLite `BEGIN IMMEDIATE` arbitrate concurrent requests. One request wins; duplicates read the first entry's timestamp, gate and operator. A failed audit insert rolls the check-in back. No client or offline state can grant admission.

Role-based sessions, secure/HttpOnly/SameSite cookies, CSRF checks, same-origin writes, database-backed login throttling, restrictive content security headers and authenticated QR endpoints protect the portal. Existing `admin` and `staff` accounts and environment-variable names are retained. Those shared role accounts identify the role in activity records; individual named operator accounts remain a prerequisite for a live festival.

## Render configuration

Update the **existing** ONRF2 Python web service, rather than creating another web service.

| Setting | Value |
| --- | --- |
| Repository / branch | `Jerry-APEX/ONRF2` / `main` after the tested change is merged |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn app:app` |
| Health check | `/health` |
| App mode | `APP_MODE=test` |
| Session signing secret | `SESSION_SECRET`, at least 32 characters; preserve the current value |
| Administrator password | `ADMIN_PASSWORD`, at least 12 characters; preserve the current value |
| Staff password | `STAFF_PASSWORD`, at least 12 characters; preserve the current value |
| Persistent storage | `DATABASE_URL`, internal Render PostgreSQL connection URL in the same workspace and region |

`gunicorn.conf.py` binds to `0.0.0.0:$PORT` and uses two processes with four threads each. An existing start command can override those settings. No frontend build step is required.

When `DATABASE_URL` is set, PostgreSQL is used exclusively; connection failure never silently falls back to SQLite. Schema creation and test-only seeding run safely at startup and never reset previously used tickets.

Without `DATABASE_URL`, `DB_PATH` defaults to `/tmp/onrf2_test.sqlite`. This fallback is convenient for local work but **is ephemeral on a free Render web service**. It does not satisfy durable hosting. Use a dedicated test PostgreSQL instance for customer testing. Free Render PostgreSQL instances expire and do not provide the production backup policy needed for a festival; check the actual instance's expiry in Render. Do not activate production ticket codes on free, expiring infrastructure.

Do not set `LOCAL_DEVELOPMENT` on Render: production HTTPS cookies must remain secure. Keep real credentials out of Git, screenshots, issue comments and browser code. Use the existing credentials supplied privately by the project administrator.

## Local development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Configure `SESSION_SECRET`, `ADMIN_PASSWORD` and `STAFF_PASSWORD` in the local environment. For localhost HTTP only, set `LOCAL_DEVELOPMENT=1`; then run `.venv/bin/python app.py` and open `http://localhost:10000`.

## Testing on phones

1. Open the deployed **HTTPS** URL in iPhone Safari or Android Chrome; sign in as `admin` or `staff`.
2. Select the correct **CHECK-IN GATE**. On **TEST QR CODES**, tap **SIMULATE SCAN** to try each sample without a second phone.
3. Sample 1–4 should each grant one entry. Sample 5 is already used, sample 6 is cancelled, and sample 7 is invalid. After sample 2 is used, sample 8 must show its original entry time, gate and operator.
4. For camera testing, display a sample on another phone or use **PRINT QR SHEET**. Tap **START CAMERA**, allow camera access and center the QR inside the corner markers. Test printed paper, another screen, small codes and moderate lighting on both phone types.
5. Leave a successfully scanned QR in view: it must not resubmit continuously. Remove it for a few seconds, then present it again: it must show **ALREADY SCANNED**. The same camera session should continue operating.
6. Use two signed-in phones at different gates to scan the same unused test ticket together. Exactly one may show **ACCESS GRANTED**. Both must then display the same original entry metadata.
7. Check **ADMIN PANEL**. Export a backup before discarding records. **RESET TEST DATA** requires confirmation and restores the initial six-ticket state (one pre-used ticket, four unused tickets, one cancelled ticket).

Camera images are decoded locally using native `BarcodeDetector` where available and self-hosted jsQR as the fallback. Rear-camera permission is requested only after a user gesture. Recognition waits for two distinct video frames, blocks overlapping requests, ignores frozen frames and latches a stationary QR. Camera access can still be affected by browser settings, another application using the camera, or the operating system. Failure always leaves manual entry available.

## Backups and live-readiness

Administrators can download a consistent **EXPORT TEST BACKUP** JSON snapshot containing the six hashed test mappings and audit records, without raw QR payloads or passwords. Store that file outside the Render service. For PostgreSQL disaster recovery, also use native database backups/exports and verify a restore on a separate database; a browser export is not an automated backup schedule.

Before the live festival: provision non-expiring persistent PostgreSQL, configure and restore-test managed backups, introduce individual operator accounts, validate permissions and availability, conduct physical phone and network tests, and build a separately approved production migration. This testing application never activates production mappings.

## Automated verification

```bash
.venv/bin/pip install pytest
.venv/bin/pytest -q
node --test tests/scanner_state.test.cjs
```

For an optional disposable local PostgreSQL 16 integration run:

```bash
.venv/bin/pip install pgserver==0.1.4
.venv/bin/python tests/postgres_acceptance.py
```

The optional test creates its own temporary local database; it does not connect to Render or use deployment credentials. See [docs/acceptance.md](docs/acceptance.md) for verified checks and remaining device/deployment acceptance work.
