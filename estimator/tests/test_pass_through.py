"""Fees charged at cost: the shingle delivery and the permit.

Two charges were on no estimate at all. The supplier's delivery fee and the
city's permit both came out of the job's margin every time - $200 of handling
on CO-10059, and a permit the price book carried as a $0 placeholder on a
package that had dropped the row anyway.

Luke's rule for both: charge them, at exactly what they cost. No margin on
either - "I don't want to make money on it" - the delivery hidden from the
customer, the permit priced at the average actually paid on Colorado retail
roofs rather than looked up per city.

Three things have to hold, and each fails silently if it does not:

* A no-margin line sells at its cost on BOTH sides. This is pricing math, so
  it is in the browser and the server and the parity suite carries fixtures
  for it; the tests here are the ones about what the rule is FOR.
* The margin PERCENTAGE ignores these lines. A roof priced exactly on the 35%
  target would otherwise read 33% and trip the floor's warning on every job,
  for a fee nobody was trying to make money on. The dollars still include them.
* A fee is charged once per roof, and not at all before there is a roof. A
  blank estimate that totals $500 of fees is no longer blank, and "nothing
  priced" is how a report-only estimate is recognised.
"""
import json
import os

import pytest

import app as A
from test_pack_pricing import (LA_TOUCHE, PER_UNIT, _catalog, _estimate, _lines,
                               _run, needs_node)

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'static', 'app.js')

DELIVERY = {'id': 'x_shingle_delivery', 'name': 'Shingle Delivery', 'unit': 'EA',
            'cost': 225, 'measure': 'roof_job', 'no_margin': True,
            'customer_visible': False, 'bullets': []}
PERMIT = {'id': 'x_permit', 'name': 'Permit', 'unit': 'LS', 'cost': 275,
          'measure': 'roof_job', 'no_margin': True}
BID_COST = 2918.89 + 14.4 * 145          # CO-10059 as bid: material + labor
BID_SELL = 8344.82                       # ...at 40%


def _book(fees=(DELIVERY, PERMIT)):
    cat = _catalog(packed=False) + [dict(f) for f in fees]
    ids = [p['id'] for p in cat]
    return {'roofing_catalog': cat,
            'roofing_bundles': [{'id': 'b_iko_nordic', 'name': 'IKO Nordic',
                                 'product_ids': ids}],
            'roofing_tier_defaults': {'good': 'b_iko_nordic', 'better': 'b_iko_nordic',
                                      'best': 'b_iko_nordic'}}


PICK_GOOD = [{'op': 'applyBundle', 'trade': 'roofing', 'tier': 'good', 'id': 'b_iko_nordic'}]


def _good_only(measurements=None):
    est = _estimate(measurements=measurements)
    est['tiers_enabled'] = {'good': True, 'better': False, 'best': False}
    return est


# ── the roof that started this, with its two fees ──────────────────────────

@needs_node
def test_the_fees_are_added_at_cost_on_top_of_the_marked_up_roof(tmp_path):
    """CO-10059 again: $5,006.89 of roof at 40% is the $8,344.82 she signed.
    With the two fees it is that plus $500 - not plus $500 / 0.6 = $833."""
    res = _run(tmp_path, PICK_GOOD + [
        {'op': 'tierTotals', 'trade': 'roofing', 'tier': 'good'},
        {'op': 'lineSell', 'trade': 'roofing', 'tier': 'good', 'id': 'x_shingle_delivery'},
        {'op': 'lineSell', 'trade': 'roofing', 'tier': 'good', 'id': 'x_permit'},
    ], estimate=_good_only(), book=_book())
    lines = _lines(res)
    assert lines['x_shingle_delivery']['quantity'] == 1
    assert lines['x_permit']['quantity'] == 1
    assert lines['x_shingle_delivery']['no_margin'] is True
    assert lines['x_shingle_delivery']['customer_visible'] is False
    totals, delivery, permit = res['probes']
    assert (delivery, permit) == (225, 275)
    assert totals['cost'] == pytest.approx(BID_COST + 500, abs=0.01)
    assert totals['sell'] == pytest.approx(BID_SELL + 500, abs=0.01)
    # The server prices the browser's own estimate to the same cent.
    assert A.calc_tier_total(res['S'], 'good') == pytest.approx(totals['sell'], abs=0.01)


