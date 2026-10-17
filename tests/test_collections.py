import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import login
from qr_encoding import print_token


COLLECTION = '/api/collections/alphanumeric-20'


def test_twenty_samples_have_separate_unique_test_mappings(module):
    samples = module.TEST20_SAMPLES
    assert len(samples) == 20
    assert all(re.fullmatch(r'[A-Za-z0-9]{32}', sample['token']) for sample in samples)
    assert len({sample['token'] for sample in samples}) == 19
    assert len(module.TEST20_TICKETS) == 18
    assert samples[7]['token'] == samples[1]['token']
    assert samples[7]['ticket_serial'] == samples[1]['ticket_serial']
    assert samples[6]['ticket_serial'] is None
    assert not {sample['token'] for sample in samples} & {sample[1] for sample in module.SAMPLES}
    with module.connect() as db:
        assert db.execute(f'SELECT COUNT(*) AS n FROM {module.TICKETS}').fetchone()['n'] == 24
        assert db.execute(f'SELECT serial FROM {module.TICKETS} WHERE code_hash=?',
                          (module.hashed(samples[6]['token']),)).fetchone() is None


def test_twenty_sequential_scans_and_collection_metadata(module):
    client, headers = login(module)
    catalog = client.get(COLLECTION + '/samples').json
    assert catalog['collection'] == 'alphanumeric-20' and catalog['ticket_count'] == 18
    assert len(catalog['samples']) == 20
    results = []
    for sample, item in zip(module.TEST20_SAMPLES, catalog['samples']):
        assert item['title'] == sample['title'].removeprefix('VIP ') and item['serial'] == sample['serial']
        assert 'token' not in item and 'code_hash' not in item
        qr = client.get(item['qr'])
        assert qr.status_code == 200 and qr.mimetype == 'image/png' and qr.data.startswith(b'\x89PNG')
        response = client.post(item['scan'], json={'gate':'GATE B'}, headers=headers)
        assert response.status_code == 200
        assert response.json['result'] == sample['sequential_result']
        results.append(response.json)
    assert results[5]['reason'] == 'CANCELLED'
    assert results[6]['reason'] == 'INVALID TICKET'
    assert results[7]['ticket'] == results[1]['ticket']
    summary = client.get('/api/summary').json
    assert summary['stats'] == {'total':24,'checked_in':18,'unused':4,'rejected':4}
    assert summary['catalog'] == {'samples':28,'initially_used':2}
    assert len(summary['logs']) == 20
    assert len(client.get('/api/samples').json['samples']) == 8
    assert client.get('/api/sample/9').status_code == 404
    assert client.post('/api/sample/9/scan', headers=headers, json={}).status_code == 404
    for number in (0, 21):
        assert client.get(f'{COLLECTION}/sample/{number}').status_code == 404
        assert client.post(f'{COLLECTION}/sample/{number}/scan', headers=headers, json={}).status_code == 404


def test_new_collection_authentication_csrf_and_roles(module):
    anonymous = module.app.test_client()
    assert anonymous.get(COLLECTION + '/samples').status_code == 401
    assert anonymous.get(COLLECTION + '/sample/1').status_code == 401
    assert anonymous.post(COLLECTION + '/sample/1/scan', json={}).status_code == 401
    staff, headers = login(module, 'staff')
    assert staff.get(COLLECTION + '/samples').status_code == 200
    assert staff.get(COLLECTION + '/sample/1').status_code == 200
    assert staff.post(COLLECTION + '/sample/1/scan', json={}).status_code == 403
    assert staff.post(COLLECTION + '/sample/1/scan', headers={**headers, 'Origin':'https://other.example'}, json={}).status_code == 403
    assert staff.post(COLLECTION + '/sample/1/scan', headers=headers, json={}).json['result'] == 'ACCESS GRANTED'
    assert staff.post('/api/reset', headers=headers, json={'confirmation':'RESET TEST DATA'}).status_code == 403
    assert staff.get('/api/backup').status_code == 403
    for resource in ('/', '/static/app.js', COLLECTION + '/samples'):
        response = staff.get(resource)
        assert all(sample['token'].encode() not in response.data for sample in module.TEST20_SAMPLES)
    assert anonymous.get('/test_samples_20.json').status_code == 404


