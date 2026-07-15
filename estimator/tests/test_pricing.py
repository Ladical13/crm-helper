"""Pricing engine rules.

The rate-resolution rule these tests pin down:

    per-trade override -> tier rate -> global rate -> 35 (house default)

A source counts as "set" only if it parses as a number; 0 counts (a rep really
can sell at cost), but None/''/garbage do not and fall through to the next
source. Falling through to 35 rather than 0 matters: a 0% default would
silently sell a roof at cost.

app.js MUST agree with all of this — see test_parity.py.
"""
import pytest


# ── rate resolution ────────────────────────────────────────────────────

def test_override_wins_over_tier_and_global(A):
    p = {'per_trade_overrides': {'roofing': 50}, 'tier_rates': {'better': 35}, 'global_rate': 20}
    assert A._tier_rate(p, 'roofing', 'better') == 50.0


def test_tier_rate_wins_over_global(A):
    assert A._tier_rate({'global_rate': 20, 'tier_rates': {'better': 35}}, 'roofing', 'better') == 35.0


def test_falls_back_to_global(A):
    assert A._tier_rate({'global_rate': 20}, 'roofing', 'better') == 20.0


def test_explicit_zero_is_honored_at_every_level(A):
    """0 means "sell at cost" — a real choice, not a missing value."""
    assert A._tier_rate({'per_trade_overrides': {'roofing': 0}, 'global_rate': 35},
                        'roofing', 'better') == 0.0
    assert A._tier_rate({'tier_rates': {'better': 0}, 'global_rate': 35},
                        'roofing', 'better') == 0.0
    assert A._tier_rate({'global_rate': 0}, 'roofing', 'better') == 0.0


@pytest.mark.parametrize('pricing', [
    {},
    {'global_rate': None},
    {'global_rate': ''},
    {'global_rate': 'abc'},
])
def test_missing_or_junk_global_defaults_to_35_never_zero(A, pricing):
    """A 0% fallback would sell at cost. The house default is 35%."""
    assert A._tier_rate(pricing, 'roofing', 'better') == 35.0


@pytest.mark.parametrize('junk', [None, '', 'abc'])
def test_junk_override_falls_through_to_tier_rate(A, junk):
    p = {'per_trade_overrides': {'roofing': junk}, 'tier_rates': {'better': 35}}
    assert A._tier_rate(p, 'roofing', 'better') == 35.0


@pytest.mark.parametrize('junk', [None, '', 'abc'])
def test_junk_tier_rate_falls_through_to_global(A, junk):
    assert A._tier_rate({'tier_rates': {'better': junk}, 'global_rate': 20},
                        'roofing', 'better') == 20.0


# ── sell price ─────────────────────────────────────────────────────────

def test_margin_mode(A):
    assert A._sell_price(65, 35, 'margin') == pytest.approx(100.0)


def test_markup_mode(A):
    assert A._sell_price(100, 50, 'markup') == 150.0


@pytest.mark.parametrize('rate', [100, 120])
def test_margin_at_or_above_100_yields_zero_not_a_crash(A, rate):
    """Guards divide-by-zero / negative price. Mirrors app.js."""
    assert A._sell_price(100, rate, 'margin') == 0.0


def test_zero_margin_sells_at_cost(A):
    assert A._sell_price(100, 0, 'margin') == 100.0


# ── line / trade totals ────────────────────────────────────────────────

def _est(trades, pricing=None):
    return {'pricing': pricing or {'mode': 'margin', 'tier_rates': {'good': 30, 'better': 35, 'best': 40}},
            'trades': trades}


def _gbb_item(qty, costs, **kw):
    it = {'name': 'x', 'quantity': qty, 'tiers': {
        t: {'material_unit_cost': c[0], 'labor_unit_cost': c[1]} for t, c in costs.items()}}
    it.update(kw)
    return it


def test_trade_subtotal_margin_math(A):
    e = _est({'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
        _gbb_item(30, {'better': (130, 55)})]}})
    # 30 * (130+55) / (1 - 0.35)
    assert A.calc_tier_total(e, 'better') == pytest.approx(30 * 185 / 0.65, abs=0.01)


def test_zero_qty_never_prices_even_with_price_override(A):
    """Zero qty = "not in scope". The customer view hides it, so the total must
    agree — even when a price_override is locked on the line."""
    e = _est({'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
        {'name': 'x', 'quantity': 0, 'tiers': {'better': {'price_override': 5000}}}]}})
    assert A.calc_tier_total(e, 'better') == 0.0


def test_price_override_is_a_line_total_not_a_unit_price(A):
    e = _est({'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
        {'name': 'x', 'quantity': 3, 'tiers': {'better': {'price_override': 2000}}}]}})
    assert A.calc_tier_total(e, 'better') == 2000.0


