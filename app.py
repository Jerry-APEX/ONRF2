"""ONRF2 customer testing portal. Production ticket imports are intentionally absent."""

import hashlib
import hmac
import io
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import qrcode
from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
secret = os.environ.get("SESSION_SECRET", "")
if len(secret) < 32:
    raise RuntimeError("SESSION_SECRET must be at least 32 characters")
if os.environ.get("APP_MODE", "test").lower() != "test":
    raise RuntimeError("This application supports TEST MODE only")
app.secret_key = secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("LOCAL_DEVELOPMENT") != "1",
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=28800,
    MAX_CONTENT_LENGTH=8192,
)
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB = Path(os.environ.get("DB_PATH", "/tmp/onrf2_test.sqlite"))
if not DATABASE_URL:
    DB.parent.mkdir(parents=True, exist_ok=True)

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
GATES = ("MAIN GATE", "GATE A", "GATE B", "VIP GATE")
TICKETS = "onrf2_test_tickets"
SCANS = "onrf2_test_scans"
ATTEMPTS = "onrf2_test_login_attempts"


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
    for serial, token, kind, status in SAMPLES[:6]:
        used = status == "used"
        db.execute(
            f"INSERT INTO {TICKETS} "
            "(serial,code_hash,kind,status,first_time,first_gate,first_user) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT DO NOTHING",
            (serial, hashed(token), kind, status, "2026-10-15T17:00:00+00:00" if used else None,
             "GATE A" if used else None, "DEMO STAFF" if used else None),
        )


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
            db.execute("SELECT pg_advisory_xact_lock(7420902)")
        db.execute(f"""CREATE TABLE IF NOT EXISTS {TICKETS} (
            serial TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('unused','used','cancelled')),
            first_time TEXT, first_gate TEXT, first_user TEXT)""")
        identity = "BIGSERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
        db.execute(f"""CREATE TABLE IF NOT EXISTS {SCANS} (
            id {identity}, time TEXT NOT NULL, serial TEXT, outcome TEXT NOT NULL,
            gate TEXT NOT NULL, operator TEXT NOT NULL)""")
        db.execute(f"CREATE INDEX IF NOT EXISTS onrf2_test_scans_time ON {SCANS}(id DESC)")
        db.execute(f"""CREATE TABLE IF NOT EXISTS {ATTEMPTS} (
            bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, started DOUBLE PRECISION NOT NULL)""")
        migrate_legacy_test_data(db)
        seed_data(db)


init_db()


def error(message, status=400):
    return jsonify(ok=False, message=message), status


def payload():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def user_required(admin=False, write=False):
    if session.get("role") not in ("admin", "staff"):
        return error("Please sign in first.", 401)
    if admin and session["role"] != "admin":
        return error("Administrator access required.", 403)
    csrf = str(request.headers.get("X-CSRF", ""))
    expected = str(session.get("csrf", ""))
    if write and (not expected or not csrf or not hmac.compare_digest(csrf, expected)):
        return error("Security token missing. Refresh and try again.", 403)
    return None


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
        return jsonify(status="ok", demo=True, mode="test", version="2.0.0",
                       storage="postgresql" if DATABASE_URL else "sqlite",
                       revision=os.environ.get("RENDER_GIT_COMMIT", "local")[:12])
    except Exception:
        return jsonify(status="unavailable", mode="test"), 503


@app.get("/")
def index():
    return render_template("index.html")


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
    session.update(role=role, username=name, csrf=os.urandom(24).hex())
    session.permanent = True
    return jsonify(ok=True, role=role, username=name, csrf=session["csrf"], demo=True, gates=GATES)


@app.get("/api/me")
def me():
    denied = user_required()
    if denied:
        return denied
    return jsonify(ok=True, role=session["role"], username=session["username"],
                   csrf=session["csrf"], demo=True, gates=GATES)


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
    digest = hashed(token.strip())
    stamp = utc()
    try:
        with connect() as db:
            db.begin_write()
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
                else:
                    result, reason = "ACCESS DENIED", "CANCELLED" if row else "INVALID TICKET"
            db.execute(f"INSERT INTO {SCANS} (time,serial,outcome,gate,operator) VALUES (?,?,?,?,?)",
                       (stamp, row["serial"] if row else None, result, gate, session["username"]))
            ticket = dict(row) if row else None
        return jsonify(ok=True, result=result, reason=reason, ticket=ticket, checked_at=stamp, gate=gate)
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
    denied = user_required()
    if denied:
        return denied
    return jsonify(ok=True, samples=[
        {"i":i + 1, "serial":serial, "kind":kind, "state":state, "qr":f"/api/sample/{i + 1}"}
        for i, (serial, _, kind, state) in enumerate(SAMPLES)
    ])


@app.get("/api/sample/<int:number>")
def sample_qr(number):
    denied = user_required()
    if denied:
        return denied
    if not 1 <= number <= len(SAMPLES):
        return error("Sample not found.", 404)
    qr = qrcode.QRCode(box_size=10, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(SAMPLES[number - 1][1])
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


@app.post("/api/sample/<int:number>/scan")
def simulate(number):
    denied = user_required(write=True)
    if denied:
        return denied
    if not 1 <= number <= len(SAMPLES):
        return error("Sample not found.", 404)
    return verify_token(SAMPLES[number - 1][1], payload().get("gate", "MAIN GATE"))


@app.get("/api/summary")
def summary():
    denied = user_required(admin=True)
    if denied:
        return denied
    with connect() as db:
        db.read_snapshot()
        tickets = db.execute(f"SELECT serial,kind,status,first_time,first_gate,first_user FROM {TICKETS} ORDER BY serial").fetchall()
        logs = db.execute(f"SELECT s.*,t.kind FROM {SCANS} s LEFT JOIN {TICKETS} t ON s.serial=t.serial ORDER BY s.id DESC LIMIT 100").fetchall()
        rejected = db.execute(f"SELECT COUNT(*) AS n FROM {SCANS} WHERE outcome != 'ACCESS GRANTED'").fetchone()["n"]
    return jsonify(ok=True, tickets=[dict(row) for row in tickets], logs=[dict(row) for row in logs],
                   stats={"total":len(tickets), "checked_in":sum(t["status"] == "used" for t in tickets),
                          "unused":sum(t["status"] == "unused" for t in tickets), "rejected":rejected},
                   storage="postgresql" if DATABASE_URL else "sqlite", updated_at=utc())


@app.post("/api/reset")
def reset():
    denied = user_required(admin=True, write=True)
    if denied:
        return denied
    if payload().get("confirmation") != "RESET TEST DATA":
        return error("Confirm the test data reset first.")
    with connect() as db:
        db.begin_write()
        if DATABASE_URL:
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
    output = io.BytesIO(json.dumps({"schema_version":1, "mode":"test", "created_at":utc(),
                                   "tickets":tickets, "scans":scans}, indent=2).encode())
    return send_file(output, mimetype="application/json", as_attachment=True,
                     download_name=f"onrf2-test-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
