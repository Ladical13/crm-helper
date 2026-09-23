"""Where a signed job stands: awaiting scheduling, scheduled, in production,
complete.

The estimator's knowledge of a job used to end at the signature, so the Job
Board had nowhere to put a job the office had already booked. The stage is set
by hand, through one endpoint, and a whole-estimate save can never move it —
otherwise a tab opened before the office scheduled the job would autosave it
straight back to "awaiting scheduling".
"""
import pytest

import demo_store


@pytest.fixture(autouse=True)
def clean_slate():
    import app as A
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)


def _seed(A, eid, *, signed=True, rep='luke', status=None):
    doc = {
        'estimate_id': eid, 'salesperson': rep, 'estimate_type': 'retail',
        'customer': {'name': 'Stage Test', 'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'trades': {'roofing': {'enabled': True, 'mode': 'simple', 'line_items': [
            {'name': 'Roof', 'quantity': 1, 'unit_price': 20000, 'unit_cost': 12000}]}},
    }
    if signed:
        doc['signature'] = {'signed_at': '2026-09-01T12:00:00', 'name': 'Stage Test'}
    if status:
        doc['status'] = status
    A.est_save(doc)
    return doc


def _rep_client(app, name='rep-stage'):
    from portal import users as pusers
    if not pusers.get(name):
        pusers.create(name, password='test-only-password', role='rep')
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = name
    return c


def _patch(c, eid, stage):
    return c.patch(f'/api/estimates/{eid}/job-stage', json={'job_stage': stage})


def test_the_stages_are_served_in_board_order(client):
    stages = client.get('/api/job-stages').get_json()
    assert [s['key'] for s in stages] == ['', 'scheduled', 'in_production', 'complete']


def test_a_signed_job_moves_and_every_move_is_kept(client, A):
    _seed(A, 's1')
    assert _patch(client, 's1', 'scheduled').status_code == 200
    assert _patch(client, 's1', 'complete').status_code == 200
    # Back again: jobs get rescheduled, so either direction is allowed.
    assert _patch(client, 's1', 'scheduled').status_code == 200
    doc = A.est_load('s1')
    assert doc['job_stage'] == 'scheduled'
    assert [h['stage'] for h in doc['job_stage_history']] == ['scheduled', 'complete', 'scheduled']
    assert all(h['by'] == 'luke' and h['at'] for h in doc['job_stage_history'])


def test_moving_back_to_awaiting_clears_the_stage(client, A):
    _seed(A, 's1')
    _patch(client, 's1', 'scheduled')
    assert _patch(client, 's1', '').status_code == 200
    assert 'job_stage' not in A.est_load('s1')


def test_an_unsigned_estimate_cannot_be_scheduled(client, A):
    _seed(A, 'u1', signed=False)
    assert _patch(client, 'u1', 'scheduled').status_code == 409
    assert 'job_stage' not in A.est_load('u1')


def test_an_accepted_estimate_can_be_scheduled(client, A):
    # The board puts a hand-accepted job on the signed side (estStatusOf), so
    # it has to be movable there too.
    _seed(A, 'a1', signed=False, status='accepted')
    assert _patch(client, 'a1', 'scheduled').status_code == 200


def test_unknown_stages_are_refused(client, A):
    _seed(A, 's1')
    assert _patch(client, 's1', 'invoiced').status_code == 400
    assert client.patch('/api/estimates/s1/job-stage', json={}).status_code == 400


def test_a_rep_moves_only_their_own_jobs(app, A):
    _seed(A, 'mine', rep='rep-stage')
    _seed(A, 'theirs', rep='someone-else')
    c = _rep_client(app)
    assert _patch(c, 'mine', 'scheduled').status_code == 200
    assert _patch(c, 'theirs', 'scheduled').status_code == 403


def test_a_whole_estimate_save_cannot_move_the_stage(client, A):
    doc = _seed(A, 's1')
    _patch(client, 's1', 'in_production')
    # A tab opened before the move, carrying no stage at all...
    stale = dict(doc)
    client.put('/api/estimates/s1', json=stale)
    assert A.est_load('s1')['job_stage'] == 'in_production'
    # ...and one carrying an OLD stage. SERVER_MANAGED_FIELDS alone would
    # have let this one through, because it only restores a missing value.
    stale['job_stage'] = 'scheduled'
    stale['job_stage_history'] = []
    client.put('/api/estimates/s1', json=stale)
    doc = A.est_load('s1')
    assert doc['job_stage'] == 'in_production'
    assert len(doc['job_stage_history']) == 1


def test_the_list_carries_the_stage(client, A):
    _seed(A, 's1')
    _patch(client, 's1', 'scheduled')
    row = next(r for r in client.get('/api/estimates').get_json() if r['estimate_id'] == 's1')
    assert row['job_stage'] == 'scheduled'
    assert row['job_stage_at']


def test_a_stray_stage_on_an_open_estimate_reads_as_nothing(client, A):
    doc = _seed(A, 'u1', signed=False)
    doc['job_stage'] = 'complete'
    A.est_save(doc)
    row = next(r for r in client.get('/api/estimates').get_json() if r['estimate_id'] == 'u1')
    assert row['job_stage'] == ''


def test_demo_may_move_its_own_jobs():
    # Writes the estimate doc and nothing else, which the demo store owns —
    # the same reach as update_estimate_status.
    assert {'get_job_stages', 'update_job_stage'} <= demo_store.ALLOWED_ENDPOINTS
