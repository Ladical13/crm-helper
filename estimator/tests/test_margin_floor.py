"""Margin floors, and the input binding that used to defeat them.

Three separate things live here because they are one story: a rep could sell a
roof at cost, the tool could not tell, and nothing stopped the estimate going
out.

1. `setTierRate` wrote `parseFloat(v) || 0`, so CLEARING a Good/Better/Best
   margin box stored a real 0 that the rate chain then honoured exactly as
   designed — priced at cost, normal-looking screen, parity green because both
   sides agreed about the wrong number. The other two setters had always
   mapped blank to null.
2. Nothing anywhere compared the resulting margin against a floor.
3. A margin of 100 or more priced every line at $0 rather than erroring.
"""
import json
import os
import re

import pytest

import app as A


APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'static', 'app.js')


def _fn(name):
    """Source of one top-level `function name(` up to its closing brace, with
    // comments stripped — these assert on what the code DOES, and the comments
    here quote the very expression being banned."""
    src = open(APP_JS, encoding='utf-8').read()
    i = src.index(f'function {name}(')
    depth, j = 0, src.index('{', i)
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                body = src[i:k + 1]
                return '\n'.join(re.sub(r'//.*$', '', ln)
                                  for ln in body.split('\n'))
    raise AssertionError(f'unterminated {name}')


# ── 1. The input binding ──────────────────────────────────────────────────

def test_clearing_a_tier_margin_inherits_rather_than_selling_at_cost():
    """The bug, pinned at its source. `|| 0` here is what turned an empty box
    into an explicit 0% margin."""
    body = _fn('setTierRate')
    assert '_rateValue(' in body, 'setTierRate must route through _rateValue'
    assert '|| 0' not in body, (
        'a cleared margin box must inherit, not resolve to 0% — that sells the '
        'roof at cost with a completely normal-looking screen')


def test_all_three_rate_setters_agree_about_an_empty_box():
    """setTradeOverride and setTradeTierRate were always right; setTierRate was
    the outlier. A single gesture must not mean two different things."""
    for name in ('setTierRate', 'setTradeOverride', 'setTradeTierRate'):
        assert '_rateValue(' in _fn(name), f'{name} must use _rateValue'


def test_an_explicit_zero_is_still_honoured():
    """Inheriting on blank must not take away selling at cost on purpose."""
    p = {'tier_rates': {'good': 0}, 'global_rate': 35}
    assert A._tier_rate(p, 'roofing', 'good') == 0.0
    assert A._tier_rate(p, 'roofing', 'better') == 35.0


def test_a_missing_tier_rate_falls_through_to_the_house_default():
    assert A._tier_rate({}, 'roofing', 'good') == A.DEFAULT_RATE == 35.0


# ── 2. The floors ─────────────────────────────────────────────────────────

def _est(sell, cost, *, tiers=('good', 'better', 'best'), est_type='retail'):
    return {
        'estimate_id': 'x', 'salesperson': 'luke', 'estimate_type': est_type,
        'tiers_enabled': {t: t in tiers for t in ('good', 'better', 'best')},
        'pricing': {'mode': 'margin'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': sell, 'unit_cost': cost}],
        }},
    }


def test_realized_margin_is_computed_from_cost_not_from_the_rate_box():
    """A 30% markup is a 23% margin. Reading the rate straight off the box
    would wave through jobs that are actually under the floor."""
    rep = A.estimate_margin_report(_est(10000.0, 7000.0))
    assert rep['lowest']['margin_pct'] == 30.0


def test_a_tier_with_no_cost_reports_an_unknown_margin_not_a_perfect_one():
    """The commercial catalog ships $0 placeholder costs on purpose. Calling
    those a 100% margin would hand a clean bill of health to exactly the bids
    that have no supplier pricing yet."""
    rep = A.estimate_margin_report(_est(10000.0, 0.0))
    assert rep['tiers'][0]['margin_pct'] is None
    assert rep['lowest'] is None


