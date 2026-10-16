# Backup and restore

`ops_backup.py` is a local operator command. It reads connection settings from the environment, does not import or start the website, and never exports raw QR payloads, staff passwords, session secrets, or database credentials. Production and test data have separate tables and cannot be restored across modes.

Backups contain ticket SHA256 hashes, ticket types and status, the original successful entry time/gate/operator, every scan with its original ID, and the production catalog manifest. Treat these files as private operational records. Copy them to protected storage outside the web service after creating them; a backup on Render's temporary filesystem is not a persistent recovery copy.

## Create a recovery point

Use the Python environment with the repository dependencies installed. Set `DATABASE_URL` using your local secret manager or shell environment, without putting its value into command arguments, Git, or chat. Use the database connection available to the machine running the command. An internal Render address is accessible only from Render's private network.

```bash
python ops_backup.py backup --mode production --output /secure/backups/onrf2-before-event.json
```

The output file must not already exist. The command creates it with mode `0600`. PostgreSQL uses one repeatable-read, read-only transaction; SQLite uses one read transaction. A validation failure produces no backup file. The command prints only mode and record counts.

For a local test database:

```bash
DB_PATH=/absolute/path/to/test.sqlite python ops_backup.py backup --mode test --output /secure/backups/onrf2-test.json
```

If `DATABASE_URL` is set it takes precedence over `DB_PATH`. Select `--mode` explicitly during operations.

## Preview recovery first

Restore into a **new, empty database**, keeping the original database available for investigation. Point `DATABASE_URL` at that target and validate the selected file:

```bash
python ops_backup.py restore --mode production --input /secure/backups/onrf2-before-event.json
```

The default is a read-only preview. It validates the complete file and checks the target, but does not create tables, restore records, reset tickets, or alter check-in history. `"preview": true` in the result confirms preview operation. The target can have no selected-mode tables, or complete empty selected-mode tables. A partially created schema must be repaired first.

The document must have the expected schema version and selected mode, unique serial numbers, unique code hashes, valid status and original-entry metadata, unique positive scan IDs, valid scan timestamps and results, and valid ticket references. Production additionally verifies its catalog manifest against every static ticket mapping. Extra raw-token or credential fields are rejected.

## Restore the approved recovery point

The exact mode-specific phrase is required:

```bash
python ops_backup.py restore --mode production --input /secure/backups/onrf2-before-event.json --confirm 'RESTORE PRODUCTION BACKUP'
```

Test restore uses `--mode test --confirm 'RESTORE TEST BACKUP'`.

Restore creates missing selected-mode tables only after confirmation and writes all tickets, scans, and manifest records in one transaction. It preserves original entry metadata and scan IDs and synchronizes the PostgreSQL scan sequence for subsequent check-ins. If any insert or constraint fails, the transaction rolls back.

A target already containing the identical records is left unchanged. Any different non-empty target is refused, even with confirmation. There is no destructive replace option. This prevents an older backup from silently reopening used tickets or deleting scan history. Do not restore over an active gate database. Recovery of a stale snapshot can omit admissions that happened after that snapshot; reconcile those records before switching gate operations.

After restoring, run another backup from the target and compare the `tickets`, `scans`, and `catalog` collections with the selected recovery point. The new `created_at` will differ. Confirm readiness and first-entry preservation before directing staff to the recovered service. Retain both snapshots and the previous database. Managed PostgreSQL backups/PITR and a restore drill should supplement this portable backup.

## Development limits

Production deployment requires persistent PostgreSQL. The command allows a small synthetic production fixture in SQLite only when `LOCAL_DEVELOPMENT=1` and an explicit, non-default `DB_PATH` is set. It refuses production SQLite backups or restores containing 13,000 or more tickets, including during local development. This exception is for automated recovery tests, not the event database. Test mode may use SQLite normally.

The public Python interfaces are `backup_snapshot(connection, mode)`, `validate_snapshot(document, mode)`, and `restore_snapshot(connection, document, mode, confirm=None)`. They accept a dedicated idle `sqlite3.Connection` or psycopg connection (or an adapter exposing `.raw`) and manage their own transactions. Do not pass a connection with an in-progress transaction. `catalog_hash(tickets)` computes the static production mapping hash. Neither CLI operation starts the Flask app or imports ticket factory spreadsheets.
