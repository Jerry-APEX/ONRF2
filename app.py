"""ONRF2 ticket verification with separate test and production registries."""

import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import qrcode
from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.middleware.proxy_fix import ProxyFix

from qr_encoding import canonical_token
from catalog_loader import install_catalog, load_catalog, manifest_metadata

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
secret = os.environ.get("SESSION_SECRET", "")
if len(secret) < 32:
    raise RuntimeError("SESSION_SECRET must be at least 32 characters")
APP_MODE = os.environ.get("APP_MODE", "test").strip().lower()
if APP_MODE not in ("test", "production"):
    raise RuntimeError("APP_MODE must be test or production")
app.secret_key = secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=APP_MODE == "production" or os.environ.get("LOCAL_DEVELOPMENT") != "1",
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=28800,
    MAX_CONTENT_LENGTH=8192,
)
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if APP_MODE == "production" and not DATABASE_URL:
    raise RuntimeError("Production requires persistent PostgreSQL through DATABASE_URL")
if DATABASE_URL and not DATABASE_URL.startswith(("postgresql://", "postgres://")):
    raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
DB = Path(os.environ.get("DB_PATH", "/tmp/onrf2_test.sqlite"))
if not DATABASE_URL:
    DB.parent.mkdir(parents=True, exist_ok=True)
PRODUCTION_CATALOG = None
if APP_MODE == "production":
    PRODUCTION_CATALOG = load_catalog(
        os.environ.get("PRODUCTION_CATALOG_PATH", str(Path(__file__).with_name("data") / "production_registry.enc")),
        os.environ.get("PRODUCTION_CATALOG_KEY", ""),
        os.environ.get("EXPECTED_PRODUCTION_CATALOG_SHA256", ""),
    )

# Original test QR payloads and serial mappings: do not regenerate or renumber.
SAMPLES = [
    ("TEST-W-0001", "u_SWdQFZB3G6iriCcC7WVbVa31N8tqLS", "WHITE", "unused"),
    ("TEST-M-00001", "gMCHnRctQhsNllxdpRXlexj_Duwnnt7D", "MAIN", "unused"),
    ("TEST-VC-001", "ydLYtQTYalsp2HK2Dj8Sr_U3ObOKhOOf", "VIP CREATIVITY", "unused"),
    ("TEST-VL-001", "gm9tvk2g8bI4G73KT7bhd5hhJHIU2yKh", "VIP CULTURE", "unused"),
    ("TEST-M-00002", "xKsucOkJNmo69lGjXS1nVyKU3GScOdE_", "MAIN", "used"),
    ("TEST-W-0002", "9VF7DBtRkoXzp-KbZcSAWXlyFF2Qd4kQ", "WHITE", "cancelled"),
    ("TEST-INVALID-001", "TEST-INVALID-QR-001-REJECT", "INVALID", "invalid"),
    ("TEST-M-00001 STUB", "gMCHnRctQhsNllxdpRXlexj_Duwnnt7D", "MAIN STUB", "same-as-02"),
]


def load_test20_samples(path=None):
    """Load public test fixtures only; malformed mappings prevent startup."""
    source = Path(path) if path else Path(__file__).with_name("test_samples_20.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    samples = document.get("samples", [])
    if document.get("mode") != "test" or document.get("schema_version") != 1 or len(samples) != 20:
        raise RuntimeError("Invalid test sample collection")
    hashes = {hashlib.sha256(sample[1].encode()).hexdigest() for sample in SAMPLES}
    issued = {}
    for number, sample in enumerate(samples, 1):
        token = sample.get("token", "")
        if (sample.get("number") != number or not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9]{32}", token)
                or not str(sample.get("serial", "")).startswith("TEST20-")
                or not isinstance(sample.get("title"), str)
                or sample.get("state") not in ("unused", "used", "cancelled", "invalid", "same-as-02")):
            raise RuntimeError("Invalid test sample mapping")
        digest = hashlib.sha256(token.encode()).hexdigest()
        if sample["state"] == "same-as-02":
            original = samples[1]
            if (number != 8 or sample.get("duplicate_of") != 2 or token != original["token"]
                    or sample.get("ticket_serial") != original["ticket_serial"]):
                raise RuntimeError("Invalid test stub mapping")
            continue
        if digest in hashes:
            raise RuntimeError("Conflicting test sample mapping")
        hashes.add(digest)
        if sample["state"] == "invalid":
            if sample.get("ticket_serial") is not None:
                raise RuntimeError("Invalid samples cannot issue tickets")
        else:
            serial = sample.get("ticket_serial")
            if serial != sample["serial"] or serial in issued or sample.get("duplicate_of") is not None:
                raise RuntimeError("Conflicting test ticket serial")
            issued[serial] = sample
    if len(issued) != 18 or len(hashes) != len({sample[1] for sample in SAMPLES}) + 19:
        raise RuntimeError("Unexpected test sample totals")
    return samples


