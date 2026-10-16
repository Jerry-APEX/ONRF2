# One Nation Reggae Festival Vol.2 — Online QR Check-in

**PUBLIC TEST DEMO ONLY.** This deployment is for staff and customer acceptance testing. It does **not** include, import, or recognize the 13,000 official production ticket codes.

## Public demo URL

https://onrf2-online-ticket-test.onrender.com

## Included features

- Responsive mobile-first festival-themed website, iPhone / Android HTTPS camera QR scanner, manual code entry and keyboard-style USB scanner input.
- Secured staff and administrator sign-in, configured using Render environment variables. No real passwords are committed to this Git repository.
- Server-side atomic one-time check-in using SQLite transactions. A repeated scan shows the original entry time, gate and operator.
- Eight sample QR cards, one valid card for each of the four designs, a pre-used ticket, a void ticket, an unknown code and a duplicate detachable stub. Tap **Simulate Scan** to test without a second camera.
- Administrator-only demo reset and recent check-in activity.

## How to test

1. Open the demo URL using **Safari** (iPhone) or **Chrome** (Android).
2. Sign in with credentials given privately by the project administrator. The test roles are `admin` and `staff`.
3. Open **TEST QR CODES**, select a sample and tap **SIMULATE SCAN**. Scan #2 twice or scan #2 then #8 to test rejection of duplicate entry.
4. To use a real camera, open **SCANNER** then tap **Start camera**, and scan a test QR code shown on a different device or printed sheet.
5. Admin can use **Reset TEST data** for another test run.

## Hosting configuration

- Runtime: Python
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1`
- Required environment variables: `SESSION_SECRET` (at least 32 characters), `ADMIN_PASSWORD` (at least 12 characters), `STAFF_PASSWORD` (at least 12 characters).
- `DB_PATH=/tmp/onrf2_test.sqlite` is used for this **disposable demo only**. Free Render instance storage is ephemeral. The database can be reset on a cold start, redeploy, or restart; do not treat test logs as durable.

## Important before real festival use

Migrate to a persistent PostgreSQL database with backups; introduce separate accounts for operators and stronger audit controls; perform in-person camera and offline-network tests; restrict staff and admin rights; import the 13,000 official ticket token-to-serial mappings over an authenticated admin migration flow **only after** separate production validation. Never upload production ticket tokens to a public GitHub repository. The demo credentials must be rotated before production.
