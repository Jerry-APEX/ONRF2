"""Production behavior uses deliberately synthetic secrets, never issued tickets."""

import base64
import copy
import gzip
import hashlib
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet

from catalog_loader import catalog_digest, install_catalog, load_catalog, manifest_metadata, validate_catalog
from conftest import login
from qr_encoding import print_token


def synthetic_catalog():
    kinds = [('W', 4, 'WHITE', 'GENERAL ADMISSION'), ('M', 5, 'MAIN', 'GENERAL ADMISSION'),
             ('VC', 3, 'VIP_CREATIVITY', 'VIP CREATIVITY LOUNGE'), ('VL', 3, 'VIP_CULTURE', 'VIP CULTURE LOUNGE')]
    tokens, tickets = [], []
    for order, (prefix, width, kind, area) in enumerate(kinds, 1):
        token = base64.urlsafe_b64encode(bytes([order]) * 24).decode('ascii')
        tokens.append(token)
        tickets.append({'serial':f'ONRF2-{prefix}-{1:0{width}d}', 'code_hash':hashlib.sha256(token.encode()).hexdigest(),
                        'kind':kind, 'access_area':area, 'print_order':order})
    document = {'schema_version':1, 'mode':'production',
                'source':{'filename':'SYNTHETIC_TEST_REGISTRY.xlsx','sha256':'a' * 64},
                'catalog_sha256':catalog_digest(tickets), 'tickets':tickets}
    return document, tokens


@pytest.fixture
def production(module, monkeypatch):
    document, tokens = synthetic_catalog()
    validate_catalog(document, document['catalog_sha256'],
                     expected_counts={kind:1 for kind in ('WHITE','MAIN','VIP_CREATIVITY','VIP_CULTURE')},
                     enforce_serials=False)
    for key, value in {'APP_MODE':'production', 'TABLE_PREFIX':'onrf2_production',
                       'TICKETS':'onrf2_production_tickets','SCANS':'onrf2_production_scans',
                       'ATTEMPTS':'onrf2_production_login_attempts', 'ADVISORY_LOCK':7420903,
                       'PRODUCTION_CATALOG':document}.items():
        monkeypatch.setattr(module, key, value)
    # This in-process test adapter is not an environment or deployment bypass:
    # actual APP_MODE=production imports always require PostgreSQL.
    module.init_db()
    return module, document, tokens


def test_catalog_requires_complete_pinned_factory_manifest(tmp_path):
    document, _ = synthetic_catalog()
    with pytest.raises(ValueError, match='ticket count'):
        validate_catalog(document, document['catalog_sha256'])
    counts = {ticket['kind']:1 for ticket in document['tickets']}
    check = lambda value, digest=document['catalog_sha256']: validate_catalog(
        value, digest, expected_counts=counts, enforce_serials=False)
    assert check(document) is document
    for mutate in [lambda d:d['tickets'][0].update(code_hash='not-a-hash'),
                   lambda d:d['tickets'][1].update(serial=d['tickets'][0]['serial']),
                   lambda d:d['tickets'][1].update(code_hash=d['tickets'][0]['code_hash']),
                   lambda d:d['tickets'][1].update(print_order=1),
                   lambda d:d['tickets'][0].update(qr_payload='should-never-be-stored'),
                   lambda d:d['source'].update(sha256='bad'),
                   lambda d:d.update(mode='test')]:
        changed = copy.deepcopy(document)
        mutate(changed)
        with pytest.raises(ValueError):
            check(changed)
    with pytest.raises(ValueError, match='SHA256'):
        check(document, '0' * 64)
    key = Fernet.generate_key()
    encrypted = Fernet(key).encrypt(gzip.compress(json.dumps(document).encode(), mtime=0))
    bundle = tmp_path / 'catalog.enc'
    bundle.write_bytes(encrypted)
    # Even authentic ciphertext may not bypass the 13,000-ticket application contract.
    with pytest.raises(RuntimeError, match='authenticated and validated'):
        load_catalog(bundle, key.decode(), document['catalog_sha256'])
    with pytest.raises(RuntimeError, match='authenticated and validated'):
        load_catalog(bundle, Fernet.generate_key().decode(), document['catalog_sha256'])
    with pytest.raises(RuntimeError, match='required'):
        load_catalog(bundle, '', document['catalog_sha256'])


