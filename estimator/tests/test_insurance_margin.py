"""Insurance job margin — carrier RCV against what the job actually costs us.

On a retail job the rep sets the price and the margin follows. On an insurance
job the carrier sets the price and the margin is whatever is left after we
build the roof — and the tool could not see it at all: insurance line items
carry the carrier's unit_price and never our cost, so an insurance estimate
reported no margin and was excluded from every margin figure on the analytics
tab. Most of this company's work is insurance.

The cost side is DERIVED from the measurement report plus the price book
rather than typed: a carrier export runs 30-80 lines and nobody was ever going
to cost them by hand.
"""
import json
import os
import shutil
import subprocess

import pytest

import app as A

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'insurance_cost_runner.js')


def _est(items=None, adders=None, supplements=0, rcv=10000.0):
    """An insurance estimate whose claim totals `rcv`, with a cost sheet."""
    return {
        'estimate_id': 'ins-margin', 'salesperson': 'luke',
        'estimate_type': 'insurance',
        'trades': {'insurance': {'enabled': True, 'sections': [
            {'name': 'Dwelling Roof', 'items': [
                {'description': 'Roofing', 'acv': rcv * 0.7,
                 'depreciation': rcv * 0.3},
            ]},
        ]}},
        'insurance_cost': {
            'bundle_id': 'b_landmark',
            'items': items if items is not None else [],
            'adders': adders or {},
            'supplements': supplements,
        },
    }


# ── The core arithmetic ───────────────────────────────────────────────────

def test_margin_is_carrier_revenue_against_our_cost():
    est = _est(items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 110.0},
                      {'name': 'Labor', 'quantity': 30, 'unit_cost': 95.0}])
    r = A.insurance_cost_report(est)
    assert r['revenue'] == 10000.0
    assert r['build_cost'] == 6150.0          # 30×110 + 30×95
    assert r['gross_profit'] == 3850.0
    assert r['margin_pct'] == 38.5


def test_adders_count_against_the_job():
    """A dumpster and a permit are real money the measurements cannot know."""
    est = _est(items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 110.0}],
               adders={'dumpster': 525.0, 'permit': 210.0})
    r = A.insurance_cost_report(est)
    assert r['adders_total'] == 735.0
    assert r['cost'] == 3300.0 + 735.0


def test_supplements_add_to_what_the_carrier_pays():
    """An approved supplement is the main lever on an insurance job's margin,
    since the rep cannot raise the carrier's price any other way."""
    est = _est(items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 110.0}],
               supplements=2000.0)
    r = A.insurance_cost_report(est)
    assert r['revenue'] == 12000.0
    assert r['margin_pct'] == 72.5


def test_an_uncosted_claim_reports_unknown_not_a_perfect_margin():
    """The trap this whole feature could have shipped with: a job nobody has
    costed would arrive as 100% profit and sort to the top of every
    profitability table."""
    r = A.insurance_cost_report(_est())
    assert r['margin_pct'] is None
    assert r['costed'] is False


def test_a_claim_with_no_revenue_reports_unknown():
    r = A.insurance_cost_report(_est(rcv=0.0,
        items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 110.0}]))
    assert r['margin_pct'] is None


def test_a_job_that_loses_money_says_so():
    """Insurance work can genuinely come out underwater once a supplement is
    denied. It must read negative, not clamp at zero."""
    est = _est(items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 400.0}])
    r = A.insurance_cost_report(est)
    assert r['gross_profit'] == -2000.0
    assert r['margin_pct'] == -20.0


def test_junk_in_the_cost_sheet_does_not_crash_the_margin():
    est = _est(items=[{'name': 'Bad', 'quantity': 'abc', 'unit_cost': None},
                      {'name': 'Good', 'quantity': 10, 'unit_cost': 50.0}],
               adders={'permit': ''}, supplements='')
    r = A.insurance_cost_report(est)
    assert r['build_cost'] == 500.0


def test_cost_items_never_reach_the_customer_total():
    """They live outside `trades` on purpose — nothing that builds a
    customer-facing document can pick them up and print them."""
    est = _est(items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 110.0}])
    assert A._estimate_total(est) == 10000.0
    assert 'insurance_cost' not in json.dumps(est['trades'])


# ── The two implementations must agree ────────────────────────────────────

