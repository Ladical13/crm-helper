"""A second estimator reading the job before it goes to a homeowner.

Everything it checks, the tool already knew. The ventilation math prints
installed square inches against required; the margin report knows the worst
package on offer and which tiers have no cost behind them; `valid_until` knows
whether the pricing still stands. What did not exist was anything that read all
of it at once, at the moment before a rep sends it.

Two properties these tests exist to hold down. The review COMPUTES NOTHING —
every number comes from the function that already owns it, because a third
implementation of money math in a codebase that keeps exactly two and holds
them to the cent is how they start disagreeing. And it NEVER BLOCKS a send:
`_margin_floor_block` is the one gate, and a second gate that disagreed with
the first is how a rep ends up unable to ship a job neither of them explains.
"""
import json
import os
import re
import sys
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import app as A                      # noqa: E402
import estimate_review as ER         # noqa: E402


def _est(**kw):
    est = {
        'estimate_id': 'rev1', 'salesperson': 'luke', 'estimate_type': 'retail',
        'valid_until': (A._company_today().replace(year=A._company_today().year + 1)
                        ).isoformat(),
        'customer': {'name': 'Test', 'email': 'test@example.com',
                     'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'measurements': {'roof_squares': 30, 'attic_sqft': 3000},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': 20000.0, 'unit_cost': 12000.0}],
        }},
    }
    est.update(kw)
    return est


def _codes(facts_est):
    return {f['code'] for f in ER.deterministic_findings(A._review_facts(facts_est))}


# ── The rules run with no API key, and they are the ones worth acting on ────

def test_the_rules_run_with_no_api_key_at_all(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    out = ER.run(A._review_facts(_est(valid_until='2020-01-01')))
    assert not out['reviewer_ran']
    assert any(f['code'] == 'expired' for f in out['findings'])


def test_an_expired_estimate_is_flagged():
    assert 'expired' in _codes(_est(valid_until='2020-01-01'))


def test_an_estimate_about_to_lapse_is_flagged_before_it_does():
    """Homeowners take longer than three days to decide."""
    soon = (A._company_today() + __import__('datetime').timedelta(days=2)).isoformat()
    assert 'expiring' in _codes(_est(valid_until=soon))


def test_a_missing_customer_email_is_flagged():
    est = _est()
    est['customer']['email'] = ''
    assert 'no_email' in _codes(est)


def test_ventilation_short_of_code_is_flagged_as_high():
    """The scope is what the crew builds and what an inspector measures."""
    est = _est(measurements={'roof_squares': 30, 'attic_sqft': 3000})
    findings = ER.deterministic_findings(A._review_facts(est))
    vent = [f for f in findings if f['code'] == 'vent_exhaust_short']
    assert vent and vent[0]['severity'] == 'high'


def test_a_guessed_attic_area_is_named_rather_than_trusted():
    """Ventilation sized off roof squares is measuring against a guess — it
    over-vents rather than under-vents, which makes it easy not to notice."""
    assert 'attic_area_assumed' in _codes(_est(measurements={'roof_squares': 30}))
    assert 'attic_area_assumed' not in _codes(_est())


def test_an_insurance_job_with_no_measurements_is_flagged():
    """RoofR is the source of truth for quantities, and the Claim Check that
    finds a supplement compares against it."""
    est = _est(estimate_type='insurance', measurements={})
    assert 'no_measurements' in _codes(est)


def test_a_retail_job_with_no_measurements_is_not_nagged_about_the_claim():
    assert 'no_measurements' not in _codes(_est(estimate_type='retail'))


def test_a_tier_with_no_cost_reports_unknown_not_healthy():
    est = _est(trades={'roofing': {
        'enabled': True, 'mode': 'simple',
        'line_items': [{'name': 'Roof', 'quantity': 1, 'unit_price': 20000.0}],
    }})
    assert 'margin_unknown' in _codes(est)


def test_findings_come_back_worst_first():
    out = ER.deterministic_findings(A._review_facts(_est(valid_until='2020-01-01')))
    ranks = [ER._RANK[f['severity']] for f in out]
    assert ranks == sorted(ranks)


def test_a_clean_estimate_produces_nothing_alarming():
    """A review that always finds something is a review nobody reads."""
    est = _est(measurements={'roof_squares': 30, 'attic_sqft': 3000,
                             'ridge_lf': 200, 'eave_lf': 200})
    codes = _codes(est)
    assert 'expired' not in codes and 'no_email' not in codes


# ── It computes nothing ─────────────────────────────────────────────────────

def test_the_facts_come_from_the_functions_that_own_them():
    """Not a style point. In margin mode sell derives FROM cost, so a review
    that re-derived a margin would be a third implementation of the money math
    this repo keeps exactly two of and holds to the cent."""
    est = _est()
    facts = A._review_facts(est)
    assert facts['margin_tiers'] == (A.estimate_margin_report(est).get('tiers') or [])
    assert facts['expired'] == bool(A._est_expired(est))
    assert facts['vent']['exhaust_required'] == A._vent_nfa_report(est)['exhaust_required']


def test_the_reader_is_never_shown_a_price():
    """It is asked what is MISSING from a scope, never whether a price is
    right — and a number that is not in the payload cannot reach a finding."""
    est = _est()
    summary = A._review_trade_summary(est)
    blob = json.dumps(summary)
    assert '20000' not in blob and '12000' not in blob
    for rows in summary.values():
        for row in rows:
            assert 'unit_price' not in row and 'unit_cost' not in row


# ── It informs; it does not gate ────────────────────────────────────────────

def test_no_send_path_consults_the_review():
    """The margin floor is the only gate. A second one that disagreed with it
    is how a rep ends up unable to send a job neither of them can explain.

    Checked against every route that puts an estimate in front of a customer,
    rather than against one named function, so a new send path cannot quietly
    acquire a second gate.
    """
    src = open(os.path.join(HERE, '..', 'app.py'), encoding='utf-8').read()
    routes = re.split(r"^@app\.route\(", src, flags=re.M)
    sending = [r for r in routes
               if re.match(r"'/api/estimates/<est_id>/(share|send-email)'", r)]
    assert len(sending) >= 2, 'the send routes moved — this test is now blind'
    for r in sending:
        assert 'estimate_review' not in r


def test_a_reader_that_fails_never_costs_the_rule_findings(monkeypatch):
    """The free layer is the one worth acting on. A rate limit, an outage or a
    missing key must not take it down with the reader."""
    monkeypatch.setattr(ER, 'available', lambda: True)
    monkeypatch.setattr(ER, 'ai_findings', lambda *a, **k: (_ for _ in ()).throw(
        ER.ReviewError('rate limited')))
    out = ER.run(A._review_facts(_est(valid_until='2020-01-01')))
    assert any(f['code'] == 'expired' for f in out['findings'])
    assert out['reviewer_error'] == 'rate limited'


def test_a_half_run_review_says_so_rather_than_looking_clean(monkeypatch):
    """A reader that quietly did not run reads exactly like a clean estimate."""
    monkeypatch.setattr(ER, 'available', lambda: True)
    monkeypatch.setattr(ER, 'ai_findings',
                        lambda *a, **k: (_ for _ in ()).throw(ER.ReviewError('down')))
    out = ER.run(A._review_facts(_est()))
    assert out['reviewer_error'] == 'down'
    assert out['reviewer_ran'] is False


# ── What comes back from the reader is checked ──────────────────────────────

class _Stream:
    def __init__(self, msg): self._msg = msg
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self._msg


def _client(payload=None, *, text=None, stop_reason='end_turn'):
    body = text if text is not None else json.dumps(payload)
    blocks = [] if stop_reason == 'refusal' else [SimpleNamespace(type='text', text=body)]
    msg = SimpleNamespace(stop_reason=stop_reason, content=blocks)
    return SimpleNamespace(beta=SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **_kw: _Stream(msg))))


