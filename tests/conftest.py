import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ['SESSION_SECRET'] = 'automated-test-session-secret-not-for-deployment'
os.environ['ADMIN_PASSWORD'] = 'automated-test-admin-password'
os.environ['STAFF_PASSWORD'] = 'automated-test-staff-password'
os.environ['LOCAL_DEVELOPMENT'] = '1'
os.environ['DATABASE_URL'] = ''
os.environ['DB_PATH'] = str(Path(tempfile.mkdtemp()) / 'bootstrap.sqlite')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as portal


@pytest.fixture
def module(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, 'DB', tmp_path / 'test.sqlite')
    portal.app.config['TESTING'] = True
    portal.init_db()
    return portal


def login(module, role='admin'):
    client = module.app.test_client()
    response = client.post('/api/login', json={
        'username': role,
        'password': os.environ['ADMIN_PASSWORD' if role == 'admin' else 'STAFF_PASSWORD'],
    })
    assert response.status_code == 200
    return client, {'X-CSRF': response.json['csrf']}