@pytest.mark.parametrize('collection', ['original', 'alphanumeric-20'])
def test_printed_and_original_formats_share_one_admission(module, collection):
    original = module.SAMPLES[0][1] if collection == 'original' else module.TEST20_SAMPLES[0]['token']
    printed = print_token(original)
    assert len(printed) == 39 and re.fullmatch(r'[A-Z2-7]{39}', printed)
    client, headers = login(module)
    first = client.post('/api/scan', headers=headers, json={'code':printed,'gate':'GATE B'}).json
    repeated = client.post('/api/scan', headers=headers, json={'code':original,'gate':'CC GATE'}).json
    assert first['result'] == 'ACCESS GRANTED' and repeated['result'] == 'ALREADY SCANNED'
    assert first['ticket'] == repeated['ticket']
    module.init_db()
    restart = client.post('/api/scan', headers=headers, json={'code':printed,'gate':'GATE A'}).json
    assert restart['result'] == 'ALREADY SCANNED' and restart['ticket'] == first['ticket']


def test_concurrent_formats_have_one_winner(module):
    original = module.TEST20_SAMPLES[1]['token']
    printed = print_token(original)
    count = 12
    barrier = threading.Barrier(count)
    def scan(index):
        client, headers = login(module, 'staff' if index % 2 else 'admin')
        barrier.wait(timeout=20)
        response = client.post('/api/scan', headers=headers,
                               json={'code':original if index % 2 else printed,'gate':module.GATES[index % len(module.GATES)]})
        assert response.status_code == 200
        return response.json
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(scan, range(count)))
    winners = [result for result in results if result['result'] == 'ACCESS GRANTED']
    assert len(winners) == 1
    assert sum(result['result'] == 'ALREADY SCANNED' for result in results) == count - 1
    assert all(result['ticket'] == winners[0]['ticket'] for result in results)


def test_adding_collection_preserves_old_checkins_and_history(module):
    client, headers = login(module)
    original = client.post('/api/sample/1/scan', headers=headers, json={'gate':'GATE B'}).json
    with module.connect() as db:
        db.execute(f"DELETE FROM {module.TICKETS} WHERE serial LIKE 'TEST20-%'")
        assert db.execute(f'SELECT COUNT(*) AS n FROM {module.TICKETS}').fetchone()['n'] == 6
    module.init_db()
    module.init_db()
    summary = client.get('/api/summary').json
    assert summary['stats']['total'] == 24
    assert len(summary['logs']) == 1 and summary['logs'][0]['serial'] == original['ticket']['serial']
    repeat = client.post('/api/sample/1/scan', headers=headers, json={'gate':'CC GATE'}).json
    assert repeat['result'] == 'ALREADY SCANNED' and repeat['ticket'] == original['ticket']
    client.post(COLLECTION + '/sample/1/scan', headers=headers, json={})
    assert client.post('/api/reset', headers=headers, json={}).status_code == 400
    assert client.post('/api/reset', headers=headers, json={'confirmation':'RESET TEST DATA'}).status_code == 200
    assert client.get('/api/summary').json['stats'] == {'total':24,'checked_in':2,'unused':20,'rejected':0}


def test_unknown_formatted_tokens_and_hashes_never_grant_entry(module):
    client, headers = login(module)
    for token in ['A' * 32, print_token('A' * 32), module.hashed(module.TEST20_SAMPLES[0]['token']),
                  module.TEST20_SAMPLES[0]['token'].lower()]:
        result = client.post('/api/scan', headers=headers, json={'code':token}).json
        assert result['result'] == 'ACCESS DENIED' and result['reason'] == 'INVALID TICKET'


def test_malformed_test_fixture_fails_closed(module, tmp_path):
    document = {'schema_version':1,'mode':'test','samples':json.loads(json.dumps(module.TEST20_SAMPLES))}
    document['samples'][0]['token'] = 'WRONG_' + 'A' * 26
    source = tmp_path / 'malformed.json'
    source.write_text(json.dumps(document))
    with pytest.raises(RuntimeError, match='Invalid test sample mapping'):
        module.load_test20_samples(source)
