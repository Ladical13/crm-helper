"""Pack pricing: estimate for what is actually ordered.

The price book priced by the foot and the square, and a supplier sells by the
bundle, the roll and the stick. On a small roof the difference is not small.
Aimee La Touche's 12.57 SQ hip roof (CO-10059) was bid at $2,918.89 of
material; the same eight lines cost $3,291.08 on the QXO invoices, because
137 LF of eave bought three 66.7 LF rolls of ice & water, 16 LF of step
flashing bought a whole bundle, and so on down the sheet.

So a product can now be BOUGHT AS a pack - what one covers, what it is called,
the waste - and from then on a line's quantity is the count of packs being
ordered and its cost is the price of one, typed straight off the invoice.

Three things here are load-bearing, and each has a test that fails without it:

* The pack, the cost and the quantity always move together. A roll size on a
  per-foot price is the $9.30 ice & water line (test_ice_water_fix.py); the
  mirror image is a per-roll price on a per-foot quantity, which re-picking a
  package on an OLDER estimate would produce if the line did not take the pack
  with the cost.
* A book nobody has converted prices exactly as it did. The first test below
  reproduces the La Touche bid to the cent off the per-foot book.
* The material order prints the count that was priced. Two documents giving
  two answers to "how many bundles" is how the first order went out short.

Quantities are computed in the browser and stored on the line; the server never
recomputes one. So the JS runs under node (pack_runner.js) and app.py is tested
only for what it does with the stored fields: the unit it prints and the order
sheet.
"""
import copy
import json
import os
import shutil
import subprocess

import pytest

import app as A

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'pack_runner.js')
APP_JS = os.path.join(os.path.dirname(HERE), 'static', 'app.js')

needs_node = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node not installed - the pack math cannot be run')

# 2415 Chase St, Structure #1 of the RoofR report, as the estimate stored it.
LA_TOUCHE = {'roof_squares': 12.57, 'waste_pct': 14, 'eave_lf': 137, 'rake_lf': 23,
             'ridge_hip_lf': 103, 'valley_lf': 0, 'step_flash_lf': 15.8,
             'pipe_boots': 2, 'low_slope_squares': 0}

# The live price book on the day of the bid: every material by the foot or the
# square. (id, name, unit, cost, measure)
PER_UNIT = [
    ('m_iko_nordic',   'IKO Nordic (Impact-Resistant Shingle)', 'SQ', 130.0, 'squares_waste'),
    ('a_underlayment', 'Synthetic Underlayment',                'SQ', 9.10,  'squares_waste'),
    ('a_ice_water',    'Ice & Water Shield',                    'LF', 1.55,  'eave_valley'),
    ('a_drip_edge',    'Drip Edge 2x4',                         'LF', 1.58,  'eave_rake'),
    ('a_ridge_cap',    'IKO Hip & Ridge Cap',                   'LF', 2.88,  'ridge_hip'),
    ('a_starter',      'Starter Strip',                         'LF', 0.58,  'eave_rake'),
    ('a_pipe_boots',   'Pipe Boots',                            'EA', 14.71, 'pipe_boots'),
    ('a_step_flash',   'Step Flashing',                         'LF', 1.99,  'step'),
]
# The same products as QXO invoices VH40477 / VJ18365 print them:
# id -> (pack price, what one covers, called, waste %).
INVOICE = {
    'm_iko_nordic':   (45.29,  1 / 3, 'bundles', 0),    # 3BDL/SQ
    'a_underlayment': (97.54,  10,    'rolls',   0),    # 10SQ/RL
    'a_ice_water':    (100.62, 66.67, 'rolls',   10),   # 2SQ RL, 36" x 66.7 ft
    'a_drip_edge':    (15.74,  9,     'sticks',  0),    # 10 ft, 9 usable
    'a_ridge_cap':    (75.58,  29.5,  'bundles', 10),   # 29.5LF/BDL
    'a_starter':      (63.48,  105,   'bundles', 0),    # 105LF
    'a_step_flash':   (119.49, 46,    'bundles', 0),    # 100 pcs, 1 per course
}
MATERIAL_IDS = [p[0] for p in PER_UNIT]


def _catalog(packed):
    out = []
    for pid, name, unit, cost, measure in PER_UNIT:
        p = {'id': pid, 'name': name, 'unit': unit, 'cost': cost, 'measure': measure}
        if packed and pid in INVOICE:
            price, cover, called, waste = INVOICE[pid]
            p.update(cost=price, bundle_lf=cover, bundle_unit=called,
                     bundle_waste_pct=waste)
        out.append(p)
    out.append({'id': 'l_install', 'name': 'Install Labor', 'unit': 'SQ', 'cost': 145.0,
                'measure': 'squares_waste', 'cost_class': 'labor',
                'customer_visible': False})
    return out


def _book(packed, simple=False):
    ids = MATERIAL_IDS + ['l_install']
    return {'roofing_catalog': _catalog(packed),
            'roofing_bundles': [{'id': 'b_iko_nordic', 'name': 'IKO Nordic',
                                 'product_ids': ids}],
            'roofing_tier_defaults': {'good': 'b_iko_nordic', 'better': 'b_iko_nordic',
                                      'best': 'b_iko_nordic'}}