@pytest.fixture()
def _key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(ER, 'anthropic', object())


def test_a_finding_with_no_text_is_dropped(_key):
    out = ER.ai_findings({}, [], client=_client({'findings': [
        {'severity': 'high', 'what': '', 'fix': 'x'}]}))
    assert out == []


def test_a_severity_outside_the_three_is_dropped(_key):
    out = ER.ai_findings({}, [], client=_client({'findings': [
        {'severity': 'catastrophic', 'what': 'the roof is on fire', 'fix': 'x'}]}))
    assert out == []


def test_a_real_reader_finding_is_labelled_as_one(_key):
    out = ER.ai_findings({}, [], client=_client({'findings': [
        {'severity': 'high', 'what': 'Tear-off with no haul-off line.',
         'fix': 'Add disposal.'}]}))
    assert len(out) == 1 and out[0]['source'] == 'reviewer'


def test_unreadable_output_is_an_error_not_an_empty_review(_key):
    with pytest.raises(ER.ReviewError):
        ER.ai_findings({}, [], client=_client(text='not json'))


def test_a_refusal_is_an_error_not_an_empty_review(_key):
    with pytest.raises(ER.ReviewError):
        ER.ai_findings({}, [], client=_client(stop_reason='refusal'))


def test_the_reader_is_handed_what_the_rules_already_found(_key):
    """Restating a rule finding doubles the list a rep has to read, and the
    duplicate is the one that makes them stop reading."""
    seen = {}
    def capture(**kw):
        seen.update(kw)
        return _Stream(SimpleNamespace(stop_reason='end_turn', content=[
            SimpleNamespace(type='text', text='{"findings": []}')]))
    client = SimpleNamespace(beta=SimpleNamespace(
        messages=SimpleNamespace(stream=capture)))

    rules = [{'code': 'expired', 'severity': 'high', 'what': 'Pricing lapsed.',
              'fix': '', 'source': 'rule'}]
    ER.ai_findings({'estimate_type': 'retail'}, rules, client=client)

    sent = json.loads(seen['messages'][0]['content'])
    assert sent['already_found_by_rules'] == ['Pricing lapsed.']
    assert 'Do not recompute' in seen['system']