@needs_node
def test_the_margin_percentage_is_taken_without_them(tmp_path):
    """Sell $8,844.82 on cost $5,506.89 is 37.7% if the fees count - under what
    the rep set, for no reason they can fix. The report says 40.0, and says it
    on both sides, because the floor's banner (browser) and the send block
    (server) have to be talking about the same number."""
    res = _run(tmp_path, PICK_GOOD + [{'op': 'marginReport'}],
               estimate=_good_only(), book=_book())
    js = res['probes'][0]['tiers'][0]
    assert js['margin_pct'] == pytest.approx(40.0, abs=0.01)
    assert js['pass_through'] == 500
    py = A.estimate_margin_report(res['S'])['tiers'][0]
    assert py['margin_pct'] == 40.0 and py['pass_through'] == 500.0
    # The dollars are whole on both sides - nothing was taken out of them.
    assert py['sell'] == pytest.approx(js['sell'], abs=0.01) == pytest.approx(BID_SELL + 500, abs=0.01)
    assert py['cost'] == pytest.approx(js['cost'], abs=0.01) == pytest.approx(BID_COST + 500, abs=0.01)


def test_a_job_priced_exactly_on_the_floor_is_not_pushed_under_it():
    """The warn floor equals the default rate on purpose, so a rep who never
    touches the margin box sits exactly on target. Two fees at cost must not
    be what tips that into a warning on every estimate the company writes."""
    est = {'estimate_type': 'retail', 'selected_tier': 'good',
           'tiers_enabled': {'good': True, 'better': False, 'best': False},
           'pricing': {'mode': 'margin', 'global_rate': 35},
           'trades': {'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
               {'name': 'Roof', 'quantity': 20,
                'tiers': {'good': {'material_unit_cost': 250, 'labor_unit_cost': 0}}},
               {'name': 'Shingle Delivery', 'quantity': 1, 'no_margin': True,
                'tiers': {'good': {'material_unit_cost': 225, 'labor_unit_cost': 0}}},
               {'name': 'Permit', 'quantity': 1, 'no_margin': True,
                'tiers': {'good': {'material_unit_cost': 275, 'labor_unit_cost': 0}}}]}}}
    row = A.estimate_margin_report(est)['tiers'][0]
    assert row['margin_pct'] == 35.0
    assert row['sell'] == round(5000 / 0.65 + 500, 2)
    warn, block = A._margin_floors()
    assert row['margin_pct'] >= warn > block
    # ...and without the flag the same two lines are marked up like any other.
    for it in est['trades']['roofing']['line_items']:
        it.pop('no_margin', None)
    assert A.estimate_margin_report(est)['tiers'][0]['sell'] == round(5500 / 0.65, 2)


@pytest.mark.parametrize('mode,rate,want', [('margin', 40, 225.0), ('markup', 40, 225.0),
                                            ('margin', 0, 225.0), ('margin', 100, 225.0)])
def test_no_margin_means_cost_in_either_mode_at_any_rate(mode, rate, want):
    """Including the 100% margin that prices an ordinary line at $0."""
    item = {'quantity': 1, 'no_margin': True,
            'tiers': {'good': {'material_unit_cost': 200, 'labor_unit_cost': 25}}}
    assert A._line_sell_total(item, 'good', rate, mode) == want