def _estimate(mode='gbb', measurements=None):
    return {'estimate_type': 'retail', 'selected_tier': 'good',
            'pricing': {'mode': 'margin', 'global_rate': 35,
                        'tier_rates': {'good': 40, 'better': 40, 'best': 38}},
            'measurements': dict(LA_TOUCHE if measurements is None else measurements),
            'structures': [],
            'trades': {'roofing': {'enabled': True, 'mode': mode, 'line_items': []}}}


def _run(tmp_path, ops, estimate=None, book=None, catalog=None, suggestions=None):
    scenario = {'priceBook': book or {}, 'estimate': estimate or _estimate(),
                'catalog': catalog or [], 'suggestions': suggestions or {}, 'ops': ops}
    fx, out = tmp_path / 'scenario.json', tmp_path / 'out.json'
    fx.write_text(json.dumps(scenario), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text(encoding='utf-8'))


def _lines(res):
    return {it['catalog_id']: it for it in res['S']['trades']['roofing']['line_items']}


def _material_cost(res, tier='good'):
    total = 0.0
    for it in res['S']['trades']['roofing']['line_items']:
        if it['catalog_id'] == 'l_install':
            continue
        total += float(it['quantity']) * float(it['tiers'][tier]['material_unit_cost'])
    return round(total, 2)


BUILD = [{'op': 'applyBundle', 'trade': 'roofing', 'tier': t, 'id': 'b_iko_nordic'}
         for t in ('good', 'better', 'best')]


# ── the roof that started this ─────────────────────────────────────────────

@needs_node
def test_a_book_priced_by_the_foot_reproduces_the_bid_to_the_cent(tmp_path):
    """Nothing moves until a manager converts a product. This is the La Touche
    estimate rebuilt off the book it was written from: $2,918.89 of material
    and $2,088.00 of labor, which at 40% is the $8,344.82 she signed."""
    res = _run(tmp_path, BUILD, book=_book(packed=False))
    q = {k: v['quantity'] for k, v in _lines(res).items()}
    assert q == {'m_iko_nordic': 14.4, 'a_underlayment': 14.4, 'a_ice_water': 137,
                 'a_drip_edge': 160, 'a_ridge_cap': 103, 'a_starter': 160,
                 'a_pipe_boots': 2, 'a_step_flash': 16, 'l_install': 14.4}
    assert _material_cost(res) == 2918.89
    assert round((2918.89 + 14.4 * 145) / 0.6, 2) == 8344.82
    for it in _lines(res).values():
        assert 'bundle_lf' not in it and 'bundle_waste_pct' not in it


@needs_node
def test_priced_by_the_pack_it_lands_on_what_was_actually_bought(tmp_path):
    """The same roof off the invoice's own packs. The supplier billed $3,291.08
    for these eight lines; this says $3,305.92 - one bundle of shingles over,
    one stick of drip edge and one pipe boot under - where the bid said
    $2,918.89."""
    res = _run(tmp_path, BUILD, book=_book(packed=True))
    lines = _lines(res)
    q = {k: v['quantity'] for k, v in lines.items()}
    assert q == {'m_iko_nordic': 43,     # 14.33 SQ x 3            (bought 42)
                 'a_underlayment': 2,    # 14.33 SQ / 10           (bought 2)
                 'a_ice_water': 3,       # 137 LF + 10% / 66.67    (bought 3)
                 'a_drip_edge': 18,      # 160 LF / 9              (bought 19)
                 'a_ridge_cap': 4,       # 103 LF + 10% / 29.5     (bought 4)
                 'a_starter': 2,         # 160 LF / 105            (bought 2)
                 'a_pipe_boots': 2,      # counted                 (bought 3)
                 'a_step_flash': 1,      # 15.8 LF / 46            (bought 1)
                 'l_install': 14.4}      # labor is not a pack
    assert _material_cost(res) == 3305.92
    assert abs(3305.92 - 3291.08) < 15
    # The line says what it is counted in, and carries the whole pack.
    assert lines['a_ridge_cap']['bundle_unit'] == 'bundles'
    assert lines['a_ridge_cap']['bundle_waste_pct'] == 10
    assert lines['m_iko_nordic']['bundle_lf'] == pytest.approx(1 / 3)
    assert 'bundle_lf' not in lines['a_pipe_boots']


