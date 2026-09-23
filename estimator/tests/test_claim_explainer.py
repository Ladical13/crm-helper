"""The homeowner's own claim, explained back to them.

We parse every line of an Xactimate or Symbility estimate and have never told
the homeowner any of it. "Why is the check smaller than the estimate?" is the
question every insurance customer asks, the answer is recoverable depreciation,
and a homeowner who does not understand it concludes either that their carrier
is cheating them or that we are.

One rule governs the whole thing, and it is what these tests mostly check:
**explain, never recalculate.** Every figure is the carrier's own. A homeowner
may repeat any of them to their adjuster, so a number this document invented
would be a number we invented.
"""
import json
import os
import re
import sys
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import app as A                  # noqa: E402
import claim_explainer as CE     # noqa: E402


FIGURES = {'rcv_total': 28450.00, 'acv_total': 19870.25,
           'recoverable_depreciation': 8579.75, 'deductible': 2500.00,
           'date_of_loss': '2026-06-12'}


def _est(**claim):
    c = dict(FIGURES)
    c.update(claim)
    return {
        'estimate_id': 'ce-test', 'estimate_type': 'insurance',
        'customer': {'name': 'Jennifer Ruiz',
                     'address': {'street': '1420 Oak St', 'city': 'Loveland',
                                 'state': 'CO'}},
        'trades': {'insurance': {'enabled': True, 'carrier': 'State Farm',
                                 'claim_number': 'CLM-2026-8841', 'sections': []}},
        'insurance_claim': c,
    }


def _pdf_text(raw):
    """Extracted page text with whitespace collapsed.

    A PDF breaks a line wherever the column ran out, so a phrase on the page
    arrives here split across a newline. Searching the raw extraction for
    "recoverable depreciation" fails on a page that says it perfectly.
    """
    fitz = pytest.importorskip('fitz')
    import io
    joined = '\n'.join(p.get_text() for p in
                       fitz.open(stream=io.BytesIO(raw), filetype='pdf'))
    return re.sub(r'\s+', ' ', joined)


# ── The figures are the carrier's, unchanged ────────────────────────────────

def test_every_figure_on_the_sheet_came_from_the_carrier():
    """The whole safety model. A homeowner may repeat any of these to their
    adjuster, so a figure we produced would be a figure we invented."""
    facts = CE.claim_facts(_est())
    assert facts['rcv_total'] == 28450.00
    assert facts['acv_total'] == 19870.25
    assert facts['depreciation'] == 8579.75
    assert facts['deductible'] == 2500.00


def test_a_missing_figure_stays_missing_rather_than_becoming_zero():
    """"Your carrier withheld $0.00" is a sentence about a claim nobody
    imported, and a homeowner would read it as a fact about theirs."""
    facts = CE.claim_facts(_est(deductible=None, recoverable_depreciation=None))
    assert facts['deductible'] is None
    assert facts['depreciation'] is None


def test_a_missing_figure_never_reaches_the_page():
    raw = A.build_claim_explainer_pdf(_est(paid_when_incurred=None))
    assert 'Paid when the work is incurred' not in _pdf_text(raw)


def test_junk_in_a_figure_is_dropped_not_printed():
    assert CE.claim_facts(_est(rcv_total='n/a'))['rcv_total'] is None


# ── The carrier's arithmetic is checked, never performed ────────────────────

def test_the_identity_is_checked_against_the_carriers_own_numbers():
    assert CE.reconciles(CE.claim_facts(_est())) is True


def test_a_claim_that_does_not_tie_out_is_not_claimed_to():
    """Carriers legitimately carry non-recoverable depreciation and
    pay-when-incurred lines this subtraction does not see. A homeowner who
    checks it and finds it wrong stops believing the rest of the page."""
    facts = CE.claim_facts(_est(acv_total=15000.00))
    assert CE.reconciles(facts) is False
    text = _pdf_text(A.build_claim_explainer_pdf(_est(acv_total=15000.00)))
    assert 'may not subtract evenly' in text
    assert 'equals the approved total' not in text


def test_a_claim_that_ties_out_says_so():
    text = _pdf_text(A.build_claim_explainer_pdf(_est()))
    assert 'equals the approved total' in text


def test_reconciles_is_none_when_a_figure_is_missing():
    assert CE.reconciles(CE.claim_facts(_est(acv_total=None))) is None


# ── There has to be a claim worth explaining ────────────────────────────────

def test_an_estimate_with_no_carrier_figures_is_refused():
    """A page with a logo and a paragraph of generalities is worse than not
    offering one."""
    est = _est()
    est['insurance_claim'] = {}
    assert not CE.has_enough(CE.claim_facts(est))
    with pytest.raises(ValueError):
        A.build_claim_explainer_pdf(est)


def test_rcv_alone_is_not_enough_to_explain_anything():
    est = _est(recoverable_depreciation=None, deductible=None)
    assert not CE.has_enough(CE.claim_facts(est))


# ── Nothing of ours is on their page ────────────────────────────────────────

