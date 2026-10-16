"""Recovery checks use synthetic tickets only and never connect to hosted databases."""

import copy
import hashlib
import json
import sqlite3
import stat

import pytest

from ops_backup import (
    BackupError, backup_snapshot, catalog_hash, connect_from_environment,
    main, restore_snapshot, validate_snapshot,
)


def snapshot(mode="production"):
    serial = "ONRF2-M-00001" if mode == "production" else "TEST20-M-00001"
    ticket = {"serial": serial, "code_hash": hashlib.sha256(b"synthetic-no-real-QR").hexdigest(),
              "kind": "MAIN", "status": "used", "first_time": "2026-10-16T12:00:00+00:00",
              "first_gate": "GATE A", "first_user": "staff"}
    if mode == "production":
        ticket.update(access_area="GENERAL ADMISSION", print_order=1)
    document = {"schema_version": 1, "mode": mode, "created_at": "2026-10-16T12:05:00+00:00",
                "tickets": [ticket], "scans": [
                    {"id": 7, "time": ticket["first_time"], "serial": serial, "outcome": "ACCESS GRANTED", "gate": "GATE A", "operator": "staff"},
                    {"id": 12, "time": "2026-10-16T12:01:00+00:00", "serial": serial, "outcome": "ALREADY SCANNED", "gate": "GATE B", "operator": "admin"},
                    {"id": 14, "time": "2026-10-16T12:02:00+00:00", "serial": None, "outcome": "ACCESS DENIED", "gate": "GATE B", "operator": "admin"},
                ]}
    if mode == "production":
        document["catalog"] = {"schema_version": "1", "catalog_sha256": catalog_hash(document["tickets"]),
                               "source_sha256": "b" * 64, "source_filename": "SYNTHETIC.xlsx"}
    return document


def database():
    return sqlite3.connect(":memory:")


def contents(db):
    return list(db.iterdump())


def confirm(mode):
    return "RESTORE " + mode.upper() + " BACKUP"


@pytest.mark.parametrize("mode", ["test", "production"])
def test_preview_is_read_only_and_exact_confirmation_required(mode):
    db = database()
    original = contents(db)
    result = restore_snapshot(db, snapshot(mode), mode)
    assert result == {"mode": mode, "preview": True, "tickets": 1, "scans": 3, "unchanged": False, "restored": False}
    assert contents(db) == original
    for phrase in ("yes", "RESTORE", confirm(mode).lower(), confirm(mode) + " ", confirm("test" if mode == "production" else "production")):
        with pytest.raises(BackupError, match="Confirmation"):
            restore_snapshot(db, snapshot(mode), mode, phrase)
        assert contents(db) == original
    db.close()


@pytest.mark.parametrize("mode", ["test", "production"])
def test_roundtrip_preserves_original_entry_and_explicit_scan_ids(mode):
    source = database()
    desired = snapshot(mode)
    assert restore_snapshot(source, desired, mode, confirm(mode))["restored"]
    exported = backup_snapshot(source, mode)
    assert validate_snapshot(exported, mode) == validate_snapshot(desired, mode)
    assert "synthetic-no-real-QR" not in json.dumps(exported)
    target = database()
    restore_snapshot(target, exported, mode, confirm(mode))
    assert validate_snapshot(backup_snapshot(target, mode), mode) == validate_snapshot(desired, mode)
    before = contents(target)
    result = restore_snapshot(target, desired, mode, confirm(mode))
    assert result["unchanged"] and not result["restored"]
    assert contents(target) == before
    scan_table = "onrf2_" + mode + "_scans"
    target.execute(f"INSERT INTO {scan_table} (time,serial,outcome,gate,operator) VALUES (?,?,?,?,?)",
                   ("2026-10-16T12:03:00+00:00", None, "ACCESS DENIED", "GATE B", "staff"))
    assert target.execute(f"SELECT MAX(id) FROM {scan_table}").fetchone()[0] == 15
    target.rollback()
    source.close()
    target.close()


@pytest.mark.parametrize("mode", ["test", "production"])
def test_different_nonempty_target_is_never_replaced(mode):
    db = database()
    restore_snapshot(db, snapshot(mode), mode, confirm(mode))
    before = contents(db)
    other = snapshot(mode)
    other["scans"].pop()
    for phrase in (None, confirm(mode)):
        with pytest.raises(BackupError, match="different records"):
            restore_snapshot(db, other, mode, phrase)
        assert contents(db) == before
    db.close()