@needs_node
@pytest.mark.parametrize('item,m,want', [
    # 42 bundles is 14 SQ / (1/3), which lands a hair OVER 42 in floating
    # point. Ordering 43 for a roof that needs exactly 42 is the failure.
    ({'unit': 'SQ', 'measure': 'squares_waste', 'bundle_lf': 1 / 3},
     {'roof_squares': 14, 'waste_pct': 0}, 42),
    ({'unit': 'SQ', 'measure': 'squares_waste', 'bundle_lf': 1 / 3},
     {'roof_squares': 14.01, 'waste_pct': 0}, 43),
    # Waste goes on the FOOTAGE, then it rounds: 59 LF fills two 29.5 LF
    # bundles exactly, and 10% tips it into a third.
    ({'unit': 'LF', 'measure': 'ridge_hip', 'bundle_lf': 29.5},
     {'ridge_hip_lf': 59}, 2),
    ({'unit': 'LF', 'measure': 'ridge_hip', 'bundle_lf': 29.5, 'bundle_waste_pct': 10},
     {'ridge_hip_lf': 59}, 3),
    # No measurement, nothing ordered - waste must not conjure a pack.
    ({'unit': 'LF', 'measure': 'ridge_hip', 'bundle_lf': 29.5, 'bundle_waste_pct': 10},
     {'ridge_hip_lf': 0}, 0),
    # A line with no waste key is the stick-priced line that always existed.
    ({'unit': 'LF', 'measure': 'eave', 'bundle_lf': 10}, {'eave_lf': 137}, 14),
    # An explicit 0 or a junk pack is no pack: per-unit, as before.
    ({'unit': 'LF', 'measure': 'eave', 'bundle_lf': 0}, {'eave_lf': 137.2}, 138),
    ({'unit': 'SQ', 'measure': 'squares_waste', 'bundle_lf': ''},
     {'roof_squares': 12.57, 'waste_pct': 14}, 14.4),
])
def test_the_count_of_packs(tmp_path, item, m, want):
    res = _run(tmp_path, [{'op': 'measure', 'item': item}],
               estimate=_estimate(measurements=m))
    assert res['probes'] == [want]


# ── the pack, the cost and the quantity move together ──────────────────────

@needs_node
def test_repicking_a_package_on_an_older_estimate_converts_the_whole_line(tmp_path):
    """Built by the foot, then the manager prices the book by the pack, then a
    rep re-picks Good. Without syncLinePack the row keeps counting feet while
    its cost becomes per-bundle: 160 LF of starter at $63.48 is a $10,157
    starter line on a $8,000 roof."""
    ops = BUILD + [{'op': 'setPriceBook', 'priceBook': _book(packed=True)},
                   {'op': 'applyBundle', 'trade': 'roofing', 'tier': 'good',
                    'id': 'b_iko_nordic'}]
    res = _run(tmp_path, ops, book=_book(packed=False))
    lines = _lines(res)
    assert lines['a_starter']['quantity'] == 2
    assert lines['a_starter']['tiers']['good']['material_unit_cost'] == 63.48
    assert _material_cost(res, 'good') == 3305.92
    # Better and Best were NOT re-picked. They share the row, so their costs
    # were converted with it - the same dollars per foot, now per pack.
    assert lines['a_starter']['tiers']['better']['material_unit_cost'] == pytest.approx(0.58 * 105)
    assert lines['a_ice_water']['tiers']['best']['material_unit_cost'] == pytest.approx(1.55 * 66.67)
    assert lines['m_iko_nordic']['tiers']['better']['material_unit_cost'] == pytest.approx(130 / 3, abs=1e-4)
    # No row is left with a pack on one side and a per-foot figure on the
    # other: Good holds the invoice's pack price, the other two hold their old
    # per-foot cost multiplied out to the same pack.
    per_unit = {p[0]: p[3] for p in PER_UNIT}
    for pid, (price, cover, _called, _waste) in INVOICE.items():
        assert lines[pid]['tiers']['good']['material_unit_cost'] == price, pid
        for tier in ('better', 'best'):
            cost = lines[pid]['tiers'][tier]['material_unit_cost']
            assert cost == pytest.approx(per_unit[pid] * cover, abs=1e-4), (pid, tier)


@needs_node
def test_and_back_again(tmp_path):
    """A manager who unticks the box gets the per-foot roof back, to the cent
    - not 3 rolls billed at $1.55 each."""
    ops = BUILD + [{'op': 'setPriceBook', 'priceBook': _book(packed=False)}] + BUILD
    res = _run(tmp_path, ops, book=_book(packed=True))
    assert _material_cost(res) == 2918.89
    for it in _lines(res).values():
        assert 'bundle_lf' not in it and 'bundle_waste_pct' not in it


@needs_node
def test_a_quantity_the_rep_typed_is_recounted_not_reused(tmp_path):
    """Manual quantity (measure ''): 150 LF of starter the rep entered by hand
    is 2 bundles, and 2 bundles going back is 210 LF - what the bundles cover,
    so the dollars do not move on the way out."""
    res = _run(tmp_path, [
        {'op': 'rebaseQty', 'qty': 150, 'from': {}, 'to': {'bundle_lf': 105}},
        {'op': 'rebaseQty', 'qty': 2, 'from': {'bundle_lf': 105}, 'to': {}},
        # Feet into packs takes the waste; packs into other packs must not
        # take it twice.
        {'op': 'rebaseQty', 'qty': 103, 'from': {},
         'to': {'bundle_lf': 29.5, 'bundle_waste_pct': 10}},
        {'op': 'rebaseQty', 'qty': 4, 'from': {'bundle_lf': 29.5, 'bundle_waste_pct': 10},
         'to': {'bundle_lf': 59, 'bundle_waste_pct': 10}},
        # Same pack: untouched, whatever it was.
        {'op': 'rebaseQty', 'qty': '7', 'from': {'bundle_lf': 10}, 'to': {'bundle_lf': 10}},
    ])
    assert res['probes'] == [2, 210, 4, 2, '7']