def test_only_a_literal_true_is_no_margin_and_a_locked_price_still_wins():
    cell = {'material_unit_cost': 200, 'labor_unit_cost': 0}
    for junk in (None, False, 0, 1, 'true', 'yes'):
        item = {'quantity': 1, 'no_margin': junk, 'tiers': {'good': dict(cell)}}
        assert A._line_sell_total(item, 'good', 50, 'margin') == 400.0, junk
    locked = {'quantity': 1, 'no_margin': True,
              'tiers': {'good': dict(cell, price_override=310)}}
    assert A._line_sell_total(locked, 'good', 50, 'margin') == 310.0
    js = open(APP_JS, encoding='utf-8').read()
    assert 'function isNoMargin(item) { return !!item && item.no_margin === true; }' in js


# ── once per roof, and not before there is one ─────────────────────────────

@needs_node
def test_a_roof_with_no_squares_carries_no_fees(tmp_path):
    """Every new estimate ships with Roofing on and its packages loaded. If the
    fees priced there, a blank estimate would total $500 - and an estimate
    whose only content is a condition report would stop being priced by it."""
    res = _run(tmp_path, PICK_GOOD + [{'op': 'tierTotals', 'trade': 'roofing', 'tier': 'good'}],
               estimate=_good_only(measurements={}), book=_book())
    lines = _lines(res)
    assert lines['x_shingle_delivery']['quantity'] == 0
    assert lines['x_permit']['quantity'] == 0
    assert res['probes'][0] == {'sell': 0, 'cost': 0}
    assert not A._has_priced_scope(res['S'])


@needs_node
@pytest.mark.parametrize('m,want', [({'roof_squares': 12.57}, 1), ({'roof_squares': 60}, 1),
                                    ({'roof_squares': 0}, 0), ({}, 0),
                                    ({'roof_squares': ''}, 0)])
def test_one_per_roof_job(tmp_path, m, want):
    res = _run(tmp_path, [{'op': 'measure', 'item': {'unit': 'EA', 'measure': 'roof_job'}}],
               estimate=_estimate(measurements=m))
    assert res['probes'] == [want]


def test_the_audit_knows_the_measure_and_accepts_a_lump_sum_counted_once():
    assert A.MEASURE_DIMENSIONS['roof_job'] == 'EA'
    pb = {'roofing_catalog': [dict(PERMIT), dict(DELIVERY)],
          'roofing_bundles': [{'id': 'b', 'name': 'B',
                               'product_ids': ['x_permit', 'x_shingle_delivery']}]}
    assert A.pricebook_audit(pb)['findings'] == []


# ── every way a line can be built or re-priced ─────────────────────────────

@needs_node
def test_a_single_price_trade_stores_the_fee_at_its_cost(tmp_path):
    """Simple mode stores its sell price rather than deriving it, so the rule
    has to be applied when the price is written."""
    est = _good_only()
    est['trades']['roofing']['mode'] = 'simple'
    res = _run(tmp_path, [{'op': 'applySimpleBundle', 'trade': 'roofing', 'id': 'b_iko_nordic'}],
               estimate=est, book=_book())
    lines = _lines(res)
    for pid, cost in (('x_shingle_delivery', 225), ('x_permit', 275)):
        assert (lines[pid]['unit_cost'], lines[pid]['unit_price']) == (cost, cost), pid
    shingle = lines['m_iko_nordic']
    assert shingle['unit_price'] == pytest.approx(shingle['unit_cost'] / 0.6, abs=0.01)
    assert A._trade_pass_through(res['S'], 'roofing', 'good') == (500.0, 500.0)


@needs_node
def test_switching_a_trade_to_one_price_and_back_keeps_the_fee_at_cost(tmp_path):
    res = _run(tmp_path, PICK_GOOD + [{'op': 'setMode', 'trade': 'roofing', 'mode': 'simple'}],
               estimate=_good_only(), book=_book())
    fee = _lines(res)['x_permit']
    assert fee['no_margin'] is True and fee['unit_price'] == 275
    back = _run(tmp_path, PICK_GOOD + [{'op': 'setMode', 'trade': 'roofing', 'mode': 'simple'},
                                       {'op': 'setMode', 'trade': 'roofing', 'mode': 'gbb'},
                                       {'op': 'lineSell', 'trade': 'roofing', 'tier': 'good',
                                        'id': 'x_permit'}],
                estimate=_good_only(), book=_book())
    assert _lines(back)['x_permit']['no_margin'] is True
    assert back['probes'][-1] == 275


