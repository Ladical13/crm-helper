"""A second reader for the material/labor split — proposals only.

`_guess_cost_class` is a keyword match, and its carve-outs are a record of the
traps somebody already hit rather than of the traps that exist: "Pancake
ScREWs" is why `crew` is not a labor word, and "Metal Delivery & Rollformer
Set-Up" is why the exclusion list runs first. This module asks something that
reads a name the way a person would.

What these tests hold down is not the model's judgement — that is not testable
and not the point. It is the boundary around it: the model proposes, a manager
approves, and nothing it returns can reach the price book on its own.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import app as A                      # noqa: E402
import cost_class_review as CCR      # noqa: E402


def _book():
    return {
        'roofing_catalog': [
            {'id': 'a_shingle', 'name': 'Architectural Shingle', 'cost': 120},
            {'id': 'l_install', 'name': 'Install Labor', 'cost': 145,
             'cost_class': 'labor'},
            {'id': 'a_ss_clips', 'name': 'Seam Clips + Pancake Screws', 'cost': 40},
        ],
        'siding_catalog': [
            {'id': 'x_ss_delivery', 'name': 'Metal Delivery & Rollformer Set-Up',
             'cost': 368},
        ],
        'intros': [],
    }


def _rows(pb=None):
    pb = pb or _book()
    return CCR.products_for_review(pb, lambda p: A._norm_cost_class(p.get('cost_class')))


class _Stream:
    def __init__(self, msg): self._msg = msg
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self._msg


def _msg(payload=None, *, text=None, stop_reason='end_turn'):
    """A stand-in for the SDK's final message: content blocks plus a stop."""
    body = text if text is not None else json.dumps(payload)
    blocks = [] if stop_reason == 'refusal' else [
        SimpleNamespace(type='text', text=body)]
    return SimpleNamespace(stop_reason=stop_reason, content=blocks)


def _client(payload=None, **kw):
    """A fake Anthropic client shaped like the one call this module makes.

    `stream` is an ordinary function on a namespace rather than a method on a
    class, so Python does not bind it and pass `self` into the keyword-only
    signature the real SDK has.
    """
    message = _msg(payload, **kw)
    return SimpleNamespace(
        beta=SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **_kw: _Stream(message))))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(CCR, 'anthropic', object())


# ── What gets sent ──────────────────────────────────────────────────────────

def test_every_catalog_product_is_offered_with_its_current_class():
    rows = _rows()
    by_id = {pid: (trade, name, cur) for trade, pid, name, cur in rows}
    assert by_id['l_install'][2] == 'labor'
    # Absence means material — the behaviour before cost_class existed.
    assert by_id['a_shingle'][2] == 'material'
    assert by_id['x_ss_delivery'][0] == 'siding'


def test_a_product_with_no_id_is_skipped_rather_than_keyed_on_blank():
    pb = _book()
    pb['roofing_catalog'].append({'name': 'Nameless', 'cost': 1})
    assert all(pid for _t, pid, _n, _c in _rows(pb))


# ── What comes back is checked against what went out ────────────────────────

def test_a_proposal_for_a_product_the_book_does_not_have_is_dropped():
    """The model is a second reader, not a second source of ids."""
    out = CCR.review(_rows(), client=_client({'proposals': [
        {'product_id': 'not_a_real_product', 'proposed': 'labor', 'reason': 'x'}]}))
    assert out == []


def test_a_proposal_that_changes_nothing_is_dropped():
    """A row whose 'proposal' is the class already stored is noise on a screen
    a manager has to read line by line."""
    out = CCR.review(_rows(), client=_client({'proposals': [
        {'product_id': 'l_install', 'proposed': 'labor', 'reason': 'it is labor'}]}))
    assert out == []


def test_a_class_that_is_not_one_of_the_two_is_dropped():
    out = CCR.review(_rows(), client=_client({'proposals': [
        {'product_id': 'a_shingle', 'proposed': 'overhead', 'reason': 'x'}]}))
    assert out == []