@needs_node
def test_a_single_price_trade_carries_quantity_and_locked_price_across(tmp_path):
    """Simple mode rebuilds its rows from the bundle and carries the old
    quantity and any locked sell price over. Both are per-foot figures until
    the product is bought by the roll."""
    est = _estimate(mode='simple')
    est['trades']['roofing']['line_items'] = [
        {'id': 'x1', 'catalog_id': 'a_ice_water', 'name': 'Ice & Water Shield',
         'unit': 'LF', 'quantity': 137, 'measure': '',          # typed by hand
         'unit_cost': 1.55, 'unit_price': 3.00, 'price_locked': True},
    ]
    res = _run(tmp_path, [{'op': 'applySimpleBundle', 'trade': 'roofing',
                           'id': 'b_iko_nordic'}],
               estimate=est, book=_book(packed=True))
    iw = _lines(res)['a_ice_water']
    assert iw['quantity'] == 3                      # 137 LF + 10% -> 3 rolls
    assert iw['unit_cost'] == 100.62
    assert iw['unit_price'] == pytest.approx(3.00 * 66.67)   # $3/LF, as a roll
    assert iw['price_locked'] is True


@needs_node
def test_the_ice_and_water_heal_leaves_a_real_roll_price_alone(tmp_path):
    """applyMeasurements strips the roll from an ice & water line once the
    Price Book product has none - a repair for lines that carried a roll size
    on a PER-FOOT price. A line genuinely priced per roll must keep it, or a
    manager who un-prices the roll bills every open estimate $100 a foot."""
    def est(cost):
        e = _estimate()
        e['trades']['roofing']['line_items'] = [
            {'id': 'x1', 'catalog_id': 'a_ice_water', 'name': 'Ice & Water Shield',
             'unit': 'LF', 'quantity': 3, 'measure': 'eave_valley',
             'bundle_lf': 66.67, 'bundle_unit': 'rolls',
             'tiers': {'good': {'material_unit_cost': cost, 'labor_unit_cost': 0,
                                'included': True}}}]
        return e
    ops = [{'op': 'applyMeasurements'}]
    broken = _lines(_run(tmp_path, ops, estimate=est(1.55), book=_book(False)))['a_ice_water']
    assert 'bundle_lf' not in broken and broken['quantity'] == 137
    real = _lines(_run(tmp_path, ops, estimate=est(100.62), book=_book(False)))['a_ice_water']
    assert real['bundle_lf'] == 66.67 and real['quantity'] == 3


@needs_node
def test_repicking_does_not_divide_a_per_foot_price_that_was_already_on_a_roll(tmp_path):
    """The state production actually holds: ice & water lines written while
    the book carried a 66.67 LF roll on a $1.55 PER-FOOT price. Re-pick Good on
    one of those and Better and Best must come out at $1.55 a foot - as they
    did before - not $1.55 / 66.67 = two cents a foot. A line genuinely priced
    per roll ($95) is converted, which is the case the old code got wrong the
    other way."""
    def run(better_cost):
        est = _estimate()
        est['trades']['roofing']['tier_bundles'] = {'good': 'b_iko_nordic',
                                                    'better': 'b_iko_nordic', 'best': ''}
        est['trades']['roofing']['line_items'] = [
            {'id': 'x1', 'catalog_id': 'a_ice_water', 'name': 'Ice & Water Shield',
             'unit': 'LF', 'quantity': 3, 'measure': 'eave_valley',
             'bundle_lf': 66.67, 'bundle_unit': 'rolls',          # no waste key
             'tiers': {'good':   {'material_unit_cost': 1.55, 'labor_unit_cost': 0, 'included': True},
                       'better': {'material_unit_cost': better_cost, 'labor_unit_cost': 0, 'included': True},
                       'best':   {'material_unit_cost': 0, 'labor_unit_cost': 0, 'included': False}}}]
        res = _run(tmp_path, [{'op': 'applyBundle', 'trade': 'roofing', 'tier': 'good',
                               'id': 'b_iko_nordic'}],
                   estimate=est, book=_book(packed=False))
        return _lines(res)['a_ice_water']
    broken = run(1.55)
    assert 'bundle_lf' not in broken and broken['quantity'] == 137
    assert broken['tiers']['good']['material_unit_cost'] == 1.55
    assert broken['tiers']['better']['material_unit_cost'] == 1.55
    real = run(95.0)
    assert 'bundle_lf' not in real and real['quantity'] == 137
    assert real['tiers']['better']['material_unit_cost'] == pytest.approx(95 / 66.67, abs=1e-4)


def test_every_line_builder_takes_the_pack_through_packOf():
    """Ten places build a line from a product. One that copies bundle_lf by
    hand and forgets the waste orders short; one that forgets bundle_lf prices
    a roll as a foot. So nothing copies the keys by hand."""
    js = open(APP_JS, encoding='utf-8').read()
    import re
    # (`|| undefined` is how every builder spelled it; the ventilation panel's
    # restore snapshot keeps its own copy of a row's pack and is not a builder.)
    raw = re.findall(r'bundle_lf:\s*\w+\.bundle_lf\s*\|\|', js)
    assert raw == [], 'copy the pack with ...packOf(p), not key by key'
    assert js.count('...packOf(') >= 9
    # ...and the three places an EXISTING row is re-priced from a product take
    # the pack with the cost.
    for fn in ('applyBundleToTier', 'liSwapVariant'):
        body = js[js.index('function ' + fn + '('):]
        body = body[:body.index('\n}\n')]
        assert 'syncLinePack(item, p)' in body, fn
    simple = js[js.index('function buildSimpleItemsFromBundle('):]
    simple = simple[:simple.index('\n}\n')]
    assert 'packRebaseQty(old.quantity, old, p)' in simple
    assert 'packRebaseCost(old.unit_price, old, p)' in simple


