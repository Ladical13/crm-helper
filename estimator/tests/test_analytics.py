"""Monthly trends and sales goals.

The analytics tab is how the team decides whether a month was made, so the
month math has to be exact: goals fall back the right way, the series has no
holes, and the current month is judged on pace rather than on a raw total that
is only part-way through.
"""
import json
import os
from datetime import datetime

import pytest

from conftest import TEST_DATA_DIR


GOALS_FILE = os.path.join(TEST_DATA_DIR, 'sales_goals.json')


@pytest.fixture(autouse=True)
def clean_slate():
    """Analytics reads every estimate on disk, so each test owns the store."""
    import app as A
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    if os.path.exists(GOALS_FILE):
        os.remove(GOALS_FILE)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    if os.path.exists(GOALS_FILE):
        os.remove(GOALS_FILE)


def _seed(A, eid, *, signed_at=None, sent_at=None, total=10000.0, rep='luke',
          est_type='retail', cost=6000.0):
    """One estimate priced in 'simple' mode so the total is exactly `total`."""
    doc = {
        'estimate_id': eid,
        'salesperson': rep,
        'estimate_type': est_type,
        'customer': {'name': 'Test', 'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': total, 'unit_cost': cost}],
        }},
    }
    if sent_at:
        doc['share_token'] = 'tok-' + eid
        doc['sent_at'] = sent_at
    if signed_at:
        doc['signature'] = {'signed_at': signed_at, 'name': 'Test'}
    A.est_save(doc)
    return doc


