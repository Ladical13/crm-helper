"""Why we lost, and the estimates that were vanishing from the numbers.

Marking an estimate lost used to take one field. The analytics tab could report
a close rate to the decimal and never say what to change: price, timing, a
competitor and an insurance denial are four different companies' problems and
nothing could tell them apart. Separately, `if not sp: continue` dropped every
unassigned estimate from the funnel, revenue, aging, cities and YTD with no
counter anywhere saying how many rows had gone.
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


def _seed(eid, *, rep='luke', total=12000.0, status=None):
    doc = {
        'estimate_id': eid, 'estimate_type': 'retail',
        'share_token': 'tok-' + eid, 'sent_at': '2026-07-01T00:00:00Z',
        'customer': {'name': 'Test', 'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': total, 'unit_cost': 7000.0}],
        }},
    }
    if rep:
        doc['salesperson'] = rep
    if status:
        doc['status'] = status
    A.est_save(doc)
    return doc


def test_a_reason_is_stored(client):
    _seed('l1')
    r = client.patch('/api/estimates/l1/status',
                     json={'status': 'lost', 'lost_reason': 'competitor'})
    assert r.status_code == 200
    doc = A.est_load('l1')
    assert doc['lost_reason'] == 'competitor'
    assert doc['lost_at']


def test_a_note_rides_along(client):
    _seed('l2')
    client.patch('/api/estimates/l2/status',
                 json={'status': 'lost', 'lost_reason': 'price',
                       'lost_note': 'Beat us by $2,400 on a 24sq tear-off'})
    assert 'Beat us by' in A.est_load('l2')['lost_note']


def test_an_unknown_reason_is_refused(client):
    """The picker's options are served by the API so the dropdown and the
    validator cannot drift — anything else is a client that has."""
    _seed('l3')
    r = client.patch('/api/estimates/l3/status',
                     json={'status': 'lost', 'lost_reason': 'mercury retrograde'})
    assert r.status_code == 400


def test_marking_lost_without_a_reason_still_works(client):
    """The reason must never become a reason not to record the outcome."""
    _seed('l4')
    assert client.patch('/api/estimates/l4/status',
                        json={'status': 'lost'}).status_code == 200
    assert A.est_load('l4')['status'] == 'lost'


def test_re_quoting_clears_the_old_reason(client):
    """Plenty get re-quoted. A job that closes in March must not carry "went
    with someone else" into the month it was won."""
    _seed('l5')
    client.patch('/api/estimates/l5/status',
                 json={'status': 'lost', 'lost_reason': 'timing'})
    client.patch('/api/estimates/l5/status', json={'status': 'sent'})
    doc = A.est_load('l5')
    assert 'lost_reason' not in doc and 'lost_at' not in doc


def test_a_note_is_capped(client):
    _seed('l6')
    client.patch('/api/estimates/l6/status',
                 json={'status': 'lost', 'lost_reason': 'other',
                       'lost_note': 'x' * 5000})
    assert len(A.est_load('l6')['lost_note']) == 500


def test_the_options_are_served(client):
    body = client.get('/api/lost-reasons').get_json()
    assert 'price' in body and 'competitor' in body
    assert body == A.LOST_REASONS


def test_analytics_counts_the_reasons(client):
    _seed('l7')
    _seed('l8')
    client.patch('/api/estimates/l7/status',
                 json={'status': 'lost', 'lost_reason': 'price'})
    client.patch('/api/estimates/l8/status',
                 json={'status': 'lost', 'lost_reason': 'price'})
    lr = client.get('/api/analytics').get_json()['lost_reasons']
    assert lr['price']['count'] == 2


def test_estimates_lost_before_the_picker_existed_are_counted_as_unrecorded(client):
    """Dropping them would quietly inflate the share of every reason that IS
    recorded, which is a worse lie than admitting the gap."""
    _seed('l9', status='lost')
    lr = client.get('/api/analytics').get_json()['lost_reasons']
    assert lr['unrecorded']['count'] == 1


def test_declined_is_still_accepted_and_normalized(client):
    """Real estimates on the live volume carry the old spelling forever."""
    _seed('l10')
    r = client.patch('/api/estimates/l10/status',
                     json={'status': 'declined', 'lost_reason': 'scope'})
    assert r.get_json()['status'] == 'lost'
    assert A.est_load('l10')['lost_reason'] == 'scope'


# ── Unassigned estimates ──────────────────────────────────────────────────

def test_unassigned_estimates_are_reported_rather_than_vanishing(client):
    """They are still excluded from the per-rep math, but the count and the
    dollars are now on the record. An invisible unknown is the bug."""
    _seed('u1', rep=None, total=25000.0)
    body = client.get('/api/analytics').get_json()
    assert body['unassigned']['count'] == 1
    assert body['unassigned']['value'] == 25000.0


def test_an_assigned_estimate_is_not_counted_as_unassigned(client):
    _seed('u2', rep='luke')
    assert client.get('/api/analytics').get_json()['unassigned']['count'] == 0


def test_unassigned_estimates_stay_out_of_the_rep_leaderboard(client):
    """A synthetic "(unassigned)" rep would rank in the leaderboard as if it
    were a person."""
    _seed('u3', rep=None)
    body = client.get('/api/analytics').get_json()
    assert '' not in body['by_rep']
    assert '(unassigned)' not in body['by_rep']
