import hashlib
import json
import threading
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import login


def test_original_eight_payload_mappings_are_unchanged(module):
    encoded = json.dumps(module.SAMPLES, separators=(',', ':')).encode()
    assert hashlib.sha256(encoded).hexdigest() == '77dc0468b3f2c6c33af8407a1870edab3783aab3e3baee7501baf67a5beb14bb'


def test_production_mode_cannot_be_activated(module):
    environment = dict(os.environ, APP_MODE='production')
    result = subprocess.run([sys.executable, '-c', 'import app'], env=environment, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'supports TEST MODE only' in result.stderr


def test_known_legacy_entries_migrate_without_editing_the_original_table(module):
    with module.connect() as db:
        db.execute(f'DELETE FROM {module.TICKETS}')
        db.execute('DROP TABLE onrf2_test_meta')
        db.execute('CREATE TABLE tickets(serial TEXT,code_hash TEXT,kind TEXT,status TEXT,first_time TEXT,first_gate TEXT,first_user TEXT)')
        db.execute('CREATE TABLE scans(id INTEGER PRIMARY KEY,time TEXT,serial TEXT,outcome TEXT,gate TEXT,operator TEXT)')
        sample = module.SAMPLES[0]
        db.execute("INSERT INTO tickets VALUES (?,?,?,'used','2026-10-14T10:00:00+00:00','GATE B','original-staff')", (sample[0],module.hashed(sample[1]),sample[2]))
        db.execute("INSERT INTO tickets VALUES ('NON-TEST-RECORD','unrelated-hash','PRIVATE','unused',NULL,NULL,NULL)")
        db.execute("INSERT INTO scans VALUES (1,'2026-10-14T10:00:00+00:00',?,'ACCESS GRANTED','GATE B','original-staff')", (sample[0],))
    module.init_db()
    module.init_db()
    with module.connect() as db:
        row = db.execute(f'SELECT * FROM {module.TICKETS} WHERE serial=?', (sample[0],)).fetchone()
        assert row['status'] == 'used' and row['first_user'] == 'original-staff'
        assert db.execute('SELECT COUNT(*) AS n FROM tickets').fetchone()['n'] == 2
        assert db.execute(f'SELECT COUNT(*) AS n FROM {module.SCANS}').fetchone()['n'] == 1
        assert db.execute(f'SELECT COUNT(*) AS n FROM {module.TICKETS}').fetchone()['n'] == 6


def test_reset_never_touches_legacy_or_production_tables(module):
    with module.connect() as db:
        db.execute('CREATE TABLE production_tickets (serial TEXT PRIMARY KEY, status TEXT)')
        db.execute("INSERT INTO production_tickets VALUES ('PRODUCTION-UNTOUCHED', 'unused')")
    client, headers = login(module)
    client.post('/api/reset', headers=headers, json={'confirmation':'RESET TEST DATA'})
    with module.connect() as db:
        row = db.execute('SELECT * FROM production_tickets').fetchone()
        assert dict(row) == {'serial':'PRODUCTION-UNTOUCHED', 'status':'unused'}


def test_login_and_role_boundaries(module):
    anonymous = module.app.test_client()
    for path in ['/api/me', '/api/samples', '/api/sample/1', '/api/summary', '/api/backup']:
        assert anonymous.get(path).status_code == 401
    assert anonymous.post('/api/scan', json={'code':'unknown'}).status_code == 401
    assert anonymous.post('/api/login', json={'username':'intruder', 'password':'automated-test-staff-password'}).status_code == 401
    staff, headers = login(module, 'staff')
    assert staff.get('/api/samples').status_code == 200
    assert staff.get('/api/summary').status_code == 403
    assert staff.get('/api/backup').status_code == 403
    assert staff.post('/api/reset', headers=headers, json={'confirmation':'RESET TEST DATA'}).status_code == 403
    assert staff.post('/api/scan', json={'code':module.SAMPLES[0][1]}).status_code == 403
    assert staff.post('/api/logout', headers=headers, json={}).status_code == 200
    assert staff.get('/api/me').status_code == 401


def test_cross_origin_and_login_throttle(module):
    client = module.app.test_client()
    assert client.post('/api/login', headers={'Origin':'https://other.example'}, json={}).status_code == 403
    for _ in range(12):
        assert client.post('/api/login', json={'username':'admin','password':'wrong'}).status_code == 401
    assert client.post('/api/login', json={'username':'admin','password':'wrong'}).status_code == 429


def test_all_eight_samples_and_stats(module):
    client, headers = login(module)
    assert len(client.get('/api/samples').json['samples']) == 8
    expected = ['ACCESS GRANTED'] * 4 + ['ALREADY SCANNED','ACCESS DENIED','ACCESS DENIED','ALREADY SCANNED']
    results = []
    for number, outcome in enumerate(expected, 1):
        response = client.post(f'/api/sample/{number}/scan', json={'gate':'GATE A'}, headers=headers)
        assert response.status_code == 200
        assert response.json['result'] == outcome
        results.append(response.json)
    assert results[5]['reason'] == 'CANCELLED'
    assert results[6]['reason'] == 'INVALID TICKET'
    assert results[7]['ticket'] == results[1]['ticket']
    assert results[4]['ticket']['first_time'] == '2026-10-15T17:00:00+00:00'
    summary = client.get('/api/summary').json
    assert summary['stats'] == {'total':6,'checked_in':5,'unused':0,'rejected':4}
    assert len(summary['logs']) == 8
    assert all('kind' in log and 'operator' in log for log in summary['logs'])


def test_simultaneous_scans_have_exactly_one_winner(module):
    count = 12
    barrier = threading.Barrier(count)
    def scan(index):
        client, headers = login(module, 'staff' if index % 2 else 'admin')
        barrier.wait(timeout=20)
        response = client.post('/api/scan', headers=headers,
                               json={'code':module.SAMPLES[1][1],'gate':module.GATES[index % 4]})
        assert response.status_code == 200
        return response.json
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(scan, range(count)))
    winners = [result for result in results if result['result'] == 'ACCESS GRANTED']
    assert len(winners) == 1
    assert sum(result['result'] == 'ALREADY SCANNED' for result in results) == count - 1
    assert all(result['ticket'] == winners[0]['ticket'] for result in results)


