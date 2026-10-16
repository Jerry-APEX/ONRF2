# One Nation Reggae Festival Vol.2

English ticket verification portal for mobile phones, tablets and desktop gate operations, with isolated **test** and **production** modes.

Existing Render service: <https://onrf2-online-ticket-test.onrender.com>

**Version 3.0.0:** the production-capable release uses the latest 13,000-ticket factory workbook and the festival website's Anton/Inter typography, gold/charcoal identity and non-animal palm artwork. The scanning workspace stays light and readable outdoors. Desktop camera and result panels sit side by side; tablet and phone layouts use a single column.

**Activation:** `/health` is the authoritative deployed mode and storage check. Production cannot start without persistent PostgreSQL and the authenticated, pinned registry. Until the owner connects the formal database and its backups, the public service remains in test mode. The existing free test database expires on 14 November 2026. Existing login credentials are preserved; do not post connection URLs or passwords in GitHub or chat.

## Experience

- **SCAN TICKET:** live rear-camera preview, continuous local QR recognition, manual token fallback, gate selection and prominent entry results. Sound and optional vibration respect browser support.
- **TEST QR CODES:** available only in test mode, with 20 alphanumeric sample cards and the eight original cards. Both collections support **SIMULATE SCAN** for testing with one phone.
- **ADMIN PANEL:** official ticket totals in production, 24 unique sample tickets in test mode, recent scan records and administrator-only backup export. Reset is available only in test mode.
- Reference identity: <https://onenationreggaefestival.com/>. Anton headings, Inter body text, charcoal masthead, festival gold buttons, cream workspace and a compact text wordmark. No animal illustrations. Fonts, imagery and QR decoding are self-hosted.

## Data and security boundaries

`APP_MODE` accepts only `test` or `production`, and defaults to `test`. Production requires `DATABASE_URL`, an authenticated encrypted registry and its independently pinned catalog hash. There is no public registry import or remote backup-restore endpoint. Production hides testing controls and rejects every sample image, simulation and reset route.

The eight original sample QR payloads, serial numbers, types and initial statuses are preserved exactly. Samples 2 and 8 share one token and one admission. Sample 7 is intentionally unknown, so the original eight cards represent **six database tickets**.

The separate 20-card collection is defined in the server-side `test_samples_20.json` fixture. Its tokens contain exactly 32 ASCII letters and digits. There are 19 distinct payloads and 18 issued tickets: 16 initially unused, one initially used and one cancelled. Sample 7 is invalid and is never inserted as a ticket; Sample 8 shares Sample 2's admission. The new `TEST20-` namespace is separate from the original samples and production registry. Test mode never decrypts or imports the production registry; production never seeds test tickets.

The original `/api/samples` and `/api/sample/1` through `/api/sample/8` routes retain their behavior; sample 9 still returns 404. The new collection uses `/api/collections/alphanumeric-20/samples`, `/api/collections/alphanumeric-20/sample/<number>` and its `/scan` POST endpoint. All sample routes require a signed-in role, and simulation requires the session CSRF header. Public page HTML, browser JavaScript, sample-list metadata and backup exports do not contain raw test tokens.

Modes use separate `onrf2_test_*` and `onrf2_production_*` tables and locks. A mode change invalidates the previous session. The test-only legacy migration preserves known demo history without changing original tables. Credentials and the production decryption key stay in server environment variables. Browser code contains no ticket secrets; authenticated test images encode only public sample tokens.

The server uses a conditional `UPDATE … WHERE status='unused' RETURNING …` in the same transaction as its audit insert. PostgreSQL row locks or SQLite `BEGIN IMMEDIATE` arbitrate concurrent requests. One request wins; duplicates read the first entry's timestamp, gate and operator. A failed audit insert rolls the check-in back. No client or offline state can grant admission.

The factory printing format is a **39-character unpadded Base32 alias of the same original 24 bytes**, whose canonical representation is the existing 32-character Base64URL token. `qr_encoding.canonical_token()` restores the original representation before hashing, so new and old printed formats share one admission. The encrypted production registry contains only canonical hashes, serials, ticket types, access areas and print order. All 13,000 mappings were compared to `ONRF2_FACTORY_ALPHANUMERIC_13000.xlsx`; its SHA256 is `f6c5492e2efc31dac1a8b09704d69a4ae5cecfc938b231553d11e64267ace8c1`. Startup authenticates the registry, validates all serial ranges/counts and preserves existing entry history. See [production deployment](docs/production-deployment.md).

Role-based sessions, secure/HttpOnly/SameSite cookies, CSRF checks, same-origin writes, database-backed login throttling, restrictive content security headers and authenticated test QR endpoints protect the portal. Existing `admin` and `staff` accounts and environment-variable names are retained; the account used for check-in is preserved as the original operator.

## Render configuration

Update the **existing** ONRF2 Python web service, rather than creating another web service.

| Setting | Value |
| --- | --- |
| Repository / branch | `Jerry-APEX/ONRF2` / `main` after the tested change is merged |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn app:app` |
| Health check | `/health` |
| App mode | `APP_MODE=test` by default; `production` after database and backup readiness |
| Session signing secret | `SESSION_SECRET`, at least 32 characters; preserve the current value |
| Administrator password | `ADMIN_PASSWORD`, at least 12 characters; preserve the current value |
| Staff password | `STAFF_PASSWORD`, at least 12 characters; preserve the current value |
| Persistent storage | `DATABASE_URL`, internal Render PostgreSQL connection URL in the same workspace and region |
| Encrypted production registry | `PRODUCTION_CATALOG_KEY`, privately configured; no default |
| Production catalog pin | `EXPECTED_PRODUCTION_CATALOG_SHA256`, independently verified registry hash |

`gunicorn.conf.py` binds to `0.0.0.0:$PORT` and uses two processes with four threads each. An existing start command can override those settings. No frontend build step is required.

When `DATABASE_URL` is set, PostgreSQL is used exclusively; connection failure never falls back to SQLite. Schema creation, test seeding and authenticated production imports use the selected mode's transaction and never reset previously used tickets. Production always requires PostgreSQL, including when `LOCAL_DEVELOPMENT` is set.

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

Administrators can download **EXPORT CHECK-IN BACKUP** in production or **EXPORT TEST BACKUP** in test mode. Each is a consistent snapshot of that mode's hashed ticket mappings and audit records, without raw QR values or passwords. The offline [backup/restore CLI](docs/backup-restore.md) previews restoration and refuses to overwrite differing existing history. Store backups outside the service and test restoration into an empty PostgreSQL database; a browser export does not configure managed backups.

Before switching live, connect non-expiring PostgreSQL with managed backups and an always-on web instance. Verify `/health` reports `production` and `postgresql`, and that the administrator summary matches 13,000 tickets and the pinned catalog/source hashes. Complete physical iPhone Safari and Android Chrome checks with dedicated acceptance fixtures. The code and private catalog are prepared; provider credentials and hosting plans must be configured separately.

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
