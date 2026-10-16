"""Offline ONRF2 backup/restore. Never imports the web application or exports QR tokens."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
TICKET_FIELDS = ("serial", "code_hash", "kind", "status", "first_time", "first_gate", "first_user")
PRODUCTION_FIELDS = TICKET_FIELDS + ("access_area", "print_order")
SCAN_FIELDS = ("id", "time", "serial", "outcome", "gate", "operator")
CATALOG_FIELDS = {"schema_version", "catalog_sha256", "source_sha256", "source_filename"}
OUTCOMES = {"ACCESS GRANTED", "ALREADY SCANNED", "ACCESS DENIED"}
PRODUCTION_KINDS = {"WHITE": "W", "MAIN": "M", "VIP_CREATIVITY": "VC", "VIP_CULTURE": "VL"}
TEST_KINDS = {"WHITE", "MAIN", "VIP CREATIVITY", "VIP CULTURE"}
DEFAULT_PATHS = {"/tmp/onrf2_test.sqlite", "/tmp/onrf2_production.sqlite"}


class BackupError(ValueError):
    """A safe, credential-free explanation suitable for CLI output."""


def _mode(mode):
    if mode not in ("test", "production"):
        raise BackupError("Mode must be test or production.")
    return mode


def _raw(connection):
    return getattr(connection, "raw", connection)


def _sqlite(connection):
    return isinstance(_raw(connection), sqlite3.Connection)


def _execute(connection, sql, values=()):
    raw = _raw(connection)
    return raw.execute(sql if _sqlite(raw) else sql.replace("?", "%s"), values)


def _rows(connection, sql):
    cursor = _execute(connection, sql)
    names = [column[0] for column in cursor.description]
    return [dict(row) if hasattr(row, "keys") else dict(zip(names, row)) for row in cursor.fetchall()]


def _tables(mode):
    prefix = "onrf2_" + _mode(mode)
    return prefix + "_tickets", prefix + "_scans", prefix + "_catalog"


def _storage_guard(connection, mode, count=0):
    if mode == "production" and _sqlite(connection):
        explicit_path = os.environ.get("DB_PATH", "")
        if (os.environ.get("LOCAL_DEVELOPMENT") != "1" or not explicit_path
                or str(Path(explicit_path).absolute()) in DEFAULT_PATHS):
            raise BackupError("Production SQLite requires LOCAL_DEVELOPMENT=1 and an explicit non-default DB_PATH.")
        if count >= 13000:
            raise BackupError("The 13,000-ticket production registry requires PostgreSQL; SQLite is for small synthetic development fixtures only.")


def _begin(connection, write=False):
    raw = _raw(connection)
    if _sqlite(raw):
        if raw.in_transaction:
            raise BackupError("Use an idle connection; an existing transaction will not be committed.")
        raw.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    else:
        # psycopg's IDLE is zero; avoid requiring psycopg for SQLite-only operation.
        if int(raw.info.transaction_status) != 0:
            raise BackupError("Use an idle connection; an existing transaction will not be committed.")
        raw.execute("BEGIN" if write else "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")


def _has_tables(connection, mode):
    names = _tables(mode)[:3 if mode == "production" else 2]
    if _sqlite(connection):
        found = {row["name"] for row in _rows(connection, "SELECT name FROM sqlite_master WHERE type='table'")}
        present = [name in found for name in names]
    else:
        present = [_execute(connection, "SELECT to_regclass(?)", (name,)).fetchone() for name in names]
        present = [next(iter(row.values())) is not None if isinstance(row, dict) else row[0] is not None for row in present]
    if any(present) and not all(present):
        raise BackupError("The target schema is incomplete; initialize or repair it before restoring.")
    return all(present)


def _contents(connection, mode, allow_missing=False):
    if not _has_tables(connection, mode):
        if not allow_missing:
            raise BackupError("No initialized ticket schema was found.")
        return {"tickets": [], "scans": [], **({"catalog": {}} if mode == "production" else {})}
    tickets, scans, catalog = _tables(mode)
    fields = PRODUCTION_FIELDS if mode == "production" else TICKET_FIELDS
    document = {
        "tickets": _rows(connection, f"SELECT {','.join(fields)} FROM {tickets} ORDER BY serial"),
        "scans": _rows(connection, f"SELECT {','.join(SCAN_FIELDS)} FROM {scans} ORDER BY id"),
    }
    if mode == "production":
        document["catalog"] = {r["key"]: r["value"] for r in _rows(connection, f"SELECT key,value FROM {catalog} ORDER BY key")}
    return document


def _text(value, label, maximum=256):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise BackupError("Invalid " + label + ".")


def _timestamp(value, label):
    _text(value, label, 64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BackupError("Invalid " + label + ".") from None
    if "T" not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BackupError("The " + label + " must be an ISO datetime with a timezone.")
    return parsed


def catalog_hash(tickets):
    static = [{key: row[key] for key in ("serial", "code_hash", "kind", "access_area", "print_order")}
              for row in sorted(tickets, key=lambda row: row["print_order"])]
    encoded = json.dumps(static, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_snapshot(document, mode):
    """Validate the entire document before opening a write transaction; return normalized rows."""
    _mode(mode)
    if isinstance(document, dict) and document.get("mode") != mode:
        raise BackupError("Backup mode does not match the selected target.")
    fields = {"schema_version", "mode", "created_at", "tickets", "scans"}
    if mode == "production":
        fields.add("catalog")
    if not isinstance(document, dict) or set(document) != fields:
        raise BackupError("Unexpected backup fields; raw QR tokens and credentials are not accepted.")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION or document["mode"] != mode:
        raise BackupError("Backup version or mode does not match the selected target.")
    _timestamp(document["created_at"], "backup creation time")
    if not isinstance(document["tickets"], list) or not isinstance(document["scans"], list):
        raise BackupError("Ticket and scan collections must be lists.")
    ticket_fields = set(PRODUCTION_FIELDS if mode == "production" else TICKET_FIELDS)
    tickets, hashes, orders = {}, set(), set()
    for row in document["tickets"]:
        if not isinstance(row, dict) or set(row) != ticket_fields:
            raise BackupError("Unexpected ticket fields.")
        _text(row["serial"], "ticket serial", 128)
        _text(row["kind"], "ticket kind", 64)
        if not isinstance(row["code_hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["code_hash"]):
            raise BackupError("Ticket code_hash must be a lowercase SHA256 digest.")
        if row["serial"] in tickets or row["code_hash"] in hashes:
            raise BackupError("Duplicate ticket serial or code hash.")
        if row["status"] not in ("unused", "used", "cancelled"):
            raise BackupError("Invalid ticket status.")
        first = [row[key] for key in ("first_time", "first_gate", "first_user")]
        if row["status"] == "used":
            _timestamp(row["first_time"], "first entry time")
            _text(row["first_gate"], "first entry gate")
            _text(row["first_user"], "first entry operator")
        elif any(value is not None for value in first):
            raise BackupError("Unused and cancelled tickets cannot contain first-entry metadata.")
        if mode == "production":
            if row["kind"] not in PRODUCTION_KINDS:
                raise BackupError("Invalid production ticket kind.")
            code = PRODUCTION_KINDS[row["kind"]]
            digits = {"W": 4, "M": 5, "VC": 3, "VL": 3}[code]
            if not re.fullmatch(r"ONRF2-" + code + r"-[0-9]{" + str(digits) + "}", row["serial"]):
                raise BackupError("Production serial does not match its ticket kind.")
            _text(row["access_area"], "access area", 128)
            if (type(row["print_order"]) is not int or not 1 <= row["print_order"] <= 2147483647
                    or row["print_order"] in orders):
                raise BackupError("Invalid or duplicate production print order.")
            orders.add(row["print_order"])
        elif row["kind"] not in TEST_KINDS or not re.fullmatch(r"TEST(?:20)?-(?:W|M|VC|VL)-[0-9]+", row["serial"]):
            raise BackupError("Invalid test ticket kind or serial.")
        tickets[row["serial"]] = dict(row)
        hashes.add(row["code_hash"])
    scan_ids, grants, scans = set(), set(), []
    for row in document["scans"]:
        if not isinstance(row, dict) or set(row) != set(SCAN_FIELDS):
            raise BackupError("Unexpected scan fields.")
        if type(row["id"]) is not int or row["id"] < 1 or row["id"] > 9223372036854775807 or row["id"] in scan_ids:
            raise BackupError("Invalid or duplicate scan id.")
        scanned = _timestamp(row["time"], "scan time")
        _text(row["gate"], "scan gate")
        _text(row["operator"], "scan operator")
        if not isinstance(row["outcome"], str) or row["outcome"] not in OUTCOMES:
            raise BackupError("Invalid scan outcome.")
        serial = row["serial"]
        if serial is not None and (not isinstance(serial, str) or serial not in tickets):
            raise BackupError("Scan references an unknown ticket.")
        if row["outcome"] in ("ACCESS GRANTED", "ALREADY SCANNED"):
            ticket = tickets.get(serial)
            if ticket is None or ticket["status"] != "used" or scanned < _timestamp(ticket["first_time"], "first entry time"):
                raise BackupError("Successful and duplicate scans must reference a used ticket and its original entry.")
            if row["outcome"] == "ACCESS GRANTED":
                if (serial in grants or row["time"] != ticket["first_time"]
                        or row["gate"] != ticket["first_gate"] or row["operator"] != ticket["first_user"]):
                    raise BackupError("A granted scan conflicts with the preserved first-entry metadata.")
                grants.add(serial)
        scans.append(dict(row))
        scan_ids.add(row["id"])
    normalized = {"tickets": sorted(tickets.values(), key=lambda row: row["serial"]),
                  "scans": sorted(scans, key=lambda row: row["id"])}
    if mode == "production":
        catalog = document["catalog"]
        if not isinstance(catalog, dict) or set(catalog) != CATALOG_FIELDS:
            raise BackupError("Invalid production catalog manifest fields.")
        if catalog["schema_version"] != "1":
            raise BackupError("Unsupported production catalog version.")
        for key in ("catalog_sha256", "source_sha256"):
            if not isinstance(catalog[key], str) or not re.fullmatch(r"[a-f0-9]{64}", catalog[key]):
                raise BackupError("Invalid production catalog hash.")
        if (not isinstance(catalog["source_filename"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.xlsx", catalog["source_filename"])
                or ".." in catalog["source_filename"]):
            raise BackupError("Catalog source_filename must be an ASCII .xlsx basename.")
        if catalog["catalog_sha256"] != catalog_hash(normalized["tickets"]):
            raise BackupError("Production ticket mappings do not match the catalog manifest.")
        normalized["catalog"] = dict(catalog)
    return normalized


def backup_snapshot(connection, mode):
    """Read a consistent, validated snapshot using a dedicated idle connection."""
    _mode(mode)
    _storage_guard(connection, mode)
    _begin(connection)
    try:
        document = {"schema_version": SCHEMA_VERSION, "mode": mode,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    **_contents(connection, mode)}
        _storage_guard(connection, mode, len(document["tickets"]))
        validate_snapshot(document, mode)
        _raw(connection).commit()
        return document
    except BaseException:
        _raw(connection).rollback()
        raise


def _initialize(connection, mode):
    tickets, scans, catalog = _tables(mode)
    extra = ", access_area TEXT NOT NULL, print_order INTEGER NOT NULL UNIQUE" if mode == "production" else ""
    identity = "INTEGER PRIMARY KEY AUTOINCREMENT" if _sqlite(connection) else "BIGSERIAL PRIMARY KEY"
    _execute(connection, f"CREATE TABLE IF NOT EXISTS {tickets} (serial TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, "
             "kind TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('unused','used','cancelled')), "
             f"first_time TEXT, first_gate TEXT, first_user TEXT{extra})")
    _execute(connection, f"CREATE TABLE IF NOT EXISTS {scans} (id {identity}, time TEXT NOT NULL, serial TEXT, "
             "outcome TEXT NOT NULL, gate TEXT NOT NULL, operator TEXT NOT NULL)")
    _execute(connection, f"CREATE INDEX IF NOT EXISTS {scans}_time ON {scans}(id DESC)")
    if mode == "production":
        _execute(connection, f"CREATE TABLE IF NOT EXISTS {catalog} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def restore_snapshot(connection, document, mode, confirm=None):
    """Preview by default; only the exact mode-specific phrase permits an empty-target restore.

    An identical target is a no-op. A different non-empty target is always refused.
    This function owns one transaction and never deletes existing admission records.
    """
    desired = validate_snapshot(document, mode)
    _storage_guard(connection, mode, len(desired["tickets"]))
    required = "RESTORE " + mode.upper() + " BACKUP"
    if confirm is not None and confirm != required:
        raise BackupError("Confirmation must be exactly: " + required)
    write = confirm == required
    _begin(connection, write=write)
    try:
        if write and not _sqlite(connection):
            _execute(connection, "SELECT pg_advisory_xact_lock(7420911)")
            _initialize(connection, mode)
            names = _tables(mode)[:3 if mode == "production" else 2]
            _execute(connection, f"LOCK TABLE {','.join(names)} IN EXCLUSIVE MODE")
        current = _contents(connection, mode, allow_missing=True)
        same = current == desired
        occupied = any(current.values())
        if occupied and not same:
            raise BackupError("Target contains different records. Restore into a new empty database; no existing history was changed.")
        result = {"mode": mode, "preview": not write, "tickets": len(desired["tickets"]),
                  "scans": len(desired["scans"]), "unchanged": same, "restored": False}
        if write and not same:
            _initialize(connection, mode)
            ticket_table, scan_table, catalog_table = _tables(mode)
            fields = PRODUCTION_FIELDS if mode == "production" else TICKET_FIELDS
            for row in desired["tickets"]:
                _execute(connection, f"INSERT INTO {ticket_table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                         tuple(row[field] for field in fields))
            for row in desired["scans"]:
                _execute(connection, f"INSERT INTO {scan_table} ({','.join(SCAN_FIELDS)}) VALUES (?,?,?,?,?,?)",
                         tuple(row[field] for field in SCAN_FIELDS))
            if mode == "production":
                for key, value in desired["catalog"].items():
                    _execute(connection, f"INSERT INTO {catalog_table} (key,value) VALUES (?,?)", (key, value))
            if not _sqlite(connection):
                # Explicit IDs do not advance PostgreSQL's serial sequence automatically.
                last = max((row["id"] for row in desired["scans"]), default=0)
                _execute(connection, "SELECT setval(pg_get_serial_sequence(?, 'id'), ?, ?)",
                         (scan_table, max(last, 1), bool(last)))
            result["restored"] = True
        _raw(connection).commit()
        return result
    except BaseException:
        _raw(connection).rollback()
        raise


def connect_from_environment(mode, *, create=False, preview=False):
    """Open a dedicated DB connection. Credentials are read only from environment variables."""
    _mode(mode)
    url = os.environ.get("DATABASE_URL", "")
    if url:
        if not url.startswith(("postgresql://", "postgres://")):
            raise BackupError("DATABASE_URL must be a PostgreSQL URL.")
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(url, connect_timeout=10, row_factory=dict_row)
    path = os.environ.get("DB_PATH", "/tmp/onrf2_test.sqlite")
    if mode == "production":
        if (os.environ.get("LOCAL_DEVELOPMENT") != "1" or not os.environ.get("DB_PATH")
                or str(Path(path).absolute()) in DEFAULT_PATHS):
            raise BackupError("Production requires DATABASE_URL; small local fixtures require LOCAL_DEVELOPMENT=1 and an explicit non-default DB_PATH.")
    target = Path(path).absolute()
    if not target.exists() and preview:
        # A missing local target is empty; preview must not create a database file.
        connection = sqlite3.connect(":memory:", timeout=15)
    elif create:
        connection = sqlite3.connect(str(target), timeout=15)
    else:
        connection = sqlite3.connect(target.as_uri() + "?mode=rw", uri=True, timeout=15)
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _write_private(path, document):
    # Exclusive creation prevents accidental overwrite of an earlier recovery point.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("--mode", choices=("test", "production"), default=os.environ.get("APP_MODE", "test"))
    parser.add_argument("--output", type=Path, help="New private backup file (backup only).")
    parser.add_argument("--input", type=Path, help="Backup file to validate and preview (restore only).")
    parser.add_argument("--confirm", help="Exact RESTORE TEST BACKUP or RESTORE PRODUCTION BACKUP phrase.")
    args = parser.parse_args(argv)
    if args.operation == "backup" and (args.output is None or args.input is not None or args.confirm is not None):
        parser.error("backup requires --output and does not accept --input or --confirm")
    if args.operation == "restore" and (args.input is None or args.output is not None):
        parser.error("restore requires --input and does not accept --output")
    connection = None
    try:
        document = None
        if args.operation == "restore":
            document = json.loads(args.input.read_text(encoding="utf-8"))
            validate_snapshot(document, args.mode)
            if args.confirm is not None and args.confirm != "RESTORE " + args.mode.upper() + " BACKUP":
                raise BackupError("Confirmation must be exactly: RESTORE " + args.mode.upper() + " BACKUP")
        connection = connect_from_environment(args.mode,
                                              create=args.operation == "restore" and args.confirm is not None,
                                              preview=args.operation == "restore" and args.confirm is None)
        if args.operation == "backup":
            document = backup_snapshot(connection, args.mode)
            _write_private(args.output, document)
            result = {"mode": args.mode, "backed_up": True, "tickets": len(document["tickets"]), "scans": len(document["scans"])}
        else:
            result = restore_snapshot(connection, document, args.mode, args.confirm)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BackupError, json.JSONDecodeError) as exc:
        print("Backup/restore refused: " + str(exc))
    except FileExistsError:
        print("Backup/restore refused: output already exists; choose a new backup filename.")
    except Exception:
        # Driver exceptions can include DSNs and database credentials. Never print them.
        print("Backup/restore failed. Check database connectivity, schema, permissions, and file access locally.")
    finally:
        if connection is not None:
            connection.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
