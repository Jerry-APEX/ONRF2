# Acceptance record

Prepared 16 October 2026 for the ONRF2 customer-testing redesign.

## Version 2.1.0 public release

Render deployment `dep-db8s247f3r2c73f7cfv0` reached `live` on 16 October 2026. It runs application commit `030803cee19e60ff66c50872509961fdb762ecbc`, whose [GitHub Actions checks passed](https://github.com/Jerry-APEX/ONRF2/actions/runs/38860150216). The configured automatic deploy did not pick up the update; deployment was triggered with a cleared build cache.

| Public check | Result |
| --- | --- |
| HTTPS health | HTTP 200, version `2.1.0`, revision `030803cee19e`, TEST MODE, storage `sqlite` |
| New sample collection and QR images | Anonymous requests return HTTP 401; the fixture JSON file is not served |
| Application, scanner and CSS | HTTP 200 and byte-for-byte hashes match the committed release |
| Browser loading | Secure context, English login, new 20-sample and original 8-sample options, no JavaScript errors |
| Mobile and desktop login layout | No overflow at 390 and 1440 pixels |
| Security headers | Same-origin camera policy, CSP, HTTPS strict transport and no-store caching present |

Existing live passwords were not retrieved or rotated. Authenticated verification was exercised locally as recorded below; public authenticated entry, actual mobile cameras and persistent database wiring remain outstanding. No production tickets were imported or activated. The service still reports disposable SQLite.

## Version 2.1.0 update verified locally

The update adds 20 alphanumeric test sample cards as the default collection and retains the eight original cards and their existing routes. Twenty new cards represent 18 issued tickets and 19 unique payloads: Sample 7 is intentionally invalid, and Sample 8 shares Sample 2's admission. Together, both collections have 24 issued tickets and 28 cards. Startup adds mappings without replacing existing check-in states or scan history. No production mappings are imported or activated.

| Check | Result |
| --- | --- |
| Server regression suite | 22 tests passed, including the original sample fingerprint, TEST MODE startup guard, new collection permissions, sequential outcomes, legacy history, reset and restart |
| SQLite mixed-format simultaneous entry | 12 original/printed-format requests: exactly 1 grant; every duplicate returned identical first-entry metadata |
| PostgreSQL 16 mixed-format simultaneous entry | 16 original/printed-format requests: exactly 1 grant; every duplicate returned identical first-entry metadata |
| PostgreSQL restart, export, summary, reset and both collections | All 28 sequential sample outcomes passed on an isolated local database |
| Actual application QR image decoding | Chromium jsQR decoded all 20 new and 8 original authenticated PNG responses to their exact fixture payloads |
| Scanner regression suite | 3 tests passed; recognition logic unchanged |
| Browser interaction | Both collections, all 28 simulations, dynamic counts, confirmed reset, sign out and no JavaScript errors |
| Delayed collection responses | Rapid collection switching retains the selected collection; a stale response cannot replace its cards |
| Responsive layout | No horizontal page overflow at 320, 375, 390, 430, 768 and 1440 px |

Sample 7 in the new collection uses QR mask 0, selected through independent reader checks without changing its payload. Other new samples use the QR generator's normal mask selection. Authentication remains required for the image endpoints.

The new 39-character printing format is unpadded Base32 for the same 24 bytes as an existing 32-character Base64URL token. Verification calls `canonical_token()` before hashing, so both representations share the same database key and admission. A future approved production import must normalize first and hash the original token; it must never register two tickets or directly hash the 39-character representation. Unknown tokens are rejected in both formats. The compatibility layer grants no production access.

These are local software checks. Public transport and access checks are recorded separately above. Persistent storage, authenticated public entry and physical mobile-camera acceptance must be verified independently; historical public checks below describe the prior release.

## Verified version 2.0.0 baseline

| Check | Result |
| --- | --- |
| Original eight QR payload/serial/type/status tuples | All unchanged; regression fingerprint `77dc0468b3f2c6c33af8407a1870edab3783aab3e3baee7501baf67a5beb14bb` |
| Server regression suite | 13 tests passed, including the TEST MODE guard and legacy test-history preservation |
| SQLite simultaneous entry | 12 requests: exactly 1 grant, 11 duplicates with identical first-entry metadata |
| PostgreSQL 16 simultaneous entry | 16 requests: exactly 1 grant, 15 duplicates with identical first-entry metadata |
| PostgreSQL restart, export, summary, reset, 8 samples | Passed on isolated local PostgreSQL |
| Existing role restrictions, CSRF, same-origin writes, login throttle | Passed |
| Failed audit insert | Entry rolled back; subsequent retry could obtain the first entry |
| Reset isolation | Namespaced test tables reset; a separate production fixture remained unchanged |
| QR generation and browser decoding | All 8 image payloads decoded back to their original values |
| Chromium synthetic camera | 160 px QR within a 960 × 720 video frame recognized; first entry granted |
| Continuous video suppression | Stationary QR submitted once; removing and reintroducing it produced a duplicate; video stream ID remained unchanged |
| Scanner state regression suite | 3 tests passed, including slow verification, frozen frames and manual-request locking |
| Responsive page overflow | No horizontal page overflow at 320, 375, 390, 430, 768 and 1440 px; activity table scrolls within its region |
| Browser JavaScript errors | None observed during tested navigation, samples, simulated verification and synthetic camera flow |
| Public release deployment | Render deployment `dep-db8qci4s728c73amn77g` reached `live`; application commit `5ca6fe4e1e166b6b9ff00d5cf1419a084a68db03` |
| Public HTTPS health | HTTP 200; version `2.0.0`, mode `test`, revision `5ca6fe4e1e16`, storage `sqlite` |
| Public browser checks | HTTPS secure context, English login, TEST MODE indicator, mobile 390 px and desktop 1440 px, no page overflow or JavaScript errors |
| Public static assets | Application, scanner, jsQR, CSS and font all returned HTTP 200 |
| Public access controls | Anonymous session, sample QR, sample listing, summary and backup requests return HTTP 401 |
| Public security headers | CSP, HTTPS strict transport and same-origin camera policy present |
| Render runtime error logs | No error-level entries observed after the release deployment |

## Deployed release

URL: <https://onrf2-online-ticket-test.onrender.com>

The redesign was merged into `Jerry-APEX/ONRF2` main and deployed to the existing `onrf2-online-ticket-test` service in JERRY's workspace, Frankfurt. Existing login credentials were preserved; `APP_MODE=test` was set explicitly. No production import was performed.

The existing private PostgreSQL 18 instance `one-nation-checkin-test-db` is available in the same workspace and region, with an expiry of **14 November 2026**. Its external access remains disabled. The application currently reports SQLite: the owner must place the database's **Internal Database URL** in the web service's `DATABASE_URL` environment variable and deploy before durable storage can be confirmed. The available Render tools do not return that connection credential. No database network access was broadened.

## Still required to accept the deployed release

- Configure `DATABASE_URL` from the existing private test PostgreSQL instance, deploy, and verify that the public health check reports `postgresql`. SQLite records can be lost when Render restarts or replaces the service instance.
- Exercise authenticated sample, role, backup, duplicate and simultaneous-scan flows through that public URL using privately configured test credentials.
- Physically scan a printed test QR and a QR shown on another phone using **an actual iPhone running Safari** and **an actual Android phone running Chrome**. Check permission denial/recovery, small QR codes, moderate light, glare and repeated scans.
- Confirm audible success/rejection cues on supported devices and explicitly enabled vibration on devices that support it.
- Compare with the original ticket artwork on a device. The repository supplied text branding, original sample types and palette guidance, but no original ticket artwork files.

Browser emulation and synthetic video validate software behavior; they do not establish physical mobile-camera performance. The redesign is publicly deployed, but persistent database wiring, authenticated public end-to-end checks and physical mobile-camera acceptance remain outstanding. Existing deployment credentials were not retrieved or rotated for automated testing.