# ── the Price Book's Bought-as editor ──────────────────────────────────────

SUGGEST = {'roofing': {'a_ice_water': {'unit': 'rolls', 'cover': 66.67, 'waste_pct': 10},
                       'm_iko_nordic': {'unit': 'bundles', 'cover': 1 / 3, 'waste_pct': 0}}}


def _idx(catalog, pid):
    return next(i for i, p in enumerate(catalog) if p['id'] == pid)


@needs_node
def test_ticking_price_by_the_pack_converts_the_cost_in_the_same_action(tmp_path):
    """$1.55 a foot becomes $103.34 a roll the moment the roll is set. There is
    no state - not even between two clicks - where the book holds a roll size
    on a per-foot price."""
    cat = _catalog(packed=False)
    i = _idx(cat, 'a_ice_water')
    res = _run(tmp_path, [{'op': 'pbSetPackPriced', 'i': i, 'on': True},
                          {'op': 'priceHint', 'i': i}],
               catalog=cat, suggestions=SUGGEST)
    p = res['catalog'][i]
    assert (p['bundle_lf'], p['bundle_unit'], p['bundle_waste_pct']) == (66.67, 'rolls', 10)
    assert p['cost'] == 103.34
    assert not any(k in p for k in ('order_pack', 'order_unit', 'order_waste_pct'))
    assert 'per roll' in res['probes'][0] and '$1.55/LF' in res['probes'][0]


@needs_node
def test_changing_what_a_pack_covers_moves_the_cost_with_it(tmp_path):
    cat = _catalog(packed=True)
    i = _idx(cat, 'a_ridge_cap')                  # $75.58 for 29.5 LF
    res = _run(tmp_path, [{'op': 'pbSetOrder', 'i': i, 'field': 'order_pack', 'val': '59'},
                          {'op': 'pbSetOrder', 'i': i, 'field': 'order_waste_pct', 'val': '5'},
                          {'op': 'pbSetOrder', 'i': i, 'field': 'order_unit', 'val': 'boxes'}],
               catalog=cat)
    p = res['catalog'][i]
    assert p['bundle_lf'] == 59 and p['cost'] == 151.16      # twice the bundle
    assert p['bundle_waste_pct'] == 5 and p['bundle_unit'] == 'boxes'
    assert 'order_pack' not in p


@needs_node
def test_unticking_goes_back_to_the_foot_and_keeps_the_key_present(tmp_path):
    """The explicit 0 matters. The server backfills a seed product's pack onto
    any book where `bundle_lf` is ABSENT, which would put the roll straight
    back on the per-foot price this just produced."""
    cat = _catalog(packed=True)
    i = _idx(cat, 'a_ice_water')
    res = _run(tmp_path, [{'op': 'pbSetPackPriced', 'i': i, 'on': False}], catalog=cat)
    p = res['catalog'][i]
    assert p['cost'] == 1.51                      # $100.62 / 66.67
    assert p['bundle_lf'] == 0 and p['bundle_unit'] == ''
    assert 'bundle_waste_pct' not in p
    # The pack is not lost - it goes back to being the order sheet's.
    assert (p['order_pack'], p['order_unit'], p['order_waste_pct']) == (66.67, 'rolls', 10)
    seed = {'id': 'a_ice_water', 'bundle_lf': 66.67, 'bundle_unit': 'rolls'}
    for field in ('bundle_lf', 'bundle_unit'):
        assert field in A._PRODUCT_BACKFILL_FIELDS
        assert not (field in seed and field not in p), 'the backfill would refill it'


@needs_node
def test_it_will_not_price_by_a_pack_nobody_has_described(tmp_path):
    cat = _catalog(packed=False)
    i = _idx(cat, 'a_pipe_boots')
    res = _run(tmp_path, [{'op': 'pbSetPackPriced', 'i': i, 'on': True}], catalog=cat)
    assert res['alerts'] and 'covers' in res['alerts'][0]
    assert res['catalog'][i] == cat[i]


@needs_node
def test_three_bundles_to_the_square_is_typed_as_a_fraction(tmp_path):
    """0.33 is not a third: it orders 43 bundles for a 14 SQ roof that needs
    42. The box takes "1/3", keeps full precision, and shows it back."""
    cat = _catalog(packed=False)
    i = _idx(cat, 'm_iko_nordic')
    res = _run(tmp_path, [
        {'op': 'parseCover', 's': '1/3'}, {'op': 'parseCover', 's': ' 1 / 3 '},
        {'op': 'parseCover', 's': '66.67'}, {'op': 'coverText', 'v': 1 / 3},
        {'op': 'coverText', 'v': 66.67}, {'op': 'coverText', 'v': 0.4},
        {'op': 'pbSetOrder', 'i': i, 'field': 'order_pack', 'val': '1/3'},
        {'op': 'pbSetOrder', 'i': i, 'field': 'order_unit', 'val': 'bundles'},
        {'op': 'pbSetPackPriced', 'i': i, 'on': True},
        {'op': 'measure', 'item': {'unit': 'SQ', 'measure': 'squares_waste',
                                   'bundle_lf': 1 / 3}},
    ], catalog=cat, estimate=_estimate(measurements={'roof_squares': 14, 'waste_pct': 0}))
    assert res['probes'][0] == res['probes'][1] == pytest.approx(1 / 3, abs=1e-15)
    assert res['probes'][2:6] == [66.67, '1/3', '66.67', '0.4']
    p = res['catalog'][i]
    assert p['bundle_lf'] == 1 / 3 and p['bundle_unit'] == 'bundles'
    assert p['cost'] == 43.33                     # $130 / SQ, three to the square
    assert res['probes'][6] == 42