def test_production_startup_has_no_local_sqlite_bypass(module):
    for local in ('', '1'):
        result = subprocess.run([sys.executable, '-c', 'import app'], capture_output=True, text=True,
                                env=dict(os.environ, APP_MODE='production', DATABASE_URL='', LOCAL_DEVELOPMENT=local))
        assert result.returncode and 'Production requires persistent PostgreSQL' in result.stderr
    invalid = subprocess.run([sys.executable, '-c', 'import app'], capture_output=True, text=True,
                             env=dict(os.environ, APP_MODE='unknown', DATABASE_URL=''))
    assert invalid.returncode and 'APP_MODE must be test or production' in invalid.stderr


def test_production_mode_isolated_and_all_sample_routes_unavailable(production):
    portal, document, _ = production
    browser, headers = login(portal)
    assert browser.get('/api/me').json['mode'] == 'production'
    assert browser.get('/health').json['mode'] == 'production'
    page = browser.get('/').get_data(as_text=True)
    assert 'data-mode="production"' in page and 'LIVE CHECK-IN' in page
    import re
    assert re.search(r'<button[^>]+id="samplesTab"[^>]+hidden', page)
    assert re.search(r'<button[^>]+id="resetData"[^>]+hidden', page)
    assert 'EXPORT CHECK-IN BACKUP' in page
    for path in ['/api/samples','/api/sample/1','/api/collections/alphanumeric-20/samples',
                 '/api/collections/alphanumeric-20/sample/1']:
        assert browser.get(path).status_code == 404
    for path in ['/api/sample/1/scan','/api/collections/alphanumeric-20/sample/1/scan','/api/reset']:
        assert browser.post(path, json={'confirmation':'RESET TEST DATA'}, headers=headers).status_code == 404
    for token in [portal.SAMPLES[0][1], portal.TEST20_SAMPLES[0]['token']]:
        response = browser.post('/api/scan', json={'code':token}, headers=headers)
        assert response.json['result'] == 'ACCESS DENIED' and response.json['reason'] == 'INVALID TICKET'
        assert response.json['mode'] == 'production'
    summary = browser.get('/api/summary').json
    assert summary['tickets'] == []
    assert summary['stats'] == {'total':4,'checked_in':0,'unused':4,'rejected':2}
    assert summary['catalog']['samples'] == 0
    assert summary['catalog']['catalog_sha256'] == document['catalog_sha256']
    assert summary['mode'] == 'production'
    with portal.connect() as db:
        assert db.execute('SELECT COUNT(*) AS n FROM onrf2_test_tickets').fetchone()['n'] == 24
        assert db.execute('SELECT COUNT(*) AS n FROM onrf2_production_tickets').fetchone()['n'] == 4


def test_test_mode_rejects_synthetic_production_codes(module):
    _, tokens = synthetic_catalog()
    browser, headers = login(module)
    for token in tokens:
        for value in (token, print_token(token)):
            response = browser.post('/api/scan', headers=headers, json={'code':value})
            assert response.json['result'] == 'ACCESS DENIED'


def test_production_cross_format_concurrency_and_restart_preserve_first_entry(production):
    portal, document, tokens = production
    count = 12
    barrier = threading.Barrier(count)
    def scan(index):
        browser, headers = login(portal, 'staff' if index % 2 else 'admin')
        barrier.wait(timeout=20)
        response = browser.post('/api/scan', headers=headers,
                                json={'code':tokens[0] if index % 2 else print_token(tokens[0]),
                                      'gate':portal.GATES[index % len(portal.GATES)]})
        assert response.status_code == 200
        return response.json
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(scan, range(count)))
    granted = [result for result in results if result['result'] == 'ACCESS GRANTED']
    assert len(granted) == 1
    assert all(result['ticket'] == granted[0]['ticket'] for result in results)
    assert sum(result['result'] == 'ALREADY SCANNED' for result in results) == count - 1
    portal.init_db()
    portal.init_db()
    browser, headers = login(portal)
    repeat = browser.post('/api/scan', headers=headers, json={'code':print_token(tokens[0]),'gate':'CC GATE'}).json
    assert repeat['ticket'] == granted[0]['ticket']
    backup = browser.get('/api/backup').json
    assert backup['mode'] == 'production' and len(backup['tickets']) == 4 and len(backup['scans']) == count + 1
    assert backup['catalog'] == manifest_metadata(document)
    encoded = json.dumps(backup)
    assert all(token not in encoded and print_token(token) not in encoded for token in tokens)
    with portal.connect() as db:
        assert db.execute('SELECT COUNT(*) AS n FROM onrf2_test_scans').fetchone()['n'] == 0


