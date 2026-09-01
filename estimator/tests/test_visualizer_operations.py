"""Paid fal usage and read-only visual asset storage reporting."""

import base64
import io
import json
import os
import time
import uuid
import zipfile

import pytest
from PIL import Image

from conftest import TEST_DATA_DIR


def _image_uri():
    buf = io.BytesIO()
    Image.new('RGB', (3, 2), 'white').save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture(autouse=True)
def empty_usage_ledger(A):
    if not A.DATABASE_URL:
        conn = A._usage_sqlite_conn()
        try:
            conn.execute('DELETE FROM visualizer_usage_events')
            conn.commit()
        finally:
            conn.close()
    yield


def _configured_detector(monkeypatch):
    from estimator import exterior_detection as detection
    monkeypatch.setenv('EXTERIOR_AUTO_DETECT', '1')
    monkeypatch.setenv('FAL_KEY', 'secret-test-key')
    monkeypatch.setattr(detection, 'configured', lambda: True)
    return detection


def test_detection_records_one_idempotent_costed_lifecycle(client, A, monkeypatch):
    detection = _configured_detector(monkeypatch)
    monkeypatch.setattr(A, 'EXTERIOR_FAL_COST_PER_REQUEST', 0.012345)
    monkeypatch.setattr(detection, 'submit', lambda role, image: {
        'request_id': 'provider-request-1', 'width': 3, 'height': 2,
        'status_url': 'https://queue.fal.run/fal-ai/sam-3/requests/provider-request-1/status',
        'response_url': 'https://queue.fal.run/fal-ai/sam-3/requests/provider-request-1',
    })
    monkeypatch.setattr(detection, 'poll', lambda job: {
        'status': 'complete', 'count': 1, 'mask': _image_uri()})
    eid = client.post('/api/estimates', json={'salesperson': 'luke'}).get_json()['estimate_id']
    endpoint = f'/api/estimates/{eid}/visualizer/detection'
    try:
        submitted = client.post(endpoint, json={
            'role': 'roof', 'photo_key': 'front-photo', 'image': _image_uri()})
        assert submitted.status_code == 202
        ticket = submitted.get_json()['ticket']
        # Repeat polling changes lifecycle state but never creates another
        # request or another costed unit.
        assert client.get(endpoint, query_string={'ticket': ticket}).status_code == 200
        assert client.get(endpoint, query_string={'ticket': ticket}).status_code == 200

        rows = [row for row in A._usage_rows() if row['estimate_id'] == eid]
        assert len(rows) == 1
        row = rows[0]
        assert row['status'] == 'completed'
        assert row['units'] == 1
        assert row['unit_cost_microusd'] == 12345
        assert row['estimated_cost_microusd'] == 12345
        assert row['input_width'] == 3 and row['input_height'] == 2
        assert row['provider_request_id'] == 'provider-request-1'
        assert 'image' not in row and 'mask' not in row and 'key' not in row

        report = client.get('/api/visualizer/operations?days=30').get_json()
        assert report['usage']['submitted_requests'] == 1
        assert report['usage']['estimated_cost_microusd'] == 12345
        assert report['usage']['estimated_cost_usd'] == 0.012345
        serialized = json.dumps(report)
        assert 'secret-test-key' not in serialized
        assert 'provider-request-1' not in serialized
        assert _image_uri() not in serialized
        assert report['cleanup_supported'] is False
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_failed_submission_is_a_single_unknown_billing_attempt(client, A, monkeypatch):
    detection = _configured_detector(monkeypatch)

    def fail(_role, _image):
        raise detection.DetectionError('Provider unavailable.')

    monkeypatch.setattr(detection, 'submit', fail)
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        response = client.post(f'/api/estimates/{eid}/visualizer/detection', json={
            'role': 'siding', 'photo_key': 'photo-failure', 'image': _image_uri()})
        assert response.status_code == 502
        rows = [row for row in A._usage_rows() if row['estimate_id'] == eid]
        assert len(rows) == 1
        assert rows[0]['status'] == 'failed'
        assert rows[0]['billing_state'] == 'unknown'
        assert rows[0]['units'] == 0
        assert rows[0]['estimated_cost_microusd'] is None
        report = client.get('/api/visualizer/operations').get_json()['usage']
        assert report['billing_unknown_attempts'] == 1
        assert report['submitted_requests'] == 0
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_operations_route_is_manager_only(client, A, monkeypatch):
    monkeypatch.setattr(A, '_is_manager_up', lambda *args, **kwargs: False)
    assert client.get('/api/visualizer/operations').status_code == 403