@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_the_browser_and_the_server_compute_the_same_margin(tmp_path):
    """Same reason every other price in this app is implemented twice: the rep
    needs it to move as they type, and the server cannot trust the client for
    anything that reaches analytics. This is what stops them drifting."""
    cases = [
        ('plain', _est(items=[{'name': 'S', 'quantity': 30, 'unit_cost': 110.0}])),
        ('adders', _est(items=[{'name': 'S', 'quantity': 28.7, 'unit_cost': 112.35}],
                        adders={'dumpster': 525.0, 'permit': 210.5, 'subs': 1200.0,
                                'other': 75.25})),
        ('supplement', _est(items=[{'name': 'S', 'quantity': 31.4, 'unit_cost': 98.75}],
                            adders={'permit': 210.0}, supplements=1834.19)),
        ('uncosted', _est()),
        ('underwater', _est(items=[{'name': 'S', 'quantity': 30, 'unit_cost': 400.0}])),
        ('no_revenue', _est(rcv=0.0, items=[{'name': 'S', 'quantity': 1, 'unit_cost': 5.0}])),
    ]
    fx = tmp_path / 'fx.json'
    out = tmp_path / 'out.json'
    fx.write_text(json.dumps([{'name': n, 'estimate': e} for n, e in cases]))
    subprocess.run(['node', RUNNER, str(fx), str(out)], check=True,
                   capture_output=True, text=True)
    js = {r['name']: r['report'] for r in json.loads(out.read_text())}

    for name, est in cases:
        py = A.insurance_cost_report(est)
        j = js[name]
        assert round(j['revenue'], 2) == py['revenue'], f'{name}: revenue'
        assert round(j['cost'], 2) == py['cost'], f'{name}: cost'
        assert round(j['gross_profit'], 2) == py['gross_profit'], f'{name}: profit'
        if py['margin_pct'] is None:
            assert j['margin_pct'] is None, f'{name}: margin should be unknown'
        else:
            assert round(j['margin_pct'], 1) == py['margin_pct'], f'{name}: margin'


# ── Analytics ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_slate():
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)


def _signed(eid, **kw):
    est = _est(**kw)
    est['estimate_id'] = eid
    est['share_token'] = 'tok-' + eid
    est['sent_at'] = '2026-07-01T00:00:00Z'
    est['signature'] = {'name': 'Jane', 'signed_at': '2026-07-15T00:00:00Z'}
    est['customer'] = {'name': 'Jane', 'address': {'city': 'Loveland'}}
    A.est_save(est)
    return est


def test_a_costed_insurance_job_now_carries_a_margin_on_the_tab(client):
    _signed('ins-a', items=[{'name': 'S', 'quantity': 30, 'unit_cost': 110.0}],
            adders={'dumpster': 500.0})
    body = client.get('/api/analytics').get_json()
    assert body['by_trade']['insurance']['margin_pct'] == 62.0


def test_an_uncosted_insurance_job_is_left_out_of_the_margin_entirely(client):
    """Counting it would arrive as pure profit and drag every margin figure on
    the tab upward — the reverse of the problem this feature exists to fix."""
    _signed('ins-b')
    body = client.get('/api/analytics').get_json()
    assert 'insurance' not in body['by_trade']


def test_insurance_revenue_still_reaches_the_type_breakdown(client):
    """It always did; costing must not change what the job is worth."""
    _signed('ins-c', items=[{'name': 'S', 'quantity': 30, 'unit_cost': 110.0}])
    body = client.get('/api/analytics').get_json()
    assert body['by_type']['insurance']['revenue'] == 10000.0


# ── The price book behind the margin ──────────────────────────────────────

def test_a_cost_line_with_no_price_is_named():
    """The failure this feature could most easily have shipped with. A freshly
    seeded roofing bundle carries Tear-Off Labor and Install Labor at $0, so an
    uncorrected book reports a roof that costs only its shingles — and the
    margin lands 20-30 points high, in the direction that makes a bad job look
    good."""
    est = _est(items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 142.0},
                      {'name': 'Install Labor', 'quantity': 30, 'unit_cost': 0},
                      {'name': 'Tear-Off Labor', 'quantity': 30, 'unit_cost': 0}])
    r = A.insurance_cost_report(est)
    assert r['unpriced'] == ['Install Labor', 'Tear-Off Labor']


def test_a_zero_quantity_line_is_not_flagged():
    """Not in scope is not the same as not priced — a job with no step flashing
    must not be reported as missing its cost."""
    est = _est(items=[{'name': 'Step Flashing', 'quantity': 0, 'unit_cost': 0},
                      {'name': 'Shingles', 'quantity': 30, 'unit_cost': 142.0}])
    assert A.insurance_cost_report(est)['unpriced'] == []


def test_a_fully_priced_job_flags_nothing():
    est = _est(items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 142.0},
                      {'name': 'Labor', 'quantity': 30, 'unit_cost': 95.0}])
    assert A.insurance_cost_report(est)['unpriced'] == []


def test_analytics_refuses_a_margin_the_price_book_cannot_support(client):
    """Worse than no margin: it would pull the company average up and make
    insurance work look like the thing to chase."""
    _signed('ins-unpriced',
            items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 142.0},
                   {'name': 'Install Labor', 'quantity': 30, 'unit_cost': 0}])
    body = client.get('/api/analytics').get_json()
    assert 'insurance' not in body['by_trade']


def test_the_front_end_flags_the_same_lines(tmp_path):
    """The banner and the analytics guard have to agree about which job is
    trustworthy, or the rep sees a warning the numbers ignore."""
    import shutil as _sh
    if _sh.which('node') is None:
        pytest.skip('node not installed')
    est = _est(items=[{'name': 'Shingles', 'quantity': 30, 'unit_cost': 142.0},
                      {'name': 'Install Labor', 'quantity': 30, 'unit_cost': 0}])
    fx, out = tmp_path / 'f.json', tmp_path / 'o.json'
    fx.write_text(json.dumps([{'name': 'unpriced', 'estimate': est}]))
    subprocess.run(['node', RUNNER, str(fx), str(out)], check=True,
                   capture_output=True, text=True)
    js = json.loads(out.read_text())[0]['report']
    assert js['unpriced'] == A.insurance_cost_report(est)['unpriced']