def test_a_real_proposal_survives_with_both_sides_of_the_diff():
    out = CCR.review(_rows(), client=_client({'proposals': [
        {'product_id': 'a_shingle', 'proposed': 'labor',
         'reason': 'Reads like crew time.'}]}))
    assert len(out) == 1
    assert out[0]['current'] == 'material' and out[0]['proposed'] == 'labor'
    assert out[0]['name'] == 'Architectural Shingle'
    assert out[0]['reason'], 'a manager cannot judge a proposal with no reason'


def test_unreadable_output_is_an_error_not_an_empty_review():
    """Silently reporting "nothing to change" when the call actually failed is
    how a manager concludes the book is clean."""
    with pytest.raises(CCR.ReviewError):
        CCR.review(_rows(), client=_client(text='not json'))


def test_a_refusal_is_an_error_not_an_empty_review():
    with pytest.raises(CCR.ReviewError):
        CCR.review(_rows(), client=_client(stop_reason='refusal'))


# ── Applying is separate, and takes ids a human ticked ──────────────────────

def test_apply_writes_only_the_ids_it_is_given():
    pb = _book()
    changed = CCR.apply(pb, [{'product_id': 'a_shingle', 'cost_class': 'labor'}])
    assert len(changed) == 1
    by_id = {p['id']: p for p in pb['roofing_catalog']}
    assert by_id['a_shingle']['cost_class'] == 'labor'
    assert 'cost_class' not in by_id['a_ss_clips'], 'an untouched product was rewritten'


def test_apply_ignores_a_class_that_is_not_one_of_the_two():
    pb = _book()
    assert CCR.apply(pb, [{'product_id': 'a_shingle', 'cost_class': 'drop table'}]) == []
    assert 'cost_class' not in pb['roofing_catalog'][0]


def test_apply_with_nothing_approved_changes_nothing():
    pb = _book()
    assert CCR.apply(pb, []) == []
    assert CCR.apply(pb, None) == []


def test_apply_never_touches_a_cost():
    """The one guarantee that makes this safe to get wrong: a reclassification
    moves which column a cost is reported in and cannot move the cost, a sell
    price, a margin floor or a quantity. In margin mode sell derives FROM cost,
    so a module that could edit one could move a price a customer already holds
    a link to."""
    pb = _book()
    before = {p['id']: p.get('cost') for p in pb['roofing_catalog']}
    CCR.apply(pb, [{'product_id': p, 'cost_class': 'labor'} for p in before])
    after = {p['id']: p.get('cost') for p in pb['roofing_catalog']}
    assert before == after


# ── The endpoints ───────────────────────────────────────────────────────────

def test_review_is_manager_up_not_open_to_reps(client, monkeypatch):
    monkeypatch.setattr(A, '_is_manager_up', lambda: False)
    assert client.post('/api/pricebook/cost-class-review').status_code in (302, 401, 403)


def test_apply_is_manager_up_not_open_to_reps(client, monkeypatch):
    monkeypatch.setattr(A, '_is_manager_up', lambda: False)
    assert client.post('/api/pricebook/cost-class-apply',
                       json={'approved': []}).status_code in (302, 401, 403)


def test_without_an_api_key_the_review_says_so_and_the_book_is_unaffected(
        client, monkeypatch):
    """No key means the price book behaves exactly as it did before this module
    existed — the keyword guesser is free, offline and unchanged."""
    monkeypatch.setattr(A, '_is_manager_up', lambda: True)
    monkeypatch.setattr(CCR, 'available', lambda: False)
    r = client.post('/api/pricebook/cost-class-review')
    assert r.status_code == 503
    assert r.get_json()['available'] is False
    assert A._guess_cost_class('l_install', 'Install Labor') == 'labor'


def test_the_keyword_guesser_is_still_the_default_and_still_first():
    """This module reviews that guesser's work; it does not replace it. Every
    new product is classified the moment it is created, with no API key, no
    network and no latency."""
    assert A._guess_cost_class('a_ss_clips', 'Seam Clips + Pancake Screws') == 'material'
    assert A._guess_cost_class('x_ss_delivery',
                               'Metal Delivery & Rollformer Set-Up') == 'material'
    assert A._guess_cost_class('l_install', 'Install Labor') == 'labor'