def test_storage_report_classifies_references_and_only_old_orphans(A):
    eid = 'ops-' + uuid.uuid4().hex
    catalog_dir = os.path.join(TEST_DATA_DIR, 'uploads', '_catalog')
    estimate_dir = os.path.join(TEST_DATA_DIR, 'uploads', eid)
    os.makedirs(catalog_dir, exist_ok=True)
    os.makedirs(estimate_dir, exist_ok=True)
    refs = {
        'base': f'{eid}/vb_live.jpg',
        'render_orphan': f'{eid}/vr_old.jpg',
        'recent': f'{eid}/vm_recent.png',
        'texture_live': '_catalog/et_' + uuid.uuid4().hex + '.png',
        'texture_orphan': '_catalog/et_' + uuid.uuid4().hex + '.png',
        'cutout_live': '_catalog/ep_' + uuid.uuid4().hex + '.png',
        'cutout_orphan': '_catalog/ep_' + uuid.uuid4().hex + '.png',
        'job_cutout_orphan': f'{eid}/vpl_' + uuid.uuid4().hex + '.png',
    }
    paths = {}
    try:
        for key, ref in refs.items():
            path = os.path.join(TEST_DATA_DIR, 'uploads', *ref.split('/'))
            with open(path, 'wb') as handle:
                handle.write(key.encode())
            paths[key] = path
        old = time.time() - 40 * 86400
        for key in ('base', 'render_orphan', 'texture_live', 'texture_orphan',
                    'cutout_live', 'cutout_orphan', 'job_cutout_orphan'):
            os.utime(paths[key], (old, old))

        estimate = {
            'estimate_id': eid,
            'visualizer': {'elevations': {'front': {
                'base_image': refs['base'], 'masks': {}, 'tier_renders': {}}}},
        }
        report = A._visualizer_storage_report(
            [estimate], [{'texture_ref': refs['texture_live'],
                          'placement_image_ref': refs['cutout_live']}], 30)
        candidates = {item['ref']: item for item in
                      report['cleanup_candidates']['items']}
        assert refs['base'] not in candidates
        assert refs['texture_live'] not in candidates
        assert refs['cutout_live'] not in candidates
        assert refs['recent'] not in candidates
        assert candidates[refs['render_orphan']]['kind'] == 'visualizer_render'
        assert candidates[refs['render_orphan']]['confidence'] == 'high'
        assert candidates[refs['texture_orphan']]['kind'] == 'catalog_texture'
        assert candidates[refs['cutout_orphan']]['kind'] == 'catalog_product_cutout'
        assert candidates[refs['job_cutout_orphan']]['kind'] == 'exact_product_cutout'
        assert candidates[refs['job_cutout_orphan']]['confidence'] == 'high'
        assert report['referenced_files'] >= 3
        assert report['unreferenced_files'] >= 5
    finally:
        for path in paths.values():
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(estimate_dir)
        except OSError:
            pass


def test_backup_contains_portable_usage_export(A):
    event_id = A._usage_start_event(
        'backup-estimate', 'luke', 'roof', 'front', 'photo-backup')
    A._usage_update_event(
        event_id, status='submitted', billing_state='estimated', units=1,
        unit_cost_microusd=5000, estimated_cost_microusd=5000)
    blob = A._build_backup_zip(include_uploads=False)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert 'operations/visualizer_usage_events.json' in archive.namelist()
        rows = json.loads(archive.read(
            'operations/visualizer_usage_events.json'))
    assert any(row['event_id'] == event_id for row in rows)


def test_operations_dashboard_frontend_is_read_only_and_complete():
    root = os.path.join(os.path.dirname(__file__), '..', 'static')
    with open(os.path.join(root, 'app.js'), encoding='utf-8') as handle:
        source = handle.read()
    with open(os.path.join(root, 'index.html'), encoding='utf-8') as handle:
        index = handle.read()
    for label in ('Usage by day', 'Usage by rep', 'Usage by project',
                  'Usage by surface', 'Storage by type',
                  'fal-accepted detection submissions', 'Billing needs review'):
        assert label in source
    assert 'visualizer-operations-modal' in index
    assert 'Nothing is deleted here.' in source
    assert 'deleteVisualizer' not in source