def test_included_false_excludes_line_from_that_tier(A):
    e = _est({'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
        {'name': 'x', 'quantity': 5, 'tiers': {
            'better': {'included': False},
            'best': {'material_unit_cost': 20, 'labor_unit_cost': 0}}}]}})
    assert A.calc_tier_total(e, 'better') == 0.0
    assert A.calc_tier_total(e, 'best') == pytest.approx(5 * 20 / 0.60, abs=0.01)


def test_disabled_trade_contributes_nothing(A):
    e = _est({'roofing': {'enabled': False, 'mode': 'gbb', 'line_items': [
        _gbb_item(30, {'better': (130, 55)})]}})
    assert A.calc_tier_total(e, 'better') == 0.0


def test_simple_mode_uses_unit_price_and_ignores_tier(A):
    e = _est({'gutters': {'enabled': True, 'mode': 'simple', 'line_items': [
        {'name': 'g', 'quantity': 100, 'unit_price': 9}]}})
    assert A.calc_tier_total(e, 'good') == 900.0
    assert A.calc_tier_total(e, 'best') == 900.0


# ── per-trade tier selection (mix-and-match) ───────────────────────────

def test_trade_tier_prefers_signature_over_estimate(A):
    e = {'signature': {'selected_tiers': {'roofing': 'best'}},
         'selected_tiers': {'roofing': 'good'},
         'trades': {'roofing': {'selected_tier': 'better'}}}
    assert A._trade_tier(e, 'roofing') == 'best'


def test_trade_tier_falls_back_through_the_chain(A):
    assert A._trade_tier({'selected_tiers': {'roofing': 'good'}, 'trades': {}}, 'roofing') == 'good'
    assert A._trade_tier({'trades': {'roofing': {'selected_tier': 'best'}}}, 'roofing') == 'best'
    assert A._trade_tier({'selected_tier': 'good', 'trades': {}}, 'roofing') == 'good'  # legacy doc
    assert A._trade_tier({'trades': {}}, 'roofing') == 'better'                          # default


def test_trade_tier_rejects_garbage(A):
    assert A._trade_tier({'trades': {'roofing': {'selected_tier': 'platinum'}}}, 'roofing') == 'better'


def test_selected_total_prices_each_trade_at_its_own_tier(A):
    e = _est({
        'roofing': {'enabled': True, 'mode': 'gbb', 'selected_tier': 'best',
                    'line_items': [_gbb_item(10, {'best': (100, 0), 'good': (50, 0)})]},
        'siding': {'enabled': True, 'mode': 'gbb', 'selected_tier': 'good',
                   'line_items': [_gbb_item(10, {'best': (100, 0), 'good': (50, 0)})]},
    })
    expected = (10 * 100 / 0.60) + (10 * 50 / 0.70)
    assert A.calc_selected_total(e) == pytest.approx(expected, abs=0.01)


def test_legacy_doc_without_tier_rates_prices_off_global_rate(A):
    """Estimates written before per-tier margins existed have only global_rate.
    They must price off it at every tier — no migration required, and no silent
    fall to a 0% rate. (app.js's migrateState seeds tier_rates client-side, but
    the server renders PDFs and the customer view without ever running it.)"""
    legacy = {'pricing': {'mode': 'margin', 'global_rate': 35},
              'selected_tier': 'better',
              'trades': {'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
                  _gbb_item(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}
    expected = 10 * 100 / 0.65
    for tier in ('good', 'better', 'best'):
        assert A.calc_tier_total(legacy, tier) == pytest.approx(expected, abs=0.01)
    assert A.calc_selected_total(legacy) == pytest.approx(expected, abs=0.01)


def test_backfilling_tier_rates_is_a_no_op_for_legacy_docs(A):
    """Seeding tier_rates from global_rate must not move any number — this is
    why the legacy estimates on disk were left alone rather than migrated."""
    base = {'pricing': {'mode': 'margin', 'global_rate': 35},
            'trades': {'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
                _gbb_item(30, {'good': (100, 50), 'better': (130, 55), 'best': (170, 60)})]}}}
    migrated = {'pricing': {'mode': 'margin', 'global_rate': 35,
                            'tier_rates': {'good': 35, 'better': 35, 'best': 35}},
                'trades': base['trades']}
    for tier in ('good', 'better', 'best'):
        assert A.calc_tier_total(base, tier) == pytest.approx(
            A.calc_tier_total(migrated, tier), abs=0.01)


def test_render_line_items_total_matches_the_engine(A):
    """The customer-facing page must never show a total the engine disagrees with."""
    e = _est({'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
        _gbb_item(30, {'good': (100, 50), 'better': (130, 55), 'best': (170, 60)})]}})
    _html, grand = A.render_line_items(e, 'better')
    assert grand == pytest.approx(A.calc_tier_total(e, 'better'), abs=0.01)