def test_our_pricing_never_appears_on_the_homeowners_sheet(monkeypatch):
    """This page is about their claim. What the job costs us to build is not
    on it, and the prompt forbids the narrative from going there."""
    est = _est()
    est['trades']['roofing'] = {'enabled': True, 'mode': 'simple',
                                'line_items': [{'name': 'Roof', 'quantity': 1,
                                                'unit_price': 31999.0,
                                                'unit_cost': 17777.0}]}
    est['insurance_cost'] = {'items': [{'name': 'Labor', 'cost': 9111.0}]}
    text = _pdf_text(A.build_claim_explainer_pdf(est))
    for ours in ('31,999', '17,777', '9,111'):
        assert ours not in text
    src = open(os.path.join(HERE, '..', 'claim_explainer.py'), encoding='utf-8').read()
    assert 'margin' in src and 'NEVER produce a number' in src


def test_the_page_carries_its_own_disclaimer():
    text = _pdf_text(A.build_claim_explainer_pdf(_est()))
    assert 'not' in text and 'legal, tax or insurance advice' in text


# ── It works with no API key ────────────────────────────────────────────────

def test_the_sheet_builds_with_no_api_key(monkeypatch):
    """A homeowner waiting to understand their claim should not be held up by
    an API key, and the template answers their actual question."""
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    text = _pdf_text(A.build_claim_explainer_pdf(_est()))
    assert '$28,450.00' in text
    assert 'recoverable depreciation' in text.lower()


def test_the_template_explains_the_thing_homeowners_actually_ask():
    """Why the first check is smaller than the total. Everything else on this
    page is context for that one paragraph."""
    text = CE.fallback_narrative(CE.claim_facts(_est()))
    assert 'smaller' in text and 'normal' in text
    assert '$8,579.75' in text


def test_the_template_never_offers_to_cover_the_deductible():
    """Colorado law, and the contract language already says so. A sheet that
    softened it would contradict the contract the same customer signs."""
    text = CE.fallback_narrative(CE.claim_facts(_est()))
    assert 'cannot waive' in text


def test_the_template_is_silent_about_a_figure_it_was_not_given():
    text = CE.fallback_narrative(CE.claim_facts(_est(deductible=None)))
    assert 'deductible' not in text.lower()


def test_a_failed_narrative_falls_back_rather_than_raising(monkeypatch):
    monkeypatch.setattr(CE, 'narrative',
                        lambda *a, **k: (_ for _ in ()).throw(CE.ExplainError('down')))
    text, source = CE.explanation(CE.claim_facts(_est()))
    assert source == 'template' and text


# ── The written narrative ───────────────────────────────────────────────────

class _Stream:
    def __init__(self, msg): self._msg = msg
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self._msg


def _client(text='Here is what it means.', stop_reason='end_turn', capture=None):
    blocks = [] if stop_reason == 'refusal' else [SimpleNamespace(type='text', text=text)]
    msg = SimpleNamespace(stop_reason=stop_reason, content=blocks)
    def stream(**kw):
        if capture is not None:
            capture.update(kw)
        return _Stream(msg)
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


@pytest.fixture()
def _key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(CE, 'anthropic', object())


def test_the_writer_is_only_given_figures_that_exist(_key):
    """A None handed to the model is an invitation to invent one."""
    seen = {}
    CE.narrative(CE.claim_facts(_est(deductible=None)), client=_client(capture=seen))
    sent = json.loads(seen['messages'][0]['content'])
    assert 'deductible' not in sent
    assert sent['rcv_total'] == 28450.00


def test_an_empty_narrative_is_an_error_not_an_empty_page(_key):
    with pytest.raises(CE.ExplainError):
        CE.narrative(CE.claim_facts(_est()), client=_client(text='   '))


def test_a_refusal_is_an_error_not_an_empty_page(_key):
    with pytest.raises(CE.ExplainError):
        CE.narrative(CE.claim_facts(_est()), client=_client(stop_reason='refusal'))


def test_the_prompt_forbids_producing_a_number(_key):
    seen = {}
    CE.narrative(CE.claim_facts(_est()), client=_client(capture=seen))
    sysmsg = seen['system']
    assert 'NEVER produce a number' in sysmsg
    assert 'legal' in sysmsg, 'the writer must not give advice'
    assert 'unfairly' in sysmsg, 'the writer must not attack the carrier'


# ── The route ───────────────────────────────────────────────────────────────

def test_the_route_refuses_an_estimate_with_no_claim(client, monkeypatch):
    est = _est()
    est['insurance_claim'] = {}
    monkeypatch.setattr(A, 'est_load', lambda _id: est)
    monkeypatch.setattr(A, '_can_touch_estimate', lambda _e: True)
    r = client.post('/api/estimates/ce-test/claim-explainer')
    assert r.status_code == 400
    assert 'carrier' in r.get_json()['error']


def test_the_route_returns_a_pdf(client, monkeypatch):
    monkeypatch.setattr(A, 'est_load', lambda _id: _est())
    monkeypatch.setattr(A, '_can_touch_estimate', lambda _e: True)
    r = client.post('/api/estimates/ce-test/claim-explainer')
    assert r.status_code == 200
    assert r.mimetype == 'application/pdf'
