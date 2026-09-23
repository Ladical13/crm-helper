"""Which package the customer actually reached for.

selectCvTier — the handler that fires every time a homeowner taps a package
card — was pure DOM: it swapped a highlight and a line-item block and reported
nothing. "Opened four times and kept coming back to Best" and "opened once,
never touched a card" are two completely different sales calls, and the page
that knew the difference was throwing it away.
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


def _seed(eid='t1', **extra):
    doc = {
        'estimate_id': eid, 'salesperson': 'luke', 'estimate_type': 'retail',
        'share_token': 'tok-' + eid,
        'customer': {'name': 'Jon Smith', 'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': 20000.0, 'unit_cost': 12000.0}],
        }},
    }
    doc.update(extra)
    A.est_save(doc)
    return doc


def _tap(anon, tier, trade='roofing', eid='t1'):
    return anon.post(f'/sign/tok-{eid}/tier-interest',
                     json={'trade': trade, 'tier': tier})


def test_a_tap_is_recorded(anon):
    _seed()
    assert _tap(anon, 'best').status_code == 204
    hist = A.est_load('t1')['tier_interest']
    assert hist[0]['trade'] == 'roofing' and hist[0]['tier'] == 'best'


def test_the_same_card_twice_running_is_one_thought(anon):
    _seed()
    _tap(anon, 'best')
    _tap(anon, 'best')
    assert len(A.est_load('t1')['tier_interest']) == 1


def test_going_back_and_forth_is_recorded(anon):
    """That is the signal — it is what deliberating looks like."""
    _seed()
    for t in ('best', 'better', 'best'):
        _tap(anon, t)
    assert len(A.est_load('t1')['tier_interest']) == 3


def test_a_reps_own_preview_is_not_a_buying_signal(client):
    """Same reason view tracking ignores a logged-in team member."""
    _seed()
    client.post('/sign/tok-t1/tier-interest',
                json={'trade': 'roofing', 'tier': 'best'})
    assert 'tier_interest' not in A.est_load('t1')


def test_a_bad_tier_is_ignored(anon):
    _seed()
    _tap(anon, 'platinum')
    assert 'tier_interest' not in A.est_load('t1')


def test_a_signed_estimate_records_nothing(anon):
    _seed(signature={'name': 'Jon', 'signed_at': '2026-01-01T00:00:00Z'})
    assert _tap(anon, 'best').status_code == 204
    assert 'tier_interest' not in A.est_load('t1')


def test_an_unknown_token_is_quiet(anon):
    """A public endpoint must not become a probe for which tokens exist."""
    assert anon.post('/sign/nope/tier-interest',
                     json={'trade': 'roofing', 'tier': 'best'}).status_code == 204


def test_the_history_is_capped(anon):
    """Unbounded growth on a public endpoint is how a doc gets to 40MB."""
    _seed()
    tiers = ('good', 'better', 'best')
    for i in range(A.TIER_INTEREST_CAP + 25):
        _tap(anon, tiers[i % 3])
    assert len(A.est_load('t1')['tier_interest']) == A.TIER_INTEREST_CAP


def test_the_summary_names_what_they_landed_on(anon):
    _seed()
    for t in ('good', 'best', 'better', 'best'):
        _tap(anon, t)
    s = A._tier_interest_summary(A.est_load('t1'))
    assert 'Roofing: landed on Best' in s and 'taps' in s


def test_no_history_is_an_empty_summary():
    assert A._tier_interest_summary({}) == ''


def test_the_reminder_email_carries_it(monkeypatch):
    """It has to reach the rep somewhere they already look."""
    sent = []
    monkeypatch.setattr(A, '_send_email',
                        lambda s, h, t, **k: sent.append(h) or True)
    est = _seed(view_count=4, last_viewed_at='2026-09-01T00:00:00Z',
                tier_interest=[{'trade': 'roofing', 'tier': 'best',
                                'at': '2026-09-01T00:00:00Z'}])
    A.send_followup_reminder(est, 7)
    assert 'Interested in' in sent[0]
    assert 'landed on Best' in sent[0]


def test_the_beacon_is_wired_into_the_package_cards(anon):
    """selectCvTier lives in an f-string inside an f-string. If that URL
    interpolation ever breaks, the page still renders and the beacon silently
    posts to a literal placeholder — so assert on the real path."""
    A.est_save({
        'estimate_id': 'gbb1', 'salesperson': 'luke', 'estimate_type': 'retail',
        'share_token': 'tok-gbb1',
        'customer': {'name': 'Jon Smith', 'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'gbb',
            'line_items': [{'name': 'Roof', 'quantity': 1, 'tiers': {
                'good':   {'material_unit_cost': 100.0, 'labor_unit_cost': 50.0},
                'better': {'material_unit_cost': 150.0, 'labor_unit_cost': 60.0},
                'best':   {'material_unit_cost': 200.0, 'labor_unit_cost': 70.0},
            }}],
        }},
    })
    html = anon.get('/sign/tok-gbb1').get_data(as_text=True)
    assert 'selectCvTier' in html, 'the G/B/B page must render the tier picker'
    assert '/sign/tok-gbb1/tier-interest' in html
    assert 'TIER_BEACON_URL' not in html
    assert 'navigator.sendBeacon' in html