TEST20_SAMPLES = load_test20_samples()
TEST20_TICKETS = [
    (sample["ticket_serial"], sample["token"], sample["kind"], sample["state"])
    for sample in TEST20_SAMPLES if sample["state"] in ("unused", "used", "cancelled")
]
# Sample 7 uses a mask verified with multiple independent QR readers.
TEST20_QR_MASKS = {7: 0}
ORIGINAL_TITLES = {
    "WHITE": "White Ticket", "MAIN": "Main Ticket", "VIP CREATIVITY": "VIP Creativity Lounge",
    "VIP CULTURE": "VIP Culture Lounge", "INVALID": "Invalid QR Code", "MAIN STUB": "Main Ticket Stub",
}
GATES = ("MAIN GATE", "GATE A", "GATE B", "VIP GATE")
TABLE_PREFIX = "onrf2_production" if APP_MODE == "production" else "onrf2_test"
TICKETS = TABLE_PREFIX + "_tickets"
SCANS = TABLE_PREFIX + "_scans"
ATTEMPTS = TABLE_PREFIX + "_login_attempts"
CATALOG_TABLE = "onrf2_production_catalog"
ADVISORY_LOCK = 7420903 if APP_MODE == "production" else 7420902


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hashed(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Connection:
    """Small adapter; SQL values stay parameterized with both database engines."""

    def __init__(self):
        if DATABASE_URL:
            import psycopg
            from psycopg.rows import dict_row
            self.raw = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)
        else:
            self.raw = sqlite3.connect(str(DB), timeout=15)
            self.raw.row_factory = sqlite3.Row
            self.raw.execute("PRAGMA busy_timeout=15000")

    def execute(self, sql, params=()):
        return self.raw.execute(sql.replace("?", "%s") if DATABASE_URL else sql, params)

    def executemany(self, sql, values):
        cursor = self.raw.cursor()
        try:
            cursor.executemany(sql.replace("?", "%s") if DATABASE_URL else sql, values)
        finally:
            cursor.close()

    def begin_write(self):
        if not DATABASE_URL:
            self.execute("BEGIN IMMEDIATE")

    def read_snapshot(self):
        if DATABASE_URL:
            self.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        else:
            self.execute("BEGIN")

    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        try:
            self.raw.rollback() if kind else self.raw.commit()
        finally:
            self.raw.close()


def connect():
    return Connection()


def seed_data(db):
    for serial, token, kind, status in [*SAMPLES[:6], *TEST20_TICKETS]:
        used = status == "used"
        db.execute(
            f"INSERT INTO {TICKETS} "
            "(serial,code_hash,kind,status,first_time,first_gate,first_user) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT DO NOTHING",
            (serial, hashed(token), kind, status, "2026-10-15T17:00:00+00:00" if used else None,
             "GATE A" if used else None, "DEMO STAFF" if used else None),
        )
        existing = db.execute(f"SELECT code_hash,kind FROM {TICKETS} WHERE serial=?", (serial,)).fetchone()
        if not existing or existing["code_hash"] != hashed(token) or existing["kind"] != kind:
            raise RuntimeError("Conflicting test ticket mapping; existing records were preserved")