@needs_node
def test_a_product_not_priced_by_the_pack_still_sets_the_order_sheet_only(tmp_path):
    """The box unticked is the editor exactly as it was: three fields the
    material order reads and pricing never does."""
    cat = _catalog(packed=False)
    i = _idx(cat, 'a_ridge_cap')
    res = _run(tmp_path, [{'op': 'pbSetOrder', 'i': i, 'field': 'order_pack', 'val': '29.5'},
                          {'op': 'pbSetOrder', 'i': i, 'field': 'order_waste_pct', 'val': '0'},
                          {'op': 'pbSetOrder', 'i': i, 'field': 'order_unit', 'val': ''}],
               catalog=cat)
    p = res['catalog'][i]
    assert p['order_pack'] == 29.5 and p['order_waste_pct'] == 0
    assert 'order_unit' not in p and 'bundle_lf' not in p
    assert p['cost'] == 2.88


# ── what the server does with a pack line ──────────────────────────────────

def test_a_quantity_is_printed_in_the_unit_it_is_counted_in():
    """4 bundles of ridge cap printed as "4 LF", and 43 bundles of shingles
    would print as "43 SQ" on a 14 square roof. Mirrors displayUnit()."""
    assert A._display_unit({'unit': 'LF', 'bundle_lf': 29.5, 'bundle_unit': 'bundles'}) == 'bundles'
    assert A._display_unit({'unit': 'SQ', 'bundle_lf': 1 / 3, 'bundle_unit': 'bundles'}) == 'bundles'
    assert A._display_unit({'unit': 'LF'}) == 'LF'
    assert A._display_unit({'unit': 'LF', 'bundle_lf': 0, 'bundle_unit': ''}) == 'LF'
    assert A._display_unit({'unit': 'LF', 'bundle_lf': 10}) == 'LF'     # unnamed pack
    assert A._display_unit({}) == ''


def _signed(items, measurements=None):
    lines = []
    for it in items:
        row = {'tiers': {'good': {'material_unit_cost': 10, 'labor_unit_cost': 0,
                                  'included': True, 'description': ''}}}
        row.update(it)
        lines.append(row)
    return {'estimate_type': 'retail', 'selected_tier': 'good',
            'pricing': {'mode': 'margin', 'tier_rates': {'good': 40}},
            'page_visibility': {},
            'measurements': dict(LA_TOUCHE if measurements is None else measurements),
            'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
                                   'selected_tier': 'good', 'line_items': lines}}}


def test_the_customer_page_and_the_invoice_say_bundles():
    est = _signed([{'name': 'IKO Nordic', 'unit': 'SQ', 'quantity': 43,
                    'bundle_lf': 1 / 3, 'bundle_unit': 'bundles', 'bundle_waste_pct': 0},
                   {'name': 'Pipe Boots', 'unit': 'EA', 'quantity': 2}])
    html, _total = A.render_line_items(est)
    assert '<td class="cvc">bundles</td>' in html
    assert '<td class="cvc">SQ</td>' not in html
    assert '<td class="cvc">EA</td>' in html
    rows = [r for sec in A.invoice_rows(est)['sections'] for r in sec['rows']]
    assert ('IKO Nordic', 43.0, 'bundles') in [(r[0], r[1], r[2]) for r in rows]


def _order(est, catalogs=None):
    return {r['name']: r for r in A.material_order_rows(est, catalogs=catalogs or {})}


def test_the_order_sheet_orders_the_count_that_was_priced():
    """Priced at 4 bundles, ordered as 4 bundles - with the arithmetic that got
    there, so whoever places the order can check it against the branch."""
    est = _signed([
        {'name': 'IKO Hip & Ridge Cap', 'unit': 'LF', 'quantity': 4, 'measure': 'ridge_hip',
         'bundle_lf': 29.5, 'bundle_unit': 'bundles', 'bundle_waste_pct': 10},
        {'name': 'IKO Nordic (Impact-Resistant Shingle)', 'unit': 'SQ', 'quantity': 43,
         'measure': 'squares_waste', 'bundle_lf': 1 / 3, 'bundle_unit': 'bundles',
         'bundle_waste_pct': 0},
        {'name': 'Step Flashing', 'unit': 'LF', 'quantity': 1, 'measure': 'step',
         'bundle_lf': 46, 'bundle_unit': 'bundles', 'bundle_waste_pct': 0},
    ])
    rows = _order(est)
    ridge = rows['IKO Hip & Ridge Cap']
    assert (ridge['order_qty'], ridge['order_unit']) == (4, 'bundles')
    assert (ridge['qty'], ridge['unit']) == (103, 'LF')
    assert '+ 10%' in ridge['math'] and '29.5 per bundle' in ridge['math']
    assert not ridge['math'].startswith('~')
    shingle = rows['IKO Nordic (Impact-Resistant Shingle)']
    assert (shingle['order_qty'], shingle['order_unit'], shingle['unit']) == (43, 'bundles', 'SQ')
    assert 'x 3/SQ' in shingle['math'] and not shingle['math'].startswith('~')
    assert rows['Step Flashing']['order_qty'] == 1