def test_conflicting_import_rolls_back_and_health_fails_with_incomplete_manifest(production):
    portal, document, tokens = production
    browser, headers = login(portal)
    first = browser.post('/api/scan', headers=headers, json={'code':tokens[0]}).json
    changed = copy.deepcopy(document)
    changed['tickets'][0]['code_hash'] = '0' * 64
    with pytest.raises(RuntimeError, match='Conflicting'):
        with portal.connect() as db:
            db.begin_write()
            db.execute(f"INSERT INTO {portal.SCANS} (time,serial,outcome,gate,operator) VALUES (?,?,?,?,?)",
                       ('2026-10-16T12:00:00+00:00',None,'ACCESS DENIED','GATE A','will-rollback'))
            install_catalog(db, portal.TICKETS, portal.CATALOG_TABLE, changed)
    snapshot = browser.get('/api/backup').json
    assert len(snapshot['scans']) == 1
    ticket = next(ticket for ticket in snapshot['tickets'] if ticket['serial'] == first['ticket']['serial'])
    assert ticket['first_time'] == first['ticket']['first_time']
    with portal.connect() as db:
        db.execute(f"DELETE FROM {portal.CATALOG_TABLE} WHERE key='catalog_sha256'")
    assert browser.get('/health').status_code == 503
    assert browser.get('/health').json['mode'] == 'production'
    assert browser.post('/api/scan', headers=headers, json={'code':tokens[1]}).status_code == 503


def test_changing_mode_invalidates_existing_sessions(module, monkeypatch):
    browser, _ = login(module)
    monkeypatch.setattr(module, 'APP_MODE', 'production')
    assert browser.get('/api/me').status_code == 401


def test_production_postgresql_cross_format_race_uses_one_admission(production, monkeypatch, tmp_path):
    """Optional local PostgreSQL coverage; no Render or real production QR is used."""
    pgserver = pytest.importorskip('pgserver')
    portal, _, tokens = production
    server = pgserver.get_server(tmp_path / 'synthetic-production-pg', cleanup_mode='stop')
    try:
        monkeypatch.setattr(portal, 'DATABASE_URL', server.get_uri())
        portal.init_db()
        count = 12
        barrier = threading.Barrier(count)
        def scan(index):
            browser, headers = login(portal, 'staff' if index % 2 else 'admin')
            barrier.wait(timeout=20)
            response = browser.post('/api/scan', headers=headers,
                                    json={'code':tokens[1] if index % 2 else print_token(tokens[1]),
                                          'gate':portal.GATES[index % len(portal.GATES)]})
            assert response.status_code == 200
            return response.json
        with ThreadPoolExecutor(max_workers=count) as pool:
            responses = list(pool.map(scan, range(count)))
        winners = [response for response in responses if response['result'] == 'ACCESS GRANTED']
        assert len(winners) == 1
        assert all(response['ticket'] == winners[0]['ticket'] for response in responses)
        assert sum(response['result'] == 'ALREADY SCANNED' for response in responses) == count - 1
        portal.init_db()
        browser, headers = login(portal)
        again = browser.post('/api/scan', headers=headers, json={'code':print_token(tokens[1])}).json
        assert again['ticket'] == winners[0]['ticket']
        assert browser.get('/api/summary').json['stats'] == {'total':4,'checked_in':1,'unused':3,'rejected':count}
        with portal.connect() as db:
            assert db.execute("SELECT to_regclass('onrf2_test_tickets') AS found").fetchone()['found'] is None
    finally:
        server.cleanup()


def test_duplicate_audit_uses_decision_time_after_waiting_for_first_entry(production, monkeypatch):
    portal, _, tokens = production
    browser, headers = login(portal)
    with portal.connect() as db:
        db.execute(f"UPDATE {portal.TICKETS} SET status='used',first_time=?,first_gate=?,first_user=? WHERE serial=?",
                   ('2026-10-16T12:00:01+00:00','GATE A','first-operator','ONRF2-W-0001'))
    times = iter(['2026-10-16T12:00:00+00:00','2026-10-16T12:00:02+00:00','2026-10-16T12:00:03+00:00'])
    monkeypatch.setattr(portal, 'utc', lambda:next(times))
    response = browser.post('/api/scan', headers=headers, json={'code':tokens[0]}).json
    assert response['result'] == 'ALREADY SCANNED'
    assert response['ticket']['first_time'] == '2026-10-16T12:00:01+00:00'
    assert response['checked_at'] == '2026-10-16T12:00:02+00:00'
    from ops_backup import validate_snapshot
    snapshot = browser.get('/api/backup').json
    validate_snapshot(snapshot, 'production')