def invalid_document(case):
    doc = snapshot()
    ticket = doc["tickets"][0]
    if case == "duplicate-ticket":
        doc["tickets"].append(copy.deepcopy(ticket))
    elif case == "duplicate-hash":
        duplicate = {**ticket, "serial": "ONRF2-M-00002", "print_order": 2}
        doc["tickets"].append(duplicate)
    elif case == "duplicate-order":
        duplicate = {**ticket, "serial": "ONRF2-M-00002", "code_hash": "a" * 64}
        doc["tickets"].append(duplicate)
    elif case == "raw-token":
        ticket["qr_payload"] = "do-not-accept-a-token"
    elif case == "secret-field":
        doc["catalog"]["DATABASE_URL"] = "do-not-accept-a-secret"
    elif case == "bad-hash":
        ticket["code_hash"] = "not-a-hash"
    elif case == "status":
        ticket["status"] = "invalid"
    elif case == "used-missing-time":
        ticket["first_time"] = None
    elif case == "naive-time":
        ticket["first_time"] = "2026-10-16T12:00:00"
    elif case == "unused-with-entry":
        ticket["status"] = "unused"
    elif case == "unknown-scan":
        doc["scans"][0]["serial"] = "ONRF2-M-99999"
    elif case == "duplicate-scan":
        doc["scans"].append(copy.deepcopy(doc["scans"][0]))
    elif case == "bad-scan-id":
        doc["scans"][0]["id"] = True
    elif case == "first-entry-conflict":
        doc["scans"][0]["gate"] = "GATE B"
    elif case == "second-grant":
        doc["scans"].append({**doc["scans"][0], "id": 16})
    elif case == "wrong-kind":
        ticket["kind"] = "WHITE"
    elif case == "manifest-mismatch":
        ticket["access_area"] = "VIP CREATIVITY LOUNGE"
    elif case == "source-path":
        doc["catalog"]["source_filename"] = "../SYNTHETIC.xlsx"
    elif case == "bad-source-sha":
        doc["catalog"]["source_sha256"] = "missing"
    elif case == "schema-version":
        doc["schema_version"] = True
    return doc


@pytest.mark.parametrize("case", [
    "duplicate-ticket", "duplicate-hash", "duplicate-order", "raw-token", "secret-field", "bad-hash", "status",
    "used-missing-time", "naive-time", "unused-with-entry", "unknown-scan", "duplicate-scan", "bad-scan-id",
    "first-entry-conflict", "second-grant", "wrong-kind", "manifest-mismatch", "source-path", "bad-source-sha", "schema-version",
])
def test_invalid_backups_do_not_change_any_target_record(case):
    db = database()
    before = contents(db)
    with pytest.raises(BackupError):
        restore_snapshot(db, invalid_document(case), "production", confirm("production"))
    assert contents(db) == before
    db.close()


def test_mode_isolation_and_busy_connection_are_rejected():
    db = database()
    with pytest.raises(BackupError, match="mode"):
        restore_snapshot(db, snapshot("test"), "production", confirm("production"))
    db.execute("CREATE TABLE unrelated (value TEXT)")
    db.execute("INSERT INTO unrelated VALUES ('uncommitted')")
    with pytest.raises(BackupError, match="idle connection"):
        restore_snapshot(db, snapshot("test"), "test", confirm("test"))
    assert db.in_transaction
    db.rollback()
    db.close()


def test_sql_failure_rolls_back_all_new_tickets_and_scan_history():
    db = database()
    # A target-side database constraint fails after ticket insert; no partial restore survives.
    db.execute("CREATE TABLE onrf2_test_tickets (serial TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, first_time TEXT, first_gate TEXT, first_user TEXT)")
    db.execute("CREATE TABLE onrf2_test_scans (id INTEGER PRIMARY KEY, time TEXT NOT NULL, serial TEXT, outcome TEXT CHECK(outcome='ACCESS DENIED'), gate TEXT, operator TEXT)")
    before = contents(db)
    with pytest.raises(sqlite3.IntegrityError):
        restore_snapshot(db, snapshot("test"), "test", confirm("test"))
    assert contents(db) == before
    db.close()


