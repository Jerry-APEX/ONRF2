# Production deployment and registry consistency

The same source tree supports an isolated `test` deployment and a live
`production` deployment. `APP_MODE` defaults to `test`. Modes use different
`onrf2_test_*` and `onrf2_production_*` tables, login throttling buckets, and
PostgreSQL advisory locks. Switching modes invalidates existing browser sessions.
The production application never seeds sample tickets or exposes sample images,
simulated scans, or the test reset route.

## Private production registry

`data/production_registry.enc` contains Fernet-authenticated ciphertext, not raw
QR values. Its decrypted, gzip-compressed JSON contains only the factory serial,
canonical-token SHA256, ticket kind, access area and print order. The server must
receive the independent `PRODUCTION_CATALOG_KEY` and
`EXPECTED_PRODUCTION_CATALOG_SHA256` in its private environment. Never commit the
key, an unencrypted registry, production spreadsheets, or QR payloads. Preserve a
private copy of the key separately from the repository and service filesystem.

The complete registry is 13,000 tickets: 1,000 WHITE, 11,000 MAIN, 500
VIP_CREATIVITY and 500 VIP_CULTURE. Its serial ranges and order match the factory
Excel. Startup rejects malformed data, duplicate hashes or serials, wrong counts,
invalid encryption, unexpected fields and a mismatched pinned digest.

The current printing format is an unpadded 39-character Base32 encoding of the
original 24-byte token. The verifier restores the canonical original 32-character
Base64URL representation before hashing. The original QR and new printed QR
therefore identify the same ticket and share one atomic first entry. Stripping
characters, hashing the 39-character alias directly or inserting aliases as extra
tickets would break this invariant.

Startup acquires a mode-specific exclusive advisory lock and imports mappings
within one transaction. Existing identical mappings remain untouched, including
status, first entry time, gate, operator and scan history. A conflicting mapping
or source manifest aborts startup and rolls the transaction back. Repeated
process startup is idempotent. First installation requires empty ticket, audit
and catalog tables. Missing ticket rows, missing metadata or partial previous
imports are rejected instead of being repaired as unused tickets; restore a
verified backup into a new empty database. Production health and scanning fail closed when
the installed catalog count or manifest is incomplete.

## Configure the existing Render service

Keep the existing `SESSION_SECRET`, `ADMIN_PASSWORD` and `STAFF_PASSWORD` values.
Configure these additional environment variables privately in the existing web
service, without putting credentials in GitHub or chat:

| Variable | Production setting |
| --- | --- |
| `DATABASE_URL` | Internal PostgreSQL URL in the same workspace and region |
| `PRODUCTION_CATALOG_KEY` | Independent private Fernet key for the registry |
| `EXPECTED_PRODUCTION_CATALOG_SHA256` | Release catalog digest independently verified against the factory Excel |
| `APP_MODE` | Set to `production` only after the database, backups and release checks are ready |
| `PRODUCTION_CATALOG_PATH` | Optional server-side path; defaults to `data/production_registry.enc` |

Production always requires PostgreSQL. `LOCAL_DEVELOPMENT=1`, `DB_PATH`, and an
empty `DATABASE_URL` cannot enable a production SQLite fallback. A configured
PostgreSQL connection failure also never falls back to SQLite. Do not enable
`LOCAL_DEVELOPMENT` on the public Render service. Production cookies always remain
HTTPS-only, including when that local flag is accidentally set.

Use `pip install -r requirements.txt` to build, `gunicorn app:app` to start, and
`/health` for the health check. The production response must report
`mode=production`, `storage=postgresql`, and `status=ok`. The authenticated admin
summary must show all 13,000 tickets with the exact four type counts and pinned
source/catalog hashes. It deliberately omits the full ticket list and only
returns the latest 100 audit entries.

Use non-expiring PostgreSQL with managed backups and a web instance that stays
awake for real gate operation. A free expiring database and disposable web
filesystem do not meet durable festival operation requirements. The existing
free test PostgreSQL expires on 14 November 2026; preparing production-capable
code does not upgrade that infrastructure or connect its credentials.

## Backup, restore and acceptance

See [backup-restore.md](backup-restore.md) for the local operator CLI. Administrators
can also download a consistent mode-labelled backup through the signed-in admin
panel. Both formats contain hashes and audit history, never raw QR tokens or
passwords. Keep backups outside the service and verify restoration into a new
empty PostgreSQL database. A browser download is not an automated backup policy.
The restore tool previews by default, requires the exact mode-specific restore
phrase, and refuses to replace differing existing history. No HTTP restore route
is provided.

Before allowing attendees to enter, verify both phone-camera environments over
public HTTPS, a printed QR and another phone's display, named gate/operator
procedures, concurrent scans, first-entry metadata on duplicates and recovery on
an isolated restored database. Physical mobile camera verification cannot be
replaced by local automated tests. Test codes must be rejected by production;
production codes must be rejected by the test deployment. Never scan a real
attendee's ticket as an acceptance fixture.