def test_the_floor_reads_the_worst_package_on_offer(monkeypatch):
    """The CUSTOMER picks the package, so the number that matters is the worst
    one they could choose, not the one the rep happens to be looking at."""
    est = {
        'estimate_id': 'x', 'estimate_type': 'retail',
        'tiers_enabled': {'good': True, 'better': True, 'best': True},
        'pricing': {'mode': 'margin'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'gbb',
            'line_items': [{'name': 'Roof', 'quantity': 1, 'tiers': {
                'good':   {'material_unit_cost': 9000.0, 'labor_unit_cost': 0,
                           'price_override': 10000.0},   # 10% — the worst
                'better': {'material_unit_cost': 6000.0, 'labor_unit_cost': 0,
                           'price_override': 12000.0},   # 50%
                'best':   {'material_unit_cost': 5000.0, 'labor_unit_cost': 0,
                           'price_override': 15000.0},   # 66%
            }}],
        }},
    }
    assert A.estimate_margin_report(est)['lowest']['margin_pct'] == 10.0


def test_a_below_floor_estimate_cannot_be_shared_by_a_rep(client, monkeypatch, app):
    monkeypatch.setattr(A, '_margin_floors', lambda: (35.0, 30.0))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: False)
    est = _est(10000.0, 9000.0)          # 10% margin
    est['estimate_id'] = 'floor-block'
    A.est_save(est)
    r = client.post('/api/estimates/floor-block/share')
    assert r.status_code == 403
    assert '10.0% margin' in r.get_json()['error']
    A.est_delete('floor-block')


def test_a_manager_may_send_it_anyway(client, monkeypatch):
    monkeypatch.setattr(A, '_margin_floors', lambda: (35.0, 30.0))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: True)
    est = _est(10000.0, 9000.0)
    est['estimate_id'] = 'floor-mgr'
    A.est_save(est)
    assert client.post('/api/estimates/floor-mgr/share').status_code == 200
    A.est_delete('floor-mgr')


def test_an_estimate_between_the_two_floors_warns_but_still_sends(client, monkeypatch):
    """32% is under the 35 target and over the 30 floor: the rep sees an amber
    banner, and the send is not the place that argues about it."""
    monkeypatch.setattr(A, '_margin_floors', lambda: (35.0, 30.0))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: False)
    est = _est(10000.0, 6800.0)          # 32%
    est['estimate_id'] = 'floor-mid'
    A.est_save(est)
    assert A.estimate_margin_report(est)['lowest']['margin_pct'] == 32.0
    assert client.post('/api/estimates/floor-mid/share').status_code == 200
    A.est_delete('floor-mid')


def test_a_healthy_margin_sends_without_comment(client, monkeypatch):
    monkeypatch.setattr(A, '_margin_floors', lambda: (35.0, 30.0))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: False)
    est = _est(10000.0, 5000.0)          # 50%
    est['estimate_id'] = 'floor-ok'
    A.est_save(est)
    assert client.post('/api/estimates/floor-ok/share').status_code == 200
    A.est_delete('floor-ok')


@pytest.mark.parametrize('est_type', ['insurance', 'commercial'])
def test_the_floor_is_residential_only(est_type, monkeypatch):
    """Insurance: the carrier sets that price. Commercial: its pricing comes
    off a per-job supplier quote and the catalog ships $0 placeholder costs, so
    a floor would be measuring the placeholders rather than the job."""
    monkeypatch.setattr(A, '_margin_floors', lambda: (35.0, 30.0))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: False)
    est = _est(10000.0, 9900.0, est_type=est_type)
    assert A._margin_floor_block(est) == (None, None)


def test_an_insurance_trade_on_a_retail_estimate_is_also_exempt(monkeypatch):
    monkeypatch.setattr(A, '_margin_floors', lambda: (35.0, 30.0))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: False)
    est = _est(10000.0, 9900.0)
    est['trades']['insurance'] = {'enabled': True}
    assert A._margin_floor_block(est) == (None, None)


def test_retail_is_not_exempt(monkeypatch):
    monkeypatch.setattr(A, '_margin_floors', lambda: (35.0, 30.0))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: False)
    msg, worst = A._margin_floor_block(_est(10000.0, 9900.0))
    assert msg and worst['margin_pct'] == 1.0


def test_the_shipped_floors_are_the_agreed_numbers():
    """Warn deliberately equals DEFAULT_RATE: a rep who never touches the
    margin box sits exactly on target, so the banner only appears because
    someone moved it down."""
    assert A.MARGIN_FLOOR_WARN_DEFAULT == 35.0 == A.DEFAULT_RATE
    assert A.MARGIN_FLOOR_BLOCK_DEFAULT == 30.0


