# One Nation Reggae Festival Vol.2

English, mobile-first ticket verification portal for **customer testing only**.

Existing Render service: <https://onrf2-online-ticket-test.onrender.com>

**Release status (16 October 2026):** version 2.0.0 is deployed over public HTTPS in TEST MODE. The deployed health check currently reports SQLite, so scan history is still disposable until `DATABASE_URL` is configured. The existing private Render test PostgreSQL instance is available in Frankfurt and expires on 14 November 2026. Add its Internal Database URL in the web service's Environment settings; do not post that credential in GitHub or chat. Existing administrator and staff passwords have not been changed.

The version 2.1.0 update adds a default collection of 20 alphanumeric test samples while retaining the original eight samples, their routes, mappings and check-in history. Deployment of this update must be verified separately from the historical release above.

## Experience

- **SCAN TICKET:** live rear-camera preview, continuous local QR recognition, manual token fallback, gate selection and prominent entry results. Sound and optional vibration respect browser support.
- **TEST QR CODES:** 20 authenticated, printable alphanumeric sample cards by default, with a collection selector for the eight original cards. Both collections support server-side **SIMULATE SCAN** for testing with one phone.
- **ADMIN PANEL:** 24 unique test tickets, check-in counts, rejected scans, latest 100 activity records, test backup export and confirmed test reset.
- Cream, burgundy, festival red, green and golden yellow styling; geometric patterns and text-based festival branding. No animal illustrations. All assets, fonts and QR decoding code are served by this application.

## Data and security boundaries

This application is locked to **TEST MODE**. Setting `APP_MODE` to anything other than `test` prevents startup. There is no production import or activation endpoint.

The eight original sample QR payloads, serial numbers, types and initial statuses are preserved exactly. Samples 2 and 8 share one token and one admission. Sample 7 is intentionally unknown, so the original eight cards represent **six database tickets**.

The separate 20-card collection is defined in the server-side `test_samples_20.json` fixture. Its tokens contain exactly 32 ASCII letters and digits. There are 19 distinct payloads and 18 issued tickets: 16 initially unused, one initially used and one cancelled. Sample 7 is invalid and is never inserted as a ticket; Sample 8 shares Sample 2's admission. The new `TEST20-` serial namespace is separate from the original samples and production registry. No 13,000-ticket production mappings are loaded or activated by this application.

The original `/api/samples` and `/api/sample/1` through `/api/sample/8` routes retain their behavior; sample 9 still returns 404. The new collection uses `/api/collections/alphanumeric-20/samples`, `/api/collections/alphanumeric-20/sample/<number>` and its `/scan` POST endpoint. All sample routes require a signed-in role, and simulation requires the session CSRF header. Public page HTML, browser JavaScript, sample-list metadata and backup exports do not contain raw test tokens.

Only `onrf2_test_*` database tables are created, changed or reset. An existing SQLite demo's known test entries can be copied into these tables once, without changing its original tables. Credentials remain in server environment variables. Browser scripts contain no built-in QR tokens or staff passwords; authenticated sample QR images necessarily encode their public test tokens.

The server uses a conditional `UPDATE … WHERE status='unused' RETURNING …` in the same transaction as its audit insert. PostgreSQL row locks or SQLite `BEGIN IMMEDIATE` arbitrate concurrent requests. One request wins; duplicates read the first entry's timestamp, gate and operator. A failed audit insert rolls the check-in back. No client or offline state can grant admission.

The new letters-and-numbers factory printing format is a **39-character unpadded Base32 alias of the same original 24 bytes**, whose canonical representation is the existing 32-character Base64URL token. `qr_encoding.canonical_token()` recognizes only valid canonical aliases and restores that original token before verification hashes it. The original token, database hash and admission state are unchanged; both formats therefore share one atomic check-in. A future, separately approved production importer must apply `canonical_token()` before hashing. Never register the two formats as separate tickets or hash the 39-character representation directly. This compatibility does not activate any production ticket; an unregistered token in either format is rejected.

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
2. Select the correct **CHECK-IN GATE**. On **TEST QR CODES**, the default collection has 20 alphanumeric samples; use **TEST SAMPLE COLLECTION** to switch to the eight original samples. Tap **SIMULATE SCAN** to try either collection without a second phone.
3. In either collection, samples 1–4 should each grant one entry. Sample 5 is already used, sample 6 is cancelled, and sample 7 is invalid. After sample 2 is used, sample 8 must show its original entry time, gate and operator. New collection samples 9–20 are additional independent valid tickets.
4. For camera testing, display a sample on another phone or use **PRINT QR SHEET**. Tap **START CAMERA**, allow camera access and center the QR inside the corner markers. Test printed paper, another screen, small codes and moderate lighting on both phone types.
5. Leave a successfully scanned QR in view: it must not resubmit continuously. Remove it for a few seconds, then present it again: it must show **ALREADY SCANNED**. The same camera session should continue operating.
6. Use two signed-in phones at different gates to scan the same unused test ticket together. Exactly one may show **ACCESS GRANTED**. Both must then display the same original entry metadata.
7. Check **ADMIN PANEL**. Export a backup before discarding records. **RESET TEST DATA** requires confirmation and restores both collections to the initial 24-ticket state: two pre-used tickets, 20 unused tickets and two cancelled tickets. Adding the collection or restarting the app does not reset existing entries or scan history.

Camera images are decoded locally using native `BarcodeDetector` where available and self-hosted jsQR as the fallback. Rear-camera permission is requested only after a user gesture. Recognition waits for two distinct video frames, blocks overlapping requests, ignores frozen frames and latches a stationary QR. Camera access can still be affected by browser settings, another application using the camera, or the operating system. Failure always leaves manual entry available.

## Backups and live-readiness

Administrators can download a consistent **EXPORT TEST BACKUP** JSON snapshot containing the 24 hashed test mappings and audit records, without raw QR payloads or passwords. Store that file outside the Render service. For PostgreSQL disaster recovery, also use native database backups/exports and verify a restore on a separate database; a browser export is not an automated backup schedule.

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