def test_the_name_table_can_no_longer_reorder_a_priced_line():
    """The sheet's own default for "IKO Hip" is 36 LF to the bundle, which
    would order 4 where the estimate priced 5 at the invoice's 29.5. A priced
    line is ordered as priced: nothing on the product, and nothing in the
    name table, gets a second opinion."""
    est = _signed([{'name': 'IKO Hip & Ridge Cap', 'unit': 'LF', 'quantity': 5,
                    'measure': 'ridge_hip', 'catalog_id': 'a_ridge_cap',
                    'bundle_lf': 29.5, 'bundle_unit': 'bundles', 'bundle_waste_pct': 10}],
                  {'ridge_hip_lf': 131})
    cat = {'roofing': {'a_ridge_cap': {'id': 'a_ridge_cap', 'order_pack': 36,
                                       'order_unit': 'boxes', 'order_waste_pct': 0}}}
    r = _order(est, cat)['IKO Hip & Ridge Cap']
    assert (r['order_qty'], r['order_unit']) == (5, 'bundles')   # 131 + 10% / 29.5 = 4.9


def test_a_count_the_rep_changed_is_ordered_as_typed():
    """Measurements say 4 bundles; the rep made it 6. They meant 6, and the row
    says the footage was inferred rather than pretending it measured 6."""
    est = _signed([{'name': 'IKO Hip & Ridge Cap', 'unit': 'LF', 'quantity': 6,
                    'measure': 'ridge_hip', 'bundle_lf': 29.5, 'bundle_unit': 'bundles',
                    'bundle_waste_pct': 10}])
    r = _order(est)['IKO Hip & Ridge Cap']
    assert r['order_qty'] == 6
    assert r['math'].startswith('~')


def test_a_line_priced_before_any_of_this_orders_exactly_as_it_did():
    """No `bundle_waste_pct` on the line means the old rules, untouched: a roll
    line still takes the sheet's 10% on the footage (7 rolls for 400 LF priced
    as 6), and step flashing picks up its new default bundle."""
    est = _signed([{'name': 'Ice & Water Shield', 'unit': 'LF', 'quantity': 6,
                    'measure': 'eave_valley', 'bundle_lf': 66.67, 'bundle_unit': 'rolls'},
                   {'name': 'Step Flashing', 'unit': 'LF', 'quantity': 16, 'measure': 'step'}],
                  {'eave_lf': 250, 'valley_lf': 150, 'step_flash_lf': 15.8})
    rows = _order(est)
    assert rows['Ice & Water Shield']['order_qty'] == 7
    step = rows['Step Flashing']
    assert (step['order_qty'], step['order_unit']) == (1, 'bundles')
    assert '46 per bundle' in step['math']


@needs_node
def test_the_squares_the_order_sheet_recomputes_match_the_browser(tmp_path):
    """_ORDER_MEASURES restates squares_waste so a shingle row can show its
    arithmetic. Blank waste is 10% in app.js (mnum(m.waste_pct, 10)), an
    explicit 0 is 0, and low slope comes off first - all three are easy to get
    wrong in a restatement."""
    cases = [{'roof_squares': 31.4, 'waste_pct': 14, 'low_slope_squares': 4.2,
              'steep_squares': 8.34},
             {'roof_squares': 20},                      # blank waste
             {'roof_squares': 20, 'waste_pct': 0},      # explicit zero
             {'roof_squares': 3, 'low_slope_squares': 5}]   # never negative
    keys = ('squares', 'squares_waste', 'low_slope', 'low_slope_waste', 'steep', 'steep_waste')
    for m in cases:
        ops = [{'op': 'measure', 'item': {'unit': 'X', 'measure': k, 'bundle_lf': 1e-6}}
               for k in keys]
        res = _run(tmp_path, ops, estimate=_estimate(measurements=m))
        for k, packs in zip(keys, res['probes']):
            # packs of one millionth of a square: the browser's own figure, to
            # six places, without restating the formula here.
            assert A._ORDER_MEASURES[k](m) == pytest.approx(packs / 1e6, abs=2e-6), (k, m)


# ── the audit, the suggestions and the one migration that strips a pack ────

def _audit_codes(product):
    pb = {'roofing_catalog': [product],
          'roofing_bundles': [{'id': 'b', 'name': 'B', 'product_ids': [product['id']]}]}
    for f in A.pricebook_audit(pb)['findings']:
        if f['product_id'] == product['id']:
            return {i['code'] for i in f['issues']}
    return set()