def test_scan_and_audit_commit_together(module, monkeypatch):
    client, headers = login(module)
    execute = module.Connection.execute
    def failing(self, sql, params=()):
        if sql.startswith('INSERT INTO ' + module.SCANS):
            raise RuntimeError('simulated storage failure')
        return execute(self, sql, params)
    with monkeypatch.context() as scope:
        scope.setattr(module.Connection, 'execute', failing)
        assert client.post('/api/sample/1/scan', headers=headers, json={}).status_code == 503
    response = client.post('/api/sample/1/scan', headers=headers, json={})
    assert response.json['result'] == 'ACCESS GRANTED'


def test_reset_requires_confirmation_and_backup_excludes_raw_tokens(module):
    client, headers = login(module)
    client.post('/api/sample/1/scan', headers=headers, json={})
    assert client.post('/api/reset', headers=headers, json={}).status_code == 400
    data = client.get('/api/backup')
    assert data.status_code == 200
    backup = json.loads(data.data)
    assert backup['mode'] == 'test'
    assert len(backup['tickets']) == 6 and len(backup['scans']) == 1
    assert all(sample[1].encode() not in data.data for sample in module.SAMPLES)
    assert client.post('/api/reset', headers=headers, json={'confirmation':'RESET TEST DATA'}).status_code == 200
    assert client.get('/api/summary').json['stats'] == {'total':6,'checked_in':1,'unused':4,'rejected':0}


def test_restart_does_not_reset_entries(module):
    client, headers = login(module)
    first = client.post('/api/sample/1/scan', headers=headers, json={}).json
    module.init_db()
    repeat = client.post('/api/sample/1/scan', headers=headers, json={'gate':'GATE B'}).json
    assert repeat['result'] == 'ALREADY SCANNED'
    assert repeat['ticket'] == first['ticket']


def test_input_validation_and_short_invalid_code(module):
    client, headers = login(module)
    invalid = client.post('/api/scan', headers=headers, json={'code':'x'})
    assert invalid.json['result'] == 'ACCESS DENIED'
    for values in [{'code':''}, {'code':'x'*257}, {'code':[]}, {'code':'abcdefg','gate':'UNLISTED'}]:
        assert client.post('/api/scan', headers=headers, json=values).status_code == 400
    assert client.post('/api/sample/9/scan', headers=headers, json={}).status_code == 404


def test_ui_and_qr_protection(module):
    client, _ = login(module)
    page = client.get('/')
    assert b'lang="en"' in page.data
    assert b'Content-Security-Policy' not in page.data
    assert "frame-ancestors 'none'" in page.headers['Content-Security-Policy']
    assert page.headers['Permissions-Policy'] == 'camera=(self), microphone=()'
    assert all(sample[1].encode() not in page.data for sample in module.SAMPLES)
    script = client.get('/static/app.js')
    assert all(sample[1].encode() not in script.data for sample in module.SAMPLES)
    for number in range(1, 9):
        qr = client.get(f'/api/sample/{number}')
        assert qr.status_code == 200 and qr.mimetype == 'image/png'
        assert qr.data.startswith(b'\x89PNG')