@needs_node
def test_repicking_a_package_on_an_older_estimate_brings_the_fees_in(tmp_path):
    """The La Touche estimate itself: a Permit row at quantity 0 and $0, built
    when the product was a placeholder with no measure. Re-pick the package and
    it becomes one permit at cost; the delivery row arrives beside it."""
    est = _good_only()
    est['trades']['roofing']['line_items'] = [
        {'id': 'old', 'catalog_id': 'x_permit', 'name': 'Permit', 'unit': 'LS',
         'quantity': 0, 'customer_visible': True,
         'tiers': {'good': {'material_unit_cost': 0, 'labor_unit_cost': 0, 'included': True}}}]
    res = _run(tmp_path, PICK_GOOD + [{'op': 'lineSell', 'trade': 'roofing', 'tier': 'good',
                                       'id': 'x_permit'}],
               estimate=est, book=_book())
    permit = _lines(res)['x_permit']
    assert permit['id'] == 'old'                       # adopted, not duplicated
    assert (permit['quantity'], permit['no_margin']) == (1, True)
    assert res['probes'][-1] == 275
    assert _lines(res)['x_shingle_delivery']['quantity'] == 1


@needs_node
def test_a_fee_the_manager_unticks_goes_back_to_carrying_margin(tmp_path):
    """The flag follows the Price Book when a package is re-picked, both ways."""
    marked_up = dict(PERMIT, no_margin=False)
    res = _run(tmp_path, PICK_GOOD + [
        {'op': 'setPriceBook', 'priceBook': _book((DELIVERY, marked_up))},
    ] + PICK_GOOD + [{'op': 'lineSell', 'trade': 'roofing', 'tier': 'good', 'id': 'x_permit'}],
        estimate=_good_only(), book=_book())
    assert 'no_margin' not in _lines(res)['x_permit']
    assert res['probes'][-1] == pytest.approx(275 / 0.6, abs=0.01)


@needs_node
def test_the_insurance_cost_sheet_counts_them_too(tmp_path):
    """On an insurance job the carrier sets the price and the margin is what is
    left after we build the roof. A delivery and a permit are part of building
    it, so the derived cost sheet carries both."""
    res = _run(tmp_path, [{'op': 'insuranceCostItems', 'id': 'b_iko_nordic'}],
               estimate=_good_only(), book=_book())
    by_id = {i['catalog_id']: i for i in res['probes'][0]}
    assert (by_id['x_shingle_delivery']['quantity'], by_id['x_shingle_delivery']['unit_cost']) == (1, 225)
    assert (by_id['x_permit']['quantity'], by_id['x_permit']['unit_cost']) == (1, 275)


def test_every_line_builder_carries_the_flag():
    """The same ten builders that carry the pack. A fee that reaches retail and
    misses one of them is marked up there and nowhere else."""
    import re
    js = open(APP_JS, encoding='utf-8').read()
    # (The optional-upgrade sizer builds a throwaway item only to MEASURE it;
    # it prices through lineTotal, which is handed the product's flag directly.)
    builders = [(v, rest) for v, rest in re.findall(r'\.\.\.packOf\((\w+)\),([^\n]*)', js)
                if not rest.lstrip().startswith('formula:')]
    assert len(builders) >= 8
    assert 'lineTotal(q, unitCost, 0, trade, S.selected_tier, p.no_margin === true)' in js
    for var, rest in builders:
        assert f'...marginOf({var})' in rest, f'packOf({var}) without marginOf({var})'
    carry = js[js.index('function _carryItemIdentity('):]
    assert "'no_margin'" in carry[:carry.index('\n}\n')]
    for fn in ('applyBundleToTier', 'liSwapVariant'):
        body = js[js.index('function ' + fn + '('):]
        assert 'item.no_margin = true; else delete item.no_margin;' in body[:body.index('\n}\n')], fn