def test_the_audit_does_not_cry_wolf_at_a_cheap_pack_set_in_the_editor():
    """$63.48 for a 105 LF bundle of starter is genuinely under a dollar a
    foot. The audit reads that shape as a per-foot price typed into a pack box
    - which is right for a pack that arrived any other way, and is still
    reported for one."""
    starter = {'id': 'a_starter', 'name': 'Starter Strip', 'unit': 'LF', 'cost': 63.48,
               'measure': 'eave_rake', 'bundle_lf': 105, 'bundle_unit': 'bundles'}
    assert 'pack_cost_unconverted' in _audit_codes(dict(starter))
    assert 'pack_cost_unconverted' not in _audit_codes(dict(starter, bundle_waste_pct=0))
    # Squares into bundles is a real conversion, not a unit mismatch.
    shingle = {'id': 'm', 'name': 'Shingle', 'unit': 'SQ', 'cost': 45.29,
               'measure': 'squares_waste', 'bundle_lf': 1 / 3, 'bundle_unit': 'bundles',
               'bundle_waste_pct': 0}
    assert _audit_codes(shingle) == set()


def test_a_roll_the_manager_priced_is_not_stripped_by_the_per_foot_migration():
    """_PER_FOOT_CONVERSIONS removes ice & water's roll while the cost reads
    per-foot. A manager who prices the roll in the editor, at whatever a roll
    costs, has made a different decision and keeps it."""
    def live(**fields):
        p = {'id': 'a_ice_water', 'name': 'Ice & Water Shield', 'unit': 'LF',
             'measure': 'eave_valley', 'bundle_lf': 66.67, 'bundle_unit': 'rolls'}
        p.update(fields)
        pb = {'roofing_catalog': [p], 'roofing_bundles': [], 'roofing_tier_defaults': {}}
        A._ensure_bundle_catalogs(pb)
        return next(x for x in pb['roofing_catalog'] if x['id'] == 'a_ice_water')
    assert 'bundle_lf' not in live(cost=1.55)                      # the old repair
    kept = live(cost=60.0, bundle_waste_pct=10)                    # under 66.67, still a roll
    assert kept['bundle_lf'] == 66.67 and kept['cost'] == 60.0
    # ...and an unticked product's explicit 0 is not refilled from the seed.
    off = live(cost=1.51, bundle_lf=0, bundle_unit='')
    assert off['bundle_lf'] == 0 and off['bundle_unit'] == ''


def test_suggestions_are_the_order_sheets_own_defaults():
    pb = {'roofing_catalog': [
        {'id': 'm', 'name': 'IKO Nordic (Impact-Resistant Shingle)', 'unit': 'SQ', 'cost': 130},
        {'id': 's', 'name': 'Starter Strip', 'unit': 'LF', 'cost': 0.58},
        {'id': 'f', 'name': 'Step Flashing', 'unit': 'LF', 'cost': 1.99},
        {'id': 'r', 'name': 'Ridge Cap', 'unit': 'LF', 'cost': 2.5},
        {'id': 'b', 'name': 'Pipe Boots', 'unit': 'EA', 'cost': 14.71},
        {'id': 'v', 'name': 'Ridge Vent', 'unit': 'LF', 'cost': 34,
         'bundle_lf': 4, 'bundle_unit': 'sticks'},
    ]}
    sug = A.pack_suggestions(pb)['roofing']
    assert sug['m'] == {'unit': 'bundles', 'cover': pytest.approx(1 / 3),
                        'waste_pct': 0.0, 'confirmed': True}
    assert (sug['s']['cover'], sug['s']['unit']) == (105.0, 'bundles')
    assert (sug['f']['cover'], sug['f']['unit']) == (46.0, 'bundles')
    assert sug['r']['confirmed'] is False          # a 25 LF guess says it is one
    assert 'b' not in sug                          # nothing to suggest
    assert 'v' not in sug                          # already priced by the pack


def test_the_suggestions_endpoint_is_manager_up(anon, client):
    assert anon.get('/api/pricebook/pack-suggestions').status_code in (401, 302, 403)
    body = client.get('/api/pricebook/pack-suggestions').get_json()
    assert isinstance(body, dict) and 'roofing' in body
    # The seeded book prices nothing by the pack that the sheet has a default
    # for, so the shingle accessories are all on offer.
    assert 'a_starter' in body['roofing']


def test_the_pack_never_reaches_a_total_except_through_the_quantity():
    """Same contract `order_pack` has: pricing multiplies the stored quantity
    by the stored cost and knows nothing about packs. If a pricing function
    ever reads bundle_lf, a manager changing a bundle size reprices signed
    estimates."""
    import inspect
    for fn in ('_trade_subtotal', '_trade_cost_subtotal', '_estimate_total',
               '_line_sell_total'):
        body = inspect.getsource(getattr(A, fn))
        for field in ('bundle_lf', 'bundle_waste_pct', 'bundle_unit'):
            assert field not in body, (fn, field)


def test_the_editor_and_the_server_agree_on_the_field_names():
    js = open(APP_JS, encoding='utf-8').read()
    assert 'onchange="pbRoofCatSetPackPriced(${i},this.checked)"' in js
    rule = __import__('inspect').getsource(A._order_rule_for)
    for field in ('bundle_lf', 'bundle_unit', 'bundle_waste_pct'):
        assert field in js and field in rule, field