def test_restart_never_reissues_a_lost_used_ticket_with_retained_audit(production):
    portal, _, tokens = production
    browser, headers = login(portal)
    lost = browser.post('/api/scan', headers=headers, json={'code':tokens[0]}).json['ticket']
    retained = browser.post('/api/scan', headers=headers, json={'code':tokens[1]}).json['ticket']
    with portal.connect() as db:
        before = [dict(row) for row in db.execute(f'SELECT * FROM {portal.SCANS} ORDER BY id').fetchall()]
        db.execute(f'DELETE FROM {portal.TICKETS} WHERE serial=?', (lost['serial'],))
    with pytest.raises(RuntimeError, match='Incomplete or conflicting production registry'):
        portal.init_db()
    with portal.connect() as db:
        assert db.execute(f'SELECT serial FROM {portal.TICKETS} WHERE serial=?', (lost['serial'],)).fetchone() is None
        row = db.execute(f'SELECT * FROM {portal.TICKETS} WHERE serial=?', (retained['serial'],)).fetchone()
        assert row['status'] == 'used' and row['first_time'] == retained['first_time']
        after = [dict(row) for row in db.execute(f'SELECT * FROM {portal.SCANS} ORDER BY id').fetchall()]
        assert after == before
    assert browser.get('/health').status_code == 503
    assert browser.post('/api/scan', headers=headers, json={'code':tokens[0]}).status_code == 503


@pytest.mark.parametrize('loss', ['metadata_rows','metadata_table','all_tickets_and_metadata'])
def test_restart_requires_verified_restore_when_catalog_metadata_is_lost(production, loss):
    portal, _, tokens = production
    browser, headers = login(portal)
    first = browser.post('/api/scan', headers=headers, json={'code':tokens[0]}).json['ticket']
    with portal.connect() as db:
        before = [dict(row) for row in db.execute(f'SELECT * FROM {portal.SCANS} ORDER BY id').fetchall()]
        if loss == 'metadata_table':
            db.execute(f'DROP TABLE {portal.CATALOG_TABLE}')
        else:
            db.execute(f'DELETE FROM {portal.CATALOG_TABLE}')
        if loss == 'all_tickets_and_metadata':
            db.execute(f'DELETE FROM {portal.TICKETS}')
    with pytest.raises(RuntimeError, match='Incomplete or conflicting production registry'):
        portal.init_db()
    with portal.connect() as db:
        after = [dict(row) for row in db.execute(f'SELECT * FROM {portal.SCANS} ORDER BY id').fetchall()]
        assert after == before and after[0]['time'] == first['first_time']
        if loss == 'all_tickets_and_metadata':
            assert db.execute(f'SELECT COUNT(*) AS n FROM {portal.TICKETS}').fetchone()['n'] == 0
        else:
            row = db.execute(f'SELECT * FROM {portal.TICKETS} WHERE serial=?', (first['serial'],)).fetchone()
            assert row['status'] == 'used' and row['first_time'] == first['first_time']
    assert browser.get('/health').status_code == 503


def test_actual_cookie_configuration_cannot_be_downgraded_in_production(module):
    """Evaluate the actual startup config expression without importing a real registry."""
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    syntax = ast.parse(Path(module.__file__).read_text())
    setting = next(keyword.value for node in ast.walk(syntax) if isinstance(node, ast.Call)
                   for keyword in node.keywords if keyword.arg == 'SESSION_COOKIE_SECURE')
    compiled = compile(ast.Expression(body=setting), module.__file__, 'eval')
    for mode, local, expected in [('production','1',True), ('production',None,True),
                                  ('test','1',False), ('test',None,True)]:
        environment = {} if local is None else {'LOCAL_DEVELOPMENT':local}
        assert eval(compiled, {'APP_MODE':mode,'os':SimpleNamespace(environ=environment)}) is expected