# ── what the customer sees ─────────────────────────────────────────────────

def _signed_roof():
    def line(name, qty, cost, **kw):
        it = {'name': name, 'unit': 'SQ', 'quantity': qty,
              'tiers': {'good': {'material_unit_cost': cost, 'labor_unit_cost': 0,
                                 'included': True, 'description': ''}}}
        it.update(kw)
        return it
    return {'estimate_type': 'retail', 'selected_tier': 'good', 'page_visibility': {},
            'tiers_enabled': {'good': True, 'better': False, 'best': False},
            'pricing': {'mode': 'margin', 'tier_rates': {'good': 40}},
            'measurements': dict(LA_TOUCHE),
            'trades': {'roofing': {'enabled': True, 'mode': 'gbb', 'selected_tier': 'good',
                                   'line_items': [
                line('IKO Nordic (Impact-Resistant Shingle)', 14.4, 130, catalog_id='m_iko_nordic'),
                line('Install Labor', 14.4, 145, catalog_id='l_install', customer_visible=False),
                line('Shingle Delivery', 1, 225, unit='EA', catalog_id='x_shingle_delivery',
                     no_margin=True, customer_visible=False),
                line('Permit', 1, 275, unit='LS', catalog_id='x_permit', no_margin=True)]}}}


def test_the_customer_never_sees_the_delivery_and_pays_for_it_once():
    """Hidden like labor: no row, no name, and its $225 rides inside the
    shingle row so the rows still add up to the total. The permit keeps its
    own row, at exactly what it costs."""
    est = _signed_roof()
    html, total = A.render_line_items(est)
    assert 'Delivery' not in html and 'Install Labor' not in html
    assert '>Permit' in html and '$275.00' in html
    marked_up = (14.4 * 130 + 14.4 * 145) / 0.6
    assert total == pytest.approx(marked_up + 500, abs=0.01)
    assert A.fc(marked_up + 225) in html          # the shingle row carries labor + delivery
    assert A.calc_selected_total(est) == pytest.approx(total, abs=0.01)


def test_the_delivery_says_nothing_on_the_package_card():
    seed = next(p for p in A.ROOFING_CATALOG_SEED if p['id'] == 'x_shingle_delivery')
    assert seed['customer_visible'] is False and seed['bullets'] == []
    assert (seed['cost'], seed['measure'], seed['no_margin']) == (225, 'roof_job', True)


# ── reaching the live price book ───────────────────────────────────────────

SHINGLE_PACKAGES = ('b_landmark', 'b_northgate', 'b_iko_nordic', 'b_4904-41ab-8d42-mrz5363f')
OTHER_ROOF_PACKAGES = ('b_edco', 'b_stone', 'b_euroshield', 'b_standing_seam', 'b_pbr')


def _live_book(**permit):
    """The shape production holds: a Permit product saved as a $0 placeholder
    with no measure, and nine packages that had all dropped it."""
    p = {'id': 'x_permit', 'name': 'Permit', 'unit': 'LS', 'cost': 0, 'cost_class': 'material'}
    p.update(permit)
    pb = {'roofing_catalog': [{'id': 'm_iko_nordic', 'name': 'IKO Nordic', 'unit': 'SQ',
                               'cost': 130, 'measure': 'squares_waste'}, p],
          'roofing_bundles': [{'id': bid, 'name': bid, 'product_ids': ['m_iko_nordic']}
                              for bid in SHINGLE_PACKAGES + OTHER_ROOF_PACKAGES],
          'roofing_tier_defaults': {}}
    A._ensure_bundle_catalogs(pb)
    return pb