def _month(n=0):
    """'YYYY-MM', n months from now — tests must not break when the year rolls."""
    now = datetime.utcnow()
    i = now.year * 12 + now.month - 1 + n
    return '%04d-%02d' % (i // 12, i % 12 + 1)


def _row(payload, month):
    return next((r for r in payload['monthly'] if r['month'] == month), None)


# ── goals API ──────────────────────────────────────────────────────────

def test_goals_default_when_never_set(client):
    g = client.get('/api/goals').get_json()
    assert g['company']['default'] == {'revenue': 0, 'jobs': 0}
    assert g['reps'] == {}


def test_goals_round_trip_and_normalize(client):
    r = client.put('/api/goals', json={
        'company': {'default': {'revenue': '250000', 'jobs': '10'},
                    'months': {_month(): {'revenue': 300000}}},
        'reps': {'  LUKE  ': {'default': {'revenue': 80000}}},
    })
    assert r.status_code == 200
    g = client.get('/api/goals').get_json()
    assert g['company']['default'] == {'revenue': 250000.0, 'jobs': 10}
    assert g['company']['months'][_month()]['revenue'] == 300000.0
    # Rep keys are normalized so "Luke" and "luke" are one person, not two.
    assert 'luke' in g['reps']
    assert g['reps']['luke']['default']['revenue'] == 80000.0


def test_goals_reject_junk_months_and_negatives(client):
    client.put('/api/goals', json={
        'company': {'default': {'revenue': -5000, 'jobs': 'abc'},
                    'months': {'2026-13': {'revenue': 1}, 'nonsense': {'revenue': 1},
                               '2026-05': {'revenue': 90000}}},
    })
    g = client.get('/api/goals').get_json()
    assert g['company']['default'] == {'revenue': 0, 'jobs': 0}
    assert list(g['company']['months']) == ['2026-05']


def test_goals_put_is_manager_only(app):
    from portal import users as pusers
    if not pusers.get('rep-goals'):
        pusers.create('rep-goals', password='test-only-password', role='rep')
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = 'rep-goals'
    assert c.put('/api/goals', json={'company': {'default': {'revenue': 1}}}).status_code == 403
    # Reps still READ goals — they see their own target on the analytics tab.
    assert c.get('/api/goals').status_code == 200


# ── monthly series ─────────────────────────────────────────────────────

def test_month_is_measured_against_its_own_goal(client, A):
    _seed(A, 'a1', signed_at=_month() + '-05T12:00:00', total=120000.0)
    client.put('/api/goals', json={'company': {
        'default': {'revenue': 200000}, 'months': {_month(): {'revenue': 100000}}}})
    row = _row(client.get('/api/analytics').get_json(), _month())
    # The month override wins over the company default: 120k of 100k, not of 200k.
    assert row['goal'] == 100000.0
    assert row['revenue'] == 120000.0
    assert row['pct_to_goal'] == 120


def test_month_without_override_uses_the_default(client, A):
    _seed(A, 'a1', signed_at=_month() + '-05T12:00:00', total=50000.0)
    client.put('/api/goals', json={'company': {'default': {'revenue': 100000}}})
    row = _row(client.get('/api/analytics').get_json(), _month())
    assert row['goal'] == 100000.0
    assert row['pct_to_goal'] == 50


def test_no_goal_reads_as_none_not_zero_percent(client, A):
    """A month with no goal has no attainment. 0% would read as a failed month."""
    _seed(A, 'a1', signed_at=_month() + '-05T12:00:00', total=50000.0)
    row = _row(client.get('/api/analytics').get_json(), _month())
    assert row['goal'] == 0
    assert row['pct_to_goal'] is None


def test_series_fills_gaps_between_months(client, A):
    """A month with zero signed work is a data point, not a hole in the chart."""
    _seed(A, 'a1', signed_at=_month(-3) + '-10T12:00:00', total=40000.0)
    _seed(A, 'a2', signed_at=_month() + '-10T12:00:00', total=60000.0)
    months = [r['month'] for r in client.get('/api/analytics').get_json()['monthly']]
    assert months == [_month(-3), _month(-2), _month(-1), _month()]
    assert _row(client.get('/api/analytics').get_json(), _month(-2))['revenue'] == 0.0


def test_series_always_reaches_the_current_month(client, A):
    """Nothing signed this month still has to show up — that's the month you're
    trying to make."""
    _seed(A, 'a1', signed_at=_month(-2) + '-10T12:00:00', total=40000.0)
    payload = client.get('/api/analytics').get_json()
    assert payload['monthly'][-1]['month'] == _month()
    assert payload['current_month']['month'] == _month()


def test_series_capped_at_24_months(client, A):
    _seed(A, 'old', signed_at=_month(-40) + '-10T12:00:00', total=1000.0)
    _seed(A, 'new', signed_at=_month() + '-10T12:00:00', total=1000.0)
    months = client.get('/api/analytics').get_json()['monthly']
    assert len(months) == 24
    assert months[0]['month'] == _month(-23)


def test_month_over_month_and_jobs(client, A):
    _seed(A, 'a1', signed_at=_month(-1) + '-10T12:00:00', total=100000.0)
    _seed(A, 'a2', signed_at=_month() + '-10T12:00:00', total=50000.0)
    _seed(A, 'a3', signed_at=_month() + '-20T12:00:00', total=70000.0)
    cur = _row(client.get('/api/analytics').get_json(), _month())
    assert cur['revenue'] == 120000.0
    assert cur['jobs'] == 2
    assert cur['avg_deal'] == 60000
    assert cur['mom_pct'] == 20        # 120k vs 100k


def test_year_over_year(client, A):
    _seed(A, 'a1', signed_at=_month(-12) + '-10T12:00:00', total=100000.0)
    _seed(A, 'a2', signed_at=_month() + '-10T12:00:00', total=150000.0)
    assert _row(client.get('/api/analytics').get_json(), _month())['yoy_pct'] == 50


def test_close_rate_is_a_sent_cohort(client, A):
    """'Close %' answers "of what we sent that month, how much closed" — the
    signed estimate counts against the month it was SENT, not signed."""
    m = _month(-1)
    _seed(A, 'a1', sent_at=m + '-02T12:00:00', signed_at=_month() + '-03T12:00:00')
    _seed(A, 'a2', sent_at=m + '-04T12:00:00')
    payload = client.get('/api/analytics').get_json()
    sent_month = _row(payload, m)
    assert sent_month['sent'] == 2
    assert sent_month['sent_won'] == 1
    assert sent_month['close_rate'] == 50
    # The revenue landed in the month it was signed, not the month it was sent.
    assert sent_month['revenue'] == 0.0
    assert _row(payload, _month())['revenue'] == 10000.0


def test_margin_uses_a_matching_revenue_and_cost_basis(client, A):
    _seed(A, 'a1', signed_at=_month() + '-10T12:00:00', total=100000.0, cost=60000.0)
    assert _row(client.get('/api/analytics').get_json(), _month())['margin_pct'] == 40.0


def test_retail_and_insurance_split_by_month(client, A):
    _seed(A, 'a1', signed_at=_month() + '-10T12:00:00', total=40000.0)
    _seed(A, 'a2', signed_at=_month() + '-11T12:00:00', total=60000.0, est_type='commercial')
    row = _row(client.get('/api/analytics').get_json(), _month())
    # An unknown estimate type falls into retail rather than vanishing from the
    # split — the two buckets must always add up to the month's revenue.
    assert row['retail'] + row['insurance'] == row['revenue']


# ── pace ───────────────────────────────────────────────────────────────

def test_current_month_pace(client, A):
    import calendar
    now = datetime.utcnow()
    dim = calendar.monthrange(now.year, now.month)[1]
    _seed(A, 'a1', signed_at=_month() + '-01T12:00:00', total=50000.0)
    client.put('/api/goals', json={'company': {'default': {'revenue': 100000}}})
    cm = client.get('/api/analytics').get_json()['current_month']

    assert cm['days_in_month'] == dim
    assert cm['days_elapsed'] == now.day
    assert cm['days_left'] == dim - now.day
    assert cm['gap'] == 50000.0
    # Straight-line projection of today's run rate to month end.
    assert cm['projected'] == pytest.approx(50000.0 / now.day * dim, rel=1e-6)
    assert cm['on_pace'] is (cm['projected'] >= 100000.0)
    assert cm['expected_to_date'] == pytest.approx(100000.0 * now.day / dim, abs=0.01)


def test_pace_is_none_without_a_goal(client, A):
    _seed(A, 'a1', signed_at=_month() + '-01T12:00:00', total=50000.0)
    cm = client.get('/api/analytics').get_json()['current_month']
    assert cm['goal'] == 0
    assert cm['pct'] is None
    assert cm['on_pace'] is None


def test_gap_never_goes_negative_once_the_goal_is_beaten(client, A):
    _seed(A, 'a1', signed_at=_month() + '-01T12:00:00', total=150000.0)
    client.put('/api/goals', json={'company': {'default': {'revenue': 100000}}})
    cm = client.get('/api/analytics').get_json()['current_month']
    assert cm['gap'] == 0.0
    assert cm['pct'] == 150


# ── per-rep ────────────────────────────────────────────────────────────

def test_rep_progress_against_rep_goal(client, A):
    _seed(A, 'a1', signed_at=_month() + '-05T12:00:00', total=40000.0, rep='luke')
    client.put('/api/goals', json={'reps': {'luke': {'default': {'revenue': 80000}}}})
    rm = client.get('/api/analytics').get_json()['rep_month']
    luke = next(r for r in rm if r['rep'] == 'luke')
    assert luke['revenue'] == 40000.0
    assert luke['goal'] == 80000.0
    assert luke['pct'] == 50
    assert luke['gap'] == 40000.0


def test_rep_name_case_does_not_split_one_rep_in_two(client, A):
    _seed(A, 'a1', signed_at=_month() + '-05T12:00:00', total=40000.0, rep='Luke')
    client.put('/api/goals', json={'reps': {'luke': {'default': {'revenue': 80000}}}})
    rm = client.get('/api/analytics').get_json()['rep_month']
    assert [r['rep'] for r in rm] == ['luke']
    assert rm[0]['revenue'] == 40000.0


def test_reps_with_no_goal_and_no_revenue_are_omitted(client, A):
    _seed(A, 'a1', signed_at=_month(-4) + '-05T12:00:00', total=40000.0, rep='ghost')
    rm = client.get('/api/analytics').get_json()['rep_month']
    assert [r['rep'] for r in rm] == []


# ── benchmarks ─────────────────────────────────────────────────────────

def test_trailing_averages_exclude_the_partial_current_month(client, A):
    """A part-way-through month would drag the averages down and make next
    month's goal look easier than it is."""
    _seed(A, 'a1', signed_at=_month(-2) + '-10T12:00:00', total=100000.0)
    _seed(A, 'a2', signed_at=_month(-1) + '-10T12:00:00', total=200000.0)
    _seed(A, 'a3', signed_at=_month() + '-01T12:00:00', total=10.0)
    bm = client.get('/api/analytics').get_json()['benchmarks']
    assert bm['avg_3'] == 150000.0     # only the two closed months
    assert bm['best_month']['month'] == _month(-1)


def test_best_month_is_none_with_no_revenue(client, A):
    _seed(A, 'a1', sent_at=_month() + '-01T12:00:00')
    assert client.get('/api/analytics').get_json()['benchmarks']['best_month'] is None