def migrate_legacy_test_data(db):
    """Preserve existing SQLite demo history; never mutate the legacy tables."""
    if DATABASE_URL:
        return
    db.execute("CREATE TABLE IF NOT EXISTS onrf2_test_meta (key TEXT PRIMARY KEY)")
    if db.execute("SELECT key FROM onrf2_test_meta WHERE key='legacy_imported'").fetchone():
        return
    tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "tickets" in tables:
        for serial, token, _, _ in SAMPLES[:6]:
            row = db.execute("SELECT * FROM tickets WHERE serial=? AND code_hash=?", (serial, hashed(token))).fetchone()
            if row:
                db.execute(
                    f"INSERT INTO {TICKETS} (serial,code_hash,kind,status,first_time,first_gate,first_user) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    tuple(row[k] for k in ("serial", "code_hash", "kind", "status", "first_time", "first_gate", "first_user")),
                )
        if "scans" in tables:
            known = {s[0] for s in SAMPLES[:6]}
            for row in db.execute("SELECT * FROM scans ORDER BY id").fetchall():
                if row["serial"] in known or row["serial"] is None:
                    db.execute(f"INSERT INTO {SCANS} (time,serial,outcome,gate,operator) VALUES (?,?,?,?,?)",
                               tuple(row[k] for k in ("time", "serial", "outcome", "gate", "operator")))
    db.execute("INSERT INTO onrf2_test_meta (key) VALUES ('legacy_imported')")


def init_db():
    with connect() as db:
        db.begin_write()
        if DATABASE_URL:
            db.execute("SELECT pg_advisory_xact_lock(?)", (ADVISORY_LOCK,))
        production_fields = ", access_area TEXT NOT NULL, print_order INTEGER UNIQUE NOT NULL" if APP_MODE == "production" else ""
        db.execute(f"""CREATE TABLE IF NOT EXISTS {TICKETS} (
            serial TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('unused','used','cancelled')),
            first_time TEXT, first_gate TEXT, first_user TEXT{production_fields})""")
        identity = "BIGSERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
        db.execute(f"""CREATE TABLE IF NOT EXISTS {SCANS} (
            id {identity}, time TEXT NOT NULL, serial TEXT, outcome TEXT NOT NULL,
            gate TEXT NOT NULL, operator TEXT NOT NULL)""")
        db.execute(f"CREATE INDEX IF NOT EXISTS {TABLE_PREFIX}_scans_time ON {SCANS}(id DESC)")
        db.execute(f"""CREATE TABLE IF NOT EXISTS {ATTEMPTS} (
            bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, started DOUBLE PRECISION NOT NULL)""")
        if APP_MODE == "production":
            db.execute(f"CREATE TABLE IF NOT EXISTS {CATALOG_TABLE} (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            install_catalog(db, TICKETS, CATALOG_TABLE, PRODUCTION_CATALOG, scans_table=SCANS)
        else:
            migrate_legacy_test_data(db)
            seed_data(db)


init_db()


def error(message, status=400):
    return jsonify(ok=False, message=message, mode=APP_MODE), status


def payload():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def user_required(admin=False, write=False):
    if session.get("role") not in ("admin", "staff") or session.get("mode") != APP_MODE:
        return error("Please sign in first.", 401)
    if admin and session["role"] != "admin":
        return error("Administrator access required.", 403)
    csrf = str(request.headers.get("X-CSRF", ""))
    expected = str(session.get("csrf", ""))
    if write and (not expected or not csrf or not hmac.compare_digest(csrf, expected)):
        return error("Security token missing. Refresh and try again.", 403)
    return None


def test_required():
    """Test QR endpoints never exist in a live production deployment."""
    if APP_MODE != "test":
        return error("Test operations are unavailable in production.", 404)
    return None


def public_ticket(row):
    if row is None:
        return None
    value = dict(row)
    if value.get("kind"):
        value["kind"] = value["kind"].replace("_", " ")
    return value


def catalog_ready(db):
    if APP_MODE != "production":
        return True
    metadata = {row["key"]:row["value"] for row in db.execute(f"SELECT key,value FROM {CATALOG_TABLE}").fetchall()}
    count = db.execute(f"SELECT COUNT(*) AS n FROM {TICKETS}").fetchone()["n"]
    return metadata == manifest_metadata(PRODUCTION_CATALOG) and count == len(PRODUCTION_CATALOG["tickets"])


@app.before_request
def same_origin_writes():
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site") == "cross-site" or (
            origin and origin.rstrip("/") != request.host_url.rstrip("/")
        ):
            return error("Cross-site requests are not permitted.", 403)