def test_production_sqlite_guards_and_real_registry_size(monkeypatch, tmp_path):
    db = database()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_DEVELOPMENT", raising=False)
    with pytest.raises(BackupError, match="SQLite"):
        restore_snapshot(db, snapshot(), "production", confirm("production"))
    with pytest.raises(BackupError, match="Production requires"):
        connect_from_environment("production")
    monkeypatch.setenv("LOCAL_DEVELOPMENT", "1")
    monkeypatch.setenv("DB_PATH", "/tmp/onrf2_test.sqlite")
    with pytest.raises(BackupError, match="non-default"):
        restore_snapshot(db, snapshot(), "production", confirm("production"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "synthetic-only.sqlite"))
    large = snapshot()
    large["tickets"] = []
    large["scans"] = []
    for n in range(1, 13001):
        row = {**snapshot()["tickets"][0], "serial": f"ONRF2-M-{n:05d}", "status": "unused",
               "code_hash": hashlib.sha256(f"synthetic-{n}".encode()).hexdigest(), "print_order": n,
               "first_time": None, "first_gate": None, "first_user": None}
        large["tickets"].append(row)
    large["catalog"]["catalog_sha256"] = catalog_hash(large["tickets"])
    with pytest.raises(BackupError, match="13,000-ticket"):
        restore_snapshot(db, large, "production", confirm("production"))
    assert contents(db) == ["BEGIN TRANSACTION;", "COMMIT;"]
    db.close()


def test_cli_backup_file_is_private_no_overwrite_and_restore_defaults_to_preview(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "source.sqlite"
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DB_PATH", str(db_path))
    db = sqlite3.connect(db_path)
    restore_snapshot(db, snapshot("test"), "test", confirm("test"))
    db.close()
    output = tmp_path / "private-backup.json"
    assert main(["backup", "--mode", "test", "--output", str(output)]) == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    expected = output.read_bytes()
    assert main(["backup", "--mode", "test", "--output", str(output)]) == 1
    assert output.read_bytes() == expected
    target_path = tmp_path / "target.sqlite"
    monkeypatch.setenv("DB_PATH", str(target_path))
    assert main(["restore", "--mode", "test", "--input", str(output), "--confirm", "yes"]) == 1
    assert not target_path.exists()
    assert main(["restore", "--mode", "test", "--input", str(output)]) == 0
    assert not target_path.exists()
    assert '"preview": true' in capsys.readouterr().out
    assert main(["restore", "--mode", "test", "--input", str(output), "--confirm", confirm("test")]) == 0


def test_cli_does_not_print_database_secrets(monkeypatch, tmp_path, capsys):
    document = tmp_path / "backup.json"
    document.write_text(json.dumps(snapshot("test")))
    monkeypatch.setenv("DATABASE_URL", "postgresql://private-user:private-password@does-not-exist.invalid/database")
    assert main(["restore", "--mode", "test", "--input", str(document)]) == 1
    output = capsys.readouterr().out
    assert "private-password" not in output and "private-user" not in output


def test_postgres_restore_preserves_snapshot_and_advances_identity(tmp_path):
    pgserver = pytest.importorskip("pgserver")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    pg = pgserver.get_server(tmp_path / "postgres", cleanup_mode="stop")
    try:
        for mode in ("test", "production"):
            with psycopg.connect(pg.get_uri(), row_factory=dict_row) as db:
                before = db.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public'").fetchone()["n"]
                db.commit()
                document = snapshot(mode)
                assert restore_snapshot(db, document, mode)["preview"]
                assert db.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public'").fetchone()["n"] == before
                db.commit()
                assert restore_snapshot(db, document, mode, confirm(mode))["restored"]
                assert validate_snapshot(backup_snapshot(db, mode), mode) == validate_snapshot(document, mode)
                assert restore_snapshot(db, document, mode, confirm(mode))["unchanged"]
                changed = snapshot(mode)
                changed["scans"].pop()
                with pytest.raises(BackupError, match="different records"):
                    restore_snapshot(db, changed, mode, confirm(mode))
                table = "onrf2_" + mode + "_scans"
                inserted = db.execute(f"INSERT INTO {table} (time,serial,outcome,gate,operator) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                                      ("2026-10-16T12:03:00+00:00", None, "ACCESS DENIED", "GATE B", "staff")).fetchone()
                assert inserted["id"] == 15
                db.rollback()
    finally:
        pg.cleanup()