def test_the_front_end_mirrors_the_shipped_floors():
    src = open(APP_JS, encoding='utf-8').read()
    body = _fn('marginFloors')
    assert 'margin_floor_warn,  35' in body
    assert 'margin_floor_block, 30' in body


def test_a_junk_floor_setting_falls_back_rather_than_blocking_everything(monkeypatch, tmp_path):
    """A fat-fingered 500 in Settings must not stop every send in the company."""
    f = tmp_path / 'app_settings.json'
    f.write_text(json.dumps({'margin_floor_warn': 'abc', 'margin_floor_block': 500}))
    monkeypatch.setattr(A, 'APP_SETTINGS_FILE', str(f))
    assert A._margin_floors() == (A.MARGIN_FLOOR_WARN_DEFAULT,
                                  A.MARGIN_FLOOR_BLOCK_DEFAULT)


def test_a_zero_floor_switches_the_block_off(monkeypatch, tmp_path):
    f = tmp_path / 'app_settings.json'
    f.write_text(json.dumps({'margin_floor_block': 0}))
    monkeypatch.setattr(A, 'APP_SETTINGS_FILE', str(f))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: False)
    assert A._margin_floor_block(_est(10000.0, 9900.0)) == (None, None)


def test_the_cost_mirror_follows_the_sell_side_line_for_line():
    """A cost that counts a line the sell total skipped reports a margin the
    customer is never actually offered. Zero-qty is the case that bites."""
    est = {
        'estimate_id': 'x', 'pricing': {'mode': 'margin'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'gbb',
            'line_items': [
                {'name': 'In scope', 'quantity': 1, 'tiers': {
                    'good': {'material_unit_cost': 100.0, 'labor_unit_cost': 0}}},
                {'name': 'Not in scope', 'quantity': 0, 'tiers': {
                    'good': {'material_unit_cost': 9999.0, 'labor_unit_cost': 0}}},
                {'name': 'Excluded here', 'quantity': 1, 'tiers': {
                    'good': {'material_unit_cost': 8888.0, 'labor_unit_cost': 0,
                             'included': False}}},
            ],
        }},
    }
    assert A._trade_cost_subtotal(est, 'roofing', 'good') == 100.0


# ── 3. The 100% cliff ─────────────────────────────────────────────────────

def test_a_margin_of_100_is_rejected_on_save(client):
    """It does not error today — it prices every line at $0, which looks
    completely normal on screen."""
    r = client.put('/api/estimates/rate-100', json={
        'estimate_id': 'rate-100',
        'pricing': {'mode': 'margin', 'tier_rates': {'good': 100}},
    })
    assert r.status_code == 400
    assert 'tier_rates.good' in r.get_json()['fields']


def test_markup_has_no_such_limit(client):
    """A 150% markup is perfectly ordinary."""
    r = client.put('/api/estimates/markup-150', json={
        'estimate_id': 'markup-150',
        'pricing': {'mode': 'markup', 'tier_rates': {'good': 150}},
    })
    assert r.status_code == 200
    A.est_delete('markup-150')


def test_every_rate_slot_is_checked():
    bad = A._invalid_rates({
        'mode': 'margin',
        'global_rate': 100,
        'tier_rates': {'best': 120},
        'per_trade_overrides': {'siding': 100.5},
        'trade_rates': {'roofing': {'good': 999}},
    })
    assert set(bad) == {'global_rate', 'tier_rates.best',
                        'per_trade_overrides.siding', 'trade_rates.roofing.good'}


def test_ninety_nine_still_saves(client):
    r = client.put('/api/estimates/rate-99', json={
        'estimate_id': 'rate-99',
        'pricing': {'mode': 'margin', 'tier_rates': {'good': 99}},
    })
    assert r.status_code == 200
    A.est_delete('rate-99')


def test_the_rate_inputs_cannot_offer_one_hundred():
    """max="100" on the per-trade override admitted exactly the value that
    prices every line at $0."""
    src = open(APP_JS, encoding='utf-8').read()
    for m in re.finditer(r'<input type="number" min="0" max="(\d+)"[^>]*'
                         r'(setTierRate|setTradeOverride|setTradeTierRate)', src):
        assert int(m.group(1)) < 100, f'{m.group(2)} input offers {m.group(1)}%'
