"""Validate and decrypt the private production registry without exposing QR tokens.

The public repository contains only authenticated ciphertext. The independent
Fernet key and pinned catalog digest belong in server environment variables.
"""

import gzip
import hashlib
import hmac
import io
import json
import re
from collections import Counter
from pathlib import Path

PRODUCTION_COUNTS = {"WHITE": 1000, "MAIN": 11000, "VIP_CREATIVITY": 500, "VIP_CULTURE": 500}
ACCESS_AREAS = {"WHITE": "GENERAL ADMISSION", "MAIN": "GENERAL ADMISSION",
                "VIP_CREATIVITY": "VIP CREATIVITY LOUNGE", "VIP_CULTURE": "VIP CULTURE LOUNGE"}
TICKET_FIELDS = {"serial", "code_hash", "kind", "access_area", "print_order"}
HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_BUNDLE_BYTES = 8 * 1024 * 1024


def catalog_digest(tickets):
    return hashlib.sha256(json.dumps(tickets, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def expected_serials():
    values = []
    for prefix, count, width, kind in (("W", 1000, 4, "WHITE"), ("M", 11000, 5, "MAIN"),
                                        ("VC", 500, 3, "VIP_CREATIVITY"), ("VL", 500, 3, "VIP_CULTURE")):
        values.extend((f"ONRF2-{prefix}-{number:0{width}d}", kind) for number in range(1, count + 1))
    return values


def validate_catalog(document, expected_digest, *, expected_counts=None, enforce_serials=True):
    """Validate a whole registry. Alternate counts are for synthetic unit tests only.

    Application startup always uses the fixed 13,000-ticket defaults. It never
    takes count, serial validation, or schema overrides from environment input.
    """
    counts = PRODUCTION_COUNTS if expected_counts is None else expected_counts
    if not isinstance(expected_digest, str) or not HEX_DIGEST.fullmatch(expected_digest):
        raise ValueError("A pinned production catalog SHA256 is required")
    if not isinstance(document, dict) or set(document) != {"schema_version", "mode", "source", "catalog_sha256", "tickets"}:
        raise ValueError("Unexpected production catalog schema")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["mode"] != "production":
        raise ValueError("Unexpected production catalog version or mode")
    source = document["source"]
    if (not isinstance(source, dict) or set(source) != {"filename", "sha256"}
            or not isinstance(source["sha256"], str) or not HEX_DIGEST.fullmatch(source["sha256"])
            or not isinstance(source["filename"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+\.xlsx", source["filename"])):
        raise ValueError("Invalid production source manifest")
    tickets = document["tickets"]
    if not isinstance(tickets, list) or len(tickets) != sum(counts.values()):
        raise ValueError("Unexpected production ticket count")
    serials, hashes, actual_counts = set(), set(), Counter()
    serial_map = expected_serials() if enforce_serials else None
    if enforce_serials and len(tickets) != len(serial_map):
        raise ValueError("Production serial ranges require the complete registry")
    for order, ticket in enumerate(tickets, 1):
        if not isinstance(ticket, dict) or set(ticket) != TICKET_FIELDS:
            raise ValueError("Unexpected production ticket fields")
        serial, digest, kind = ticket["serial"], ticket["code_hash"], ticket["kind"]
        if (not isinstance(serial, str) or not re.fullmatch(r"ONRF2-[A-Z]{1,2}-[0-9]{3,5}", serial)
                or not isinstance(digest, str) or not HEX_DIGEST.fullmatch(digest)
                or not isinstance(kind, str) or kind not in counts or ticket["access_area"] != ACCESS_AREAS.get(kind)
                or type(ticket["print_order"]) is not int or ticket["print_order"] != order
                or serial in serials or digest in hashes):
            raise ValueError("Invalid or duplicate production ticket mapping")
        if serial_map is not None and (serial, kind) != serial_map[order - 1]:
            raise ValueError("Production serial mapping differs from the factory registry")
        serials.add(serial)
        hashes.add(digest)
        actual_counts[kind] += 1
    if dict(actual_counts) != counts:
        raise ValueError("Unexpected production ticket type totals")
    actual_digest = catalog_digest(tickets)
    if (not isinstance(document["catalog_sha256"], str)
            or not hmac.compare_digest(actual_digest, expected_digest)
            or not hmac.compare_digest(actual_digest, document["catalog_sha256"])):
        raise ValueError("Production catalog SHA256 does not match the pinned release")
    return document


def load_catalog(path, key, expected_digest):
    """Decrypt a bounded Fernet/gzip document and enforce the full factory schema."""
    from cryptography.fernet import Fernet, InvalidToken
    if not isinstance(key, str) or not key:
        raise RuntimeError("PRODUCTION_CATALOG_KEY is required in production")
    try:
        cipher = Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise RuntimeError("Invalid production catalog encryption key") from None
    source = Path(path)
    try:
        if source.stat().st_size > MAX_BUNDLE_BYTES:
            raise ValueError("Production catalog exceeds the size limit")
        compressed = cipher.decrypt(source.read_bytes())
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            plaintext = stream.read(MAX_BUNDLE_BYTES + 1)
        if len(plaintext) > MAX_BUNDLE_BYTES:
            raise ValueError("Production catalog exceeds the size limit")
        document = json.loads(plaintext.decode("utf-8"))
        return validate_catalog(document, expected_digest)
    except (OSError, EOFError, ValueError, TypeError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Production catalog could not be authenticated and validated") from None


def manifest_metadata(document):
    return {"schema_version": "1", "catalog_sha256": document["catalog_sha256"],
            "source_sha256": document["source"]["sha256"], "source_filename": document["source"]["filename"]}


def install_catalog(db, tickets_table, metadata_table, document, *, scans_table=None):
    """Install in the caller's write transaction; never overwrite entry history."""
    expected = {ticket["serial"]: ticket for ticket in document["tickets"]}
    rows = db.execute(f"SELECT serial,code_hash,kind,access_area,print_order FROM {tickets_table}").fetchall()
    existing = {}
    for row in rows:
        record = dict(row)
        if record["serial"] not in expected or record != expected[record["serial"]]:
            raise RuntimeError("Conflicting production ticket mapping; all existing records were preserved")
        existing[record["serial"]] = record
    metadata = manifest_metadata(document)
    stored = {row["key"]: row["value"] for row in db.execute(f"SELECT key,value FROM {metadata_table}").fetchall()}
    audit_table = scans_table or tickets_table.removesuffix("_tickets") + "_scans"
    audit_count = db.execute(f"SELECT COUNT(*) AS n FROM {audit_table}").fetchone()["n"]
    # Only a completely empty registry is a first installation. Repairing a lost
    # ticket as unused could reissue admission after its original successful scan.
    if existing or stored or audit_count:
        if stored != metadata or len(existing) != len(expected):
            raise RuntimeError("Incomplete or conflicting production registry; restore a verified backup. Existing history was preserved")
        return
    additions = [(ticket["serial"], ticket["code_hash"], ticket["kind"], "unused", None, None, None,
                  ticket["access_area"], ticket["print_order"])
                 for ticket in document["tickets"]]
    if additions:
        db.executemany(f"INSERT INTO {tickets_table} "
                       "(serial,code_hash,kind,status,first_time,first_gate,first_user,access_area,print_order) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", additions)
    for key, value in metadata.items():
        db.execute(f"INSERT INTO {metadata_table} (key,value) VALUES (?,?) ON CONFLICT DO NOTHING", (key, value))
    if db.execute(f"SELECT COUNT(*) AS n FROM {tickets_table}").fetchone()["n"] != len(expected):
        raise RuntimeError("Incomplete production catalog import")
