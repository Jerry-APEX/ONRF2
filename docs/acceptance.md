# Acceptance record

Prepared 16 October 2026 for the ONRF2 customer-testing redesign.

## Verified in the development environment

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
| Public baseline Render service | Existing `/health` returned 200 before deployment; this does not verify the redesigned release |

## Still required to accept the deployed release

- Confirm the target Render workspace, inspect the existing service and connect dedicated persistent test PostgreSQL.
- Deploy the tested commit to the existing Render service and verify its public HTTPS `/health` revision and database engine.
- Exercise authenticated sample, role, backup, duplicate and simultaneous-scan flows through that public URL using privately configured test credentials.
- Physically scan a printed test QR and a QR shown on another phone using **an actual iPhone running Safari** and **an actual Android phone running Chrome**. Check permission denial/recovery, small QR codes, moderate light, glare and repeated scans.
- Confirm audible success/rejection cues on supported devices and explicitly enabled vibration on devices that support it.
- Compare with the original ticket artwork on a device. The repository supplied text branding, original sample types and palette guidance, but no original ticket artwork files.

Browser emulation and synthetic video validate software behavior; they do not establish physical mobile-camera performance. The redesign is not yet recorded as publicly deployed or fully accepted in this document.
