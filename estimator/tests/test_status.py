"""Estimate outcome: Lost, and what it is supposed to remove you from.

`declined` was the old name and did almost nothing. The dashboard decided which
bucket an estimate belonged to from `signed / first_viewed_at / sent` and never
looked at the status at all, so a declined estimate stayed in Outstanding,
carried on counting toward the outstanding total, and kept appearing in the
follow-up banner. The only thing it actually suppressed was the reminder email.

These pin the behaviour that makes marking one Lost worth doing, and pin the
old spelling working forever — real estimates on the live volume still carry it
and are deliberately never rewritten.
"""
import pytest

import app as A


@pytest.fixture(autouse=True)
def clean_slate():
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)


def _seed(eid, *, status=None, sent=True, viewed=True, signed=False, total=12000.0):
    doc = {
        'estimate_id': eid,
        'salesperson': 'luke',
        'estimate_type': 'retail',
        'customer': {'name': 'Test', 'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': total, 'unit_cost': 7000.0}],
        }},
    }
    if status:
        doc['status'] = status
    if sent:
        doc['share_token'] = 'tok-' + eid
        doc['sent_at'] = '2026-07-01T00:00:00Z'
    if viewed:
        doc['first_viewed_at'] = '2026-07-02T00:00:00Z'
    if signed:
        doc['signature'] = {'signed_at': '2026-07-03T00:00:00Z', 'name': 'Test'}
    A.est_save(doc)
    return doc


# ── Naming ──────────────────────────────────────────────────────────────

def test_lost_is_accepted_and_stored(client):
    _seed('e-lost')
    r = client.patch('/api/estimates/e-lost/status', json={'status': 'lost'})
    assert r.status_code == 200
    assert r.get_json()['status'] == 'lost'
    assert A.est_load('e-lost')['status'] == 'lost'


def test_the_old_declined_spelling_is_stored_as_lost(client):
    """Accepted on the way in so nothing that still sends it breaks, but only
    one spelling is ever written from here on."""
    _seed('e-dec')
    r = client.patch('/api/estimates/e-dec/status', json={'status': 'declined'})
    assert r.status_code == 200
    assert r.get_json()['status'] == 'lost'
    assert A.est_load('e-dec')['status'] == 'lost'


def test_an_estimate_stored_as_declined_reads_back_as_lost(client):
    """The records already on the volume are never rewritten, so the read path
    is what has to normalize them."""
    _seed('e-old', status='declined')
    row = next(e for e in client.get('/api/estimates').get_json()
               if e['estimate_id'] == 'e-old')
    assert row['status'] == 'lost'


def test_a_signed_estimate_cannot_be_marked_lost(client):
    """A signature is a fact about what the customer did, not a status a rep
    can retract."""
    _seed('e-signed', signed=True)
    r = client.patch('/api/estimates/e-signed/status', json={'status': 'lost'})
    assert r.status_code == 400
    assert A.est_load('e-signed').get('status') != 'lost'


# ── What Lost actually removes you from ─────────────────────────────────

def test_a_lost_estimate_leaves_the_open_pipeline(client):
    """It used to keep aging in the pipeline report forever."""
    _seed('e-open')
    _seed('e-gone', status='lost')
    aging = client.get('/api/analytics').get_json()['pipeline_aging']
    counted = sum(b.get('count', 0) for b in aging.values())
    assert counted == 1


def test_lost_estimates_are_counted_under_their_own_name(client):
    _seed('e-a', status='lost')
    _seed('e-b', status='declined')     # the old spelling counts the same
    funnel = client.get('/api/analytics').get_json()['funnel']
    assert funnel['lost'] == 2
    assert 'declined' not in funnel


def test_a_lost_estimate_is_skipped_by_the_reminder_sweep(client):
    """The one thing declined already did — it must survive the rename."""
    _seed('e-quiet', status='lost')
    assert A._is_lost(A.est_load('e-quiet'))
    assert A._is_lost({'status': 'declined'})
    assert not A._is_lost({'status': 'sent'})