@app.after_request
def headers(response):
    response.headers.update({
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "camera=(self), microphone=()",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                   "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
                                   "font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.get("/health")
def health():
    try:
        with connect() as db:
            db.execute(f"SELECT serial FROM {TICKETS} LIMIT 1")
            if not catalog_ready(db):
                raise RuntimeError("Incomplete production catalog")
        return jsonify(status="ok", demo=APP_MODE == "test", mode=APP_MODE, version="3.0.0",
                       storage="postgresql" if DATABASE_URL else "sqlite",
                       revision=os.environ.get("RENDER_GIT_COMMIT", "local")[:12])
    except Exception:
        return jsonify(status="unavailable", mode=APP_MODE), 503


@app.get("/")
def index():
    return render_template("index.html", app_mode=APP_MODE)


@app.post("/api/login")
def login():
    values = payload()
    name = str(values.get("username", "")).strip().lower()[:64]
    password = str(values.get("password", ""))
    bucket = hashed((request.remote_addr or "unknown") + ":" + name)
    now = time.time()
    with connect() as db:
        db.begin_write()
        db.execute(f"DELETE FROM {ATTEMPTS} WHERE started < ?", (now - 300,))
        previous = db.execute(f"SELECT attempts FROM {ATTEMPTS} WHERE bucket=?", (bucket,)).fetchone()
        if previous and previous["attempts"] >= 12:
            return error("Too many attempts. Try again in five minutes.", 429)
        role = name if name in ("admin", "staff") else None
        expected = os.environ.get("ADMIN_PASSWORD" if role == "admin" else "STAFF_PASSWORD", "")
        if not role or len(expected) < 12 or not hmac.compare_digest(password.encode(), expected.encode()):
            db.execute(f"INSERT INTO {ATTEMPTS} (bucket,attempts,started) VALUES (?,1,?) "
                       f"ON CONFLICT(bucket) DO UPDATE SET attempts={ATTEMPTS}.attempts+1", (bucket, now))
            return error("Incorrect username or password.", 401)
        db.execute(f"DELETE FROM {ATTEMPTS} WHERE bucket=?", (bucket,))
    session.clear()
    session.update(role=role, username=name, csrf=os.urandom(24).hex(), mode=APP_MODE)
    session.permanent = True
    return jsonify(ok=True, role=role, username=name, csrf=session["csrf"], demo=APP_MODE == "test", mode=APP_MODE, gates=GATES)


@app.get("/api/me")
def me():
    denied = user_required()
    if denied:
        return denied
    return jsonify(ok=True, role=session["role"], username=session["username"],
                   csrf=session["csrf"], demo=APP_MODE == "test", mode=APP_MODE, gates=GATES)


@app.post("/api/logout")
def logout():
    denied = user_required(write=True)
    if denied:
        return denied
    session.clear()
    return jsonify(ok=True)


def verify_token(token, gate):
    if not isinstance(token, str) or not token.strip() or len(token) > 256:
        return error("Enter a ticket code of up to 256 characters.")
    if gate not in GATES:
        return error("Choose a check-in gate.")
    digest = hashed(canonical_token(token.strip()))
    stamp = utc()
    try:
        with connect() as db:
            db.begin_write()
            if DATABASE_URL:
                db.execute("SELECT pg_advisory_xact_lock_shared(?)", (ADVISORY_LOCK,))
            if not catalog_ready(db):
                raise RuntimeError("Production catalog is not ready")
            # The conditional UPDATE is the arbiter, never a client-side pre-check.
            # PostgreSQL rechecks the condition after a concurrent row lock;
            # SQLite serializes writers with BEGIN IMMEDIATE.
            row = db.execute(
                f"UPDATE {TICKETS} SET status='used',first_time=?,first_gate=?,first_user=? "
                "WHERE code_hash=? AND status='unused' "
                "RETURNING serial,kind,status,first_time,first_gate,first_user",
                (stamp, gate, session["username"], digest),
            ).fetchone()
            if row:
                result, reason = "ACCESS GRANTED", "VALID FIRST ENTRY"
            else:
                row = db.execute(
                    f"SELECT serial,kind,status,first_time,first_gate,first_user FROM {TICKETS} WHERE code_hash=?",
                    (digest,),
                ).fetchone()
                if row and row["status"] == "used":
                    result, reason = "ALREADY SCANNED", "ALREADY USED"
                elif row and row["status"] == "cancelled":
                    result, reason = "ACCESS DENIED", "CANCELLED"
                elif row:
                    raise RuntimeError("Unexpected ticket state after conditional check-in")
                else:
                    result, reason = "ACCESS DENIED", "INVALID TICKET"
                # A duplicate may have waited behind the winning row lock. Its
                # audit time describes the decision, after that first entry.
                stamp = utc()
            db.execute(f"INSERT INTO {SCANS} (time,serial,outcome,gate,operator) VALUES (?,?,?,?,?)",
                       (stamp, row["serial"] if row else None, result, gate, session["username"]))
            ticket = public_ticket(row)
        return jsonify(ok=True, mode=APP_MODE, result=result, reason=reason, ticket=ticket, checked_at=stamp, gate=gate)
    except Exception as exc:
        app.logger.error("Verification database failure (%s)", type(exc).__name__)
        return error("Verification unavailable. Check recent records before retrying.", 503)


@app.post("/api/scan")
def scan():
    denied = user_required(write=True)
    if denied:
        return denied
    values = payload()
    return verify_token(values.get("code", ""), values.get("gate", "MAIN GATE"))


@app.get("/api/samples")
def samples():
    denied = test_required() or user_required()
    if denied:
        return denied
    return jsonify(ok=True, collection="original", ticket_count=len(SAMPLES[:6]), samples=[
        {"i":i + 1, "serial":serial, "kind":kind, "state":state, "title":ORIGINAL_TITLES[kind],
         "qr":f"/api/sample/{i + 1}", "scan":f"/api/sample/{i + 1}/scan"}
        for i, (serial, _, kind, state) in enumerate(SAMPLES)
    ])


@app.get("/api/sample/<int:number>")
def sample_qr(number):
    denied = test_required() or user_required()
    if denied:
        return denied
    if not 1 <= number <= len(SAMPLES):
        return error("Sample not found.", 404)
    return qr_image(SAMPLES[number - 1][1])


def qr_image(token, mask_pattern=None):
    qr = qrcode.QRCode(box_size=10, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       mask_pattern=mask_pattern)
    qr.add_data(token)
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


@app.post("/api/sample/<int:number>/scan")
def simulate(number):
    denied = test_required() or user_required(write=True)
    if denied:
        return denied
    if not 1 <= number <= len(SAMPLES):
        return error("Sample not found.", 404)
    return verify_token(SAMPLES[number - 1][1], payload().get("gate", "MAIN GATE"))


@app.get("/api/collections/alphanumeric-20/samples")
def test20_samples():
    denied = test_required() or user_required()
    if denied:
        return denied
    return jsonify(ok=True, collection="alphanumeric-20", ticket_count=len(TEST20_TICKETS), samples=[
        {"i":sample["number"], "serial":sample["serial"], "kind":sample["kind"],
         "state":sample["state"], "title":sample["title"],
         "qr":f"/api/collections/alphanumeric-20/sample/{sample['number']}",
         "scan":f"/api/collections/alphanumeric-20/sample/{sample['number']}/scan"}
        for sample in TEST20_SAMPLES
    ])


@app.get("/api/collections/alphanumeric-20/sample/<int:number>")
def test20_qr(number):
    denied = test_required() or user_required()
    if denied:
        return denied
    if not 1 <= number <= len(TEST20_SAMPLES):
        return error("Sample not found.", 404)
    return qr_image(TEST20_SAMPLES[number - 1]["token"], TEST20_QR_MASKS.get(number))


@app.post("/api/collections/alphanumeric-20/sample/<int:number>/scan")
def test20_simulate(number):
    denied = test_required() or user_required(write=True)
    if denied:
        return denied
    if not 1 <= number <= len(TEST20_SAMPLES):
        return error("Sample not found.", 404)
    return verify_token(TEST20_SAMPLES[number - 1]["token"], payload().get("gate", "MAIN GATE"))


@app.get("/api/summary")
def summary():
    denied = user_required(admin=True)
    if denied:
        return denied
    with connect() as db:
        db.read_snapshot()
        tickets = [] if APP_MODE == "production" else db.execute(
            f"SELECT serial,kind,status,first_time,first_gate,first_user FROM {TICKETS} ORDER BY serial").fetchall()
        totals = db.execute(f"SELECT COUNT(*) AS total, "
                            "COALESCE(SUM(CASE WHEN status='used' THEN 1 ELSE 0 END),0) AS checked_in, "
                            f"COALESCE(SUM(CASE WHEN status='unused' THEN 1 ELSE 0 END),0) AS unused FROM {TICKETS}").fetchone()
        logs = db.execute(f"SELECT s.*,t.kind FROM {SCANS} s LEFT JOIN {TICKETS} t ON s.serial=t.serial ORDER BY s.id DESC LIMIT 100").fetchall()
        rejected = db.execute(f"SELECT COUNT(*) AS n FROM {SCANS} WHERE outcome != 'ACCESS GRANTED'").fetchone()["n"]
    catalog = {"samples":len(SAMPLES) + len(TEST20_SAMPLES),
               "initially_used":sum(t[3] == "used" for t in [*SAMPLES[:6], *TEST20_TICKETS])}
    if APP_MODE == "production":
        catalog = {"samples":0, "initially_used":0, "ticket_count":len(PRODUCTION_CATALOG["tickets"]),
                   **manifest_metadata(PRODUCTION_CATALOG),
                   "counts":{kind:sum(ticket["kind"] == kind for ticket in PRODUCTION_CATALOG["tickets"])
                             for kind in ("WHITE", "MAIN", "VIP_CREATIVITY", "VIP_CULTURE")}}
    return jsonify(ok=True, mode=APP_MODE, tickets=[public_ticket(row) for row in tickets], logs=[public_ticket(row) for row in logs],
                   stats={"total":totals["total"], "checked_in":totals["checked_in"],
                          "unused":totals["unused"], "rejected":rejected}, catalog=catalog,
                   storage="postgresql" if DATABASE_URL else "sqlite", updated_at=utc())


@app.post("/api/reset")
def reset():
    denied = test_required() or user_required(admin=True, write=True)
    if denied:
        return denied
    if payload().get("confirmation") != "RESET TEST DATA":
        return error("Confirm the test data reset first.")
    with connect() as db:
        db.begin_write()
        if DATABASE_URL:
            db.execute("SELECT pg_advisory_xact_lock(?)", (ADVISORY_LOCK,))
            db.execute(f"LOCK TABLE {TICKETS}, {SCANS} IN EXCLUSIVE MODE")
        db.execute(f"DELETE FROM {SCANS}")
        db.execute(f"DELETE FROM {TICKETS}")
        seed_data(db)
    return jsonify(ok=True, message="Test tickets and scan records have been reset.")


@app.get("/api/backup")
def backup():
    denied = user_required(admin=True)
    if denied:
        return denied
    with connect() as db:
        db.read_snapshot()
        tickets = [dict(r) for r in db.execute(f"SELECT * FROM {TICKETS} ORDER BY serial").fetchall()]
        scans = [dict(r) for r in db.execute(f"SELECT * FROM {SCANS} ORDER BY id").fetchall()]
        snapshot = {"schema_version":1, "mode":APP_MODE, "created_at":utc(), "tickets":tickets, "scans":scans}
        if APP_MODE == "production":
            snapshot["catalog"] = {r["key"]:r["value"] for r in db.execute(f"SELECT key,value FROM {CATALOG_TABLE}").fetchall()}
    output = io.BytesIO(json.dumps(snapshot, indent=2).encode())
    return send_file(output, mimetype="application/json", as_attachment=True,
                     download_name=f"onrf2-{APP_MODE}-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