def test_a_live_book_gets_both_fees_on_the_right_packages():
    pb = _live_book()
    cat = {p['id']: p for p in pb['roofing_catalog']}
    permit, delivery = cat['x_permit'], cat['x_shingle_delivery']
    assert (permit['cost'], permit['measure'], permit['no_margin']) == (275, 'roof_job', True)
    assert (delivery['cost'], delivery['no_margin'], delivery['customer_visible']) == (225, True, False)
    packages = {b['id']: b['product_ids'] for b in pb['roofing_bundles']}
    for bid in SHINGLE_PACKAGES:
        assert packages[bid][-2:] == ['x_permit', 'x_shingle_delivery'], bid
    for bid in OTHER_ROOF_PACKAGES:
        # Every roof needs a permit; metal and the specialty roofs do not come
        # on the shingle supplier's truck.
        assert 'x_permit' in packages[bid] and 'x_shingle_delivery' not in packages[bid], bid
    # Running it again adds nothing twice.
    A._ensure_bundle_catalogs(pb)
    assert all(ids.count('x_permit') == 1 for ids in
               (b['product_ids'] for b in pb['roofing_bundles']))


def test_a_managers_own_permit_settings_are_left_alone():
    """The $0 is rewritten because $0 is the placeholder nobody chose. A price
    someone typed, a measure they set to Manual and an At cost box they
    unticked are all decisions, and all three stay."""
    priced = {p['id']: p for p in _live_book(cost=310)['roofing_catalog']}['x_permit']
    assert priced['cost'] == 310
    manual = {p['id']: p for p in _live_book(measure='')['roofing_catalog']}['x_permit']
    assert manual['measure'] == ''
    off = {p['id']: p for p in _live_book(no_margin=False)['roofing_catalog']}['x_permit']
    assert off['no_margin'] is False
    assert 'no_margin' in A._PRODUCT_BACKFILL_FIELDS
    js = open(APP_JS, encoding='utf-8').read()
    assert "else if (field === 'no_margin') it.no_margin = !!val;" in js


def test_a_fresh_book_ships_them_too():
    seed = {b['id']: b['product_ids'] for b in A.ROOFING_BUNDLES_SEED}
    for bid in ('b_landmark', 'b_northgate', 'b_iko_nordic'):
        assert 'x_permit' in seed[bid] and 'x_shingle_delivery' in seed[bid], bid
    for bid in ('b_edco', 'b_stone', 'b_euroshield'):
        assert 'x_permit' in seed[bid] and 'x_shingle_delivery' not in seed[bid], bid


# ── analytics ──────────────────────────────────────────────────────────────

@pytest.fixture
def empty_store():
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)


def test_analytics_counts_the_dollars_and_leaves_them_out_of_the_margin(client, empty_store):
    """A $10,000 roof costing $6,000 is a 40% job. Add $500 of fees at cost and
    the revenue is $10,500 - but the margin is still 40%, on the month, the
    trade, the rep and the headline tile alike."""
    from portal import clock
    A.est_save({
        'estimate_id': 'pt-1', 'salesperson': 'luke', 'estimate_type': 'retail',
        'customer': {'name': 'Test', 'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'signature': {'signed_at': clock.company_today().strftime('%Y-%m-%dT18:00:00Z'),
                      'name': 'Test'},
        'trades': {'roofing': {'enabled': True, 'mode': 'simple', 'line_items': [
            {'name': 'Roof', 'quantity': 1, 'unit_price': 10000, 'unit_cost': 6000},
            {'name': 'Shingle Delivery', 'quantity': 1, 'unit_price': 225, 'unit_cost': 225,
             'no_margin': True},
            {'name': 'Permit', 'quantity': 1, 'unit_price': 275, 'unit_cost': 275,
             'no_margin': True}]}}})
    body = client.get('/api/analytics').get_json()
    roofing = body['by_trade']['roofing']
    assert (roofing['revenue'], roofing['cost'], roofing['margin_pct']) == (10500, 6500, 40.0)
    assert body['by_rep']['luke']['margin_pct'] == 40.0
    assert body['kpis']['margin_pct'] == 40.0
    months = [m for m in body['monthly'] if m.get('margin_pct') is not None]
    assert months and months[-1]['margin_pct'] == 40.0
    assert '_pt_sell' not in json.dumps(body) and '_pt_cost' not in json.dumps(body)
