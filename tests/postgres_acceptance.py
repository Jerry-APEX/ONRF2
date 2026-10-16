"""Optional isolated PostgreSQL check: pip install pgserver, then run this file.

Uses a temporary local Postgres 16 server. Never uses a deployment database.
"""
import os
import secrets
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pgserver

pg = pgserver.get_server(Path(tempfile.mkdtemp(prefix='onrf2-pg-test-')), cleanup_mode='stop')
os.environ.update(DATABASE_URL=pg.get_uri(), APP_MODE='test', LOCAL_DEVELOPMENT='1',
                  SESSION_SECRET=secrets.token_urlsafe(48),
                  ADMIN_PASSWORD=secrets.token_urlsafe(24), STAFF_PASSWORD=secrets.token_urlsafe(24))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as portal


def client(role='admin'):
    browser = portal.app.test_client()
    response = browser.post('/api/login', json={
        'username':role, 'password':os.environ['ADMIN_PASSWORD' if role == 'admin' else 'STAFF_PASSWORD']})
    assert response.status_code == 200
    return browser, {'X-CSRF':response.json['csrf']}


try:
    count = 16
    barrier = threading.Barrier(count)
    def scan(number):
        browser, headers = client('admin' if number % 2 else 'staff')
        barrier.wait(timeout=20)
        response = browser.post('/api/sample/2/scan', json={'gate':portal.GATES[number % 4]}, headers=headers)
        assert response.status_code == 200
        return response.json
    with ThreadPoolExecutor(max_workers=count) as pool:
        responses = list(pool.map(scan, range(count)))
    granted = [row for row in responses if row['result'] == 'ACCESS GRANTED']
    assert len(granted) == 1
    assert all(row['ticket'] == granted[0]['ticket'] for row in responses)
    browser, headers = client()
    portal.init_db()
    repeated = browser.post('/api/sample/8/scan', headers=headers, json={}).json
    assert repeated['result'] == 'ALREADY SCANNED' and repeated['ticket'] == granted[0]['ticket']
    backup = browser.get('/api/backup').json
    assert len(backup['tickets']) == 6 and len(backup['scans']) == 17
    assert browser.get('/api/summary').json['stats']['rejected'] == 16
    assert browser.post('/api/reset', headers=headers, json={'confirmation':'RESET TEST DATA'}).status_code == 200
    outcomes = []
    for number in range(1, 9):
        response = browser.post(f'/api/sample/{number}/scan', headers=headers, json={})
        assert response.status_code == 200
        outcomes.append(response.json['result'])
    assert outcomes == ['ACCESS GRANTED'] * 4 + ['ALREADY SCANNED','ACCESS DENIED','ACCESS DENIED','ALREADY SCANNED']
    print('PostgreSQL 16: 16 simultaneous requests, exactly one grant; first-entry metadata preserved.')
    print('PostgreSQL 16: restart, backup snapshot, admin summary, reset and all eight samples passed.')
finally:
    pg.cleanup()
