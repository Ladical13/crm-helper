"""Material order pack counts: ice & water rolls and hip & ridge bundles.

Three things were wrong on the sheet at once, and every one read plausibly:

* Ice & water is priced per roll (bundle_lf 66.67), so its stored quantity is
  already a COUNT of rolls. The sheet matched only an SQ rule, found none, and
  printed "6 LF - order as measured".
* Hip & ridge matched on "ridge cap" only, at a generic 25 LF. The two ridge
  caps actually sold on Landmark, Northgate and Duration Flex - "Certainteed
  Shadow Ridge H&R" and "OC Flex Hip and Ridge" - matched nothing and printed
  raw feet, and IKO (36 LF) was ordered as if it were 25.
* Anything priced in sticks was divided AGAIN by a name rule: 6 sticks of
  "Metal Ridge Cap - 3pc" became "1 bundles".

Pack sizes confirmed with the supplier 2026-09-15: CertainTeed 30 LF, OC 33,
IKO 36, ice & water 36" x 66.7 ft; 10% waste on both, then round up.
"""
import json
import math
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'measure_runner.js')

ROOF = {'eave_lf': 250, 'valley_lf': 150, 'ridge_hip_lf': 250, 'rake_lf': 120}


def _est(items, measurements=None, **over):
    est = {'estimate_type': 'retail', 'selected_tier': 'better',
           'measurements': dict(ROOF if measurements is None else measurements),
           'trades': {'roofing': {'enabled': True, 'mode': 'simple',
                                  'line_items': items}}}
    est.update(over)
    return est


def _iw(qty, **over):
    it = {'name': 'Ice & Water Shield', 'unit': 'LF', 'quantity': qty,
          'measure': 'eave_valley', 'bundle_lf': 66.67, 'bundle_unit': 'rolls',
          'catalog_id': 'a_ice_water'}
    it.update(over)
    return it


def _ridge(name, qty=250, **over):
    it = {'name': name, 'unit': 'LF', 'quantity': qty, 'measure': 'ridge_hip'}
    it.update(over)
    return it


def _row(A, est, catalogs=None):
    rows = A.material_order_rows(est, catalogs=catalogs or {})
    assert len(rows) == 1, rows
    return rows[0]


# ── ice & water ────────────────────────────────────────────────────────────

def test_ice_and_water_orders_rolls_off_the_footage_plus_ten_percent(A):
    """400 LF of eave + valley: 440 with waste / 66.67 = 6.6 -> 7 rolls.
    It used to print the stored count as "6 LF"."""
    r = _row(A, _est([_iw(6)]))
    assert (r['order_qty'], r['order_unit']) == (7, 'rolls')
    assert (r['qty'], r['unit']) == (400, 'LF')
    assert '+ 10%' in r['math'] and '66.67 per roll' in r['math']


def test_waste_goes_on_the_footage_not_the_stored_count(A):
    """340 LF is 6 rolls priced. With waste it is 374 LF -> 6 rolls. Backing
    footage out of the count (6 x 66.67 = 400 LF, +10%) would order 7."""
    r = _row(A, _est([_iw(6)], {'eave_lf': 190, 'valley_lf': 150}))
    assert r['order_qty'] == 6
    assert r['qty'] == 340


def test_second_row_at_the_eaves_is_counted(A):
    """iw_second_row doubles the eave run: 150 x 2 + 100 = 400 LF."""
    m = {'eave_lf': 150, 'valley_lf': 100, 'iw_second_row': 1}
    r = _row(A, _est([_iw(6)], m))
    assert (r['qty'], r['order_qty']) == (400, 7)


def test_a_count_the_rep_typed_is_respected(A):
    """Measurements say 6 rolls; the rep set 9. They meant 9 rolls, so the
    footage is inferred from 9 and the row says it was."""
    r = _row(A, _est([_iw(9)]))
    assert r['order_qty'] == math.ceil(9 * 66.67 * 1.1 / 66.67 - 1e-9) == 10
    assert r['math'].startswith('~')


def test_each_building_uses_its_own_measurements(A):
    """A garage line must not be sized off the house's eaves."""
    est = _est([_iw(2, section='Garage')], ROOF,
               structures=[{'name': 'Garage', 'trade': 'roofing',
                            'measurements': {'eave_lf': 60, 'valley_lf': 40}}])
    r = _row(A, est)
    assert (r['qty'], r['order_qty']) == (100, 2)


def test_ice_and_water_priced_by_the_foot_orders_the_same_rolls(A):
    """Since 2026-09-15 the line is priced per LF, so the quantity IS the
    footage. The sheet has to land on the same rolls either way."""
    it = {'name': 'Ice & Water Shield', 'unit': 'LF', 'quantity': 400,
          'measure': 'eave_valley', 'catalog_id': 'a_ice_water'}
    r = _row(A, _est([it]))
    assert (r['order_qty'], r['order_unit'], r['qty']) == (7, 'rolls', 400)


def test_full_deck_high_temp_membrane_is_not_wasted_twice(A):
    """Sold by the square off squares_waste, which already carries waste."""
    it = {'name': 'High Temp Ice and Water barrier (metal roof)', 'unit': 'SQ',
          'quantity': 33, 'measure': 'squares_waste'}
    r = _row(A, _est([it]))
    assert (r['order_qty'], r['order_unit']) == (17, 'rolls')


# ── hip & ridge ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name,bundles', [
    # 250 LF + 10% = 275 LF
    ('Certainteed Shadow Ridge H&R', 10),   # / 30 = 9.17
    ('OC Flex Hip and Ridge', 9),           # / 33 = 8.33
    ('IKO Hip & Ridge Cap', 8),             # / 36 = 7.64
])
def test_hip_and_ridge_orders_by_brand(A, name, bundles):
    """The live product names. Two of these printed raw feet, and IKO was
    ordered against a 25 LF bundle."""
    r = _row(A, _est([_ridge(name)]))
    assert (r['order_qty'], r['order_unit']) == (bundles, 'bundles')
    assert (r['qty'], r['unit']) == (250, 'LF')


def test_unbranded_ridge_cap_says_it_is_a_guess(A):
    """And says so FIRST: an informational note is dropped when the column is
    full, and this is the one note that must never be."""
    r = _row(A, _est([_ridge('Ridge Cap')]))
    assert r['order_qty'] == 11            # 275 / 25
    assert r['math'].startswith('UNCONFIRMED')
    long = _row(A, _est([_ridge('Ridge Cap', qty=12345)],
                        {'ridge_hip_lf': 12345.5}))
    assert long['math'].startswith('UNCONFIRMED')


# ── stick-priced lines are not converted twice ─────────────────────────────

@pytest.mark.parametrize('item,sticks', [
    ({'name': 'Metal Ridge Cap - 3pc (24ga)', 'unit': 'LF', 'quantity': 25,
      'measure': 'ridge_hip', 'bundle_lf': 10, 'bundle_unit': 'sticks'}, 25),
    ({'name': 'Metal Drip Edge - Style D (24ga)', 'unit': 'LF', 'quantity': 25,
      'measure': 'eave', 'bundle_lf': 10, 'bundle_unit': 'sticks'}, 25),
    ({'name': 'Ridge Vent', 'unit': 'LF', 'quantity': 12,
      'measure': 'ridge_vent_code', 'bundle_lf': 4, 'bundle_unit': 'sticks'}, 12),
])
def test_a_line_priced_in_sticks_is_ordered_in_those_sticks(A, item, sticks):
    """"Metal Ridge Cap" contains "ridge cap", and 25 sticks came out as
    "1 bundles". The pricing pack is what the count is already in."""
    r = _row(A, _est([item]))
    assert (r['order_qty'], r['order_unit']) == (sticks, 'sticks')


# ── the Price Book's Order pack wins ───────────────────────────────────────

def _catalog(**fields):
    p = {'id': 'p_ridge', 'name': 'Shadow Ridge', 'unit': 'LF'}
    p.update(fields)
    return {'roofing': {'p_ridge': p}}


def test_order_pack_on_the_product_beats_the_name_default(A):
    est = _est([_ridge('Certainteed Shadow Ridge H&R', catalog_id='p_ridge')])
    r = _row(A, est, _catalog(order_pack=20))
    assert r['order_qty'] == 14             # 275 / 20 = 13.75, waste kept


def test_an_explicit_zero_waste_is_honoured(A):
    est = _est([_ridge('Certainteed Shadow Ridge H&R', catalog_id='p_ridge')])
    r = _row(A, est, _catalog(order_waste_pct=0))
    assert r['order_qty'] == 9              # 250 / 30 = 8.33
    assert '%' not in r['math']


def test_order_pack_is_never_read_by_pricing(A):
    """The field exists to change what is ORDERED. If it ever reaches a total,
    a manager dialling in a bundle size reprices signed estimates."""
    import inspect
    src = inspect.getsource(A)
    for fn in ('_trade_subtotal', '_trade_cost_subtotal', '_estimate_total'):
        body = inspect.getsource(getattr(A, fn))
        assert 'order_pack' not in body and 'order_waste_pct' not in body, fn
    assert 'order_pack' in src


def test_the_sheet_prints_the_rolls_and_bundles(A):
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    import io
    est = _est([_iw(6), _ridge('IKO Hip & Ridge Cap')],
               customer={'name': 'Ada Lovelace', 'address': {}},
               signature={'selected_tier': 'better'})
    raw = A.build_material_order_pdf(est)
    text = ' '.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages)
    assert '7 rolls' in text and '8 bundles' in text


def test_the_price_book_editor_writes_the_fields_the_sheet_reads(A):
    """The 📦 editor and _order_rule_for have to agree on three key names, and
    nothing else joins them - a typo on either side makes the editor save a
    value the sheet never looks at."""
    js = open(os.path.join(os.path.dirname(HERE), 'static', 'app.js'),
              encoding='utf-8').read()
    assert "onclick=\"pbToggleOrder('${it.id}')\"" in js
    for field in ('order_pack', 'order_unit', 'order_waste_pct'):
        assert f"pbRoofCatSetOrder(${{i}},'{field}',this.value)" in js, field
        assert field in __import__('inspect').getsource(A._order_rule_for), field


# ── the footage recompute matches the browser ──────────────────────────────

MEASURE_FIXTURE = {'eave_lf': 212.5, 'valley_lf': 48.25, 'ridge_hip_lf': 131,
                   'ridge_lf': 88, 'rake_lf': 97, 'step_flash_lf': 33,
                   'wall_flash_lf': 21, 'unspecified_lf': 14, 'transition_lf': 9,
                   'iw_second_row': 1}


def test_order_measures_match_measure_defs(A, tmp_path):
    """_ORDER_MEASURES restates part of MEASURE_DEFS, so it is run against the
    real app.js formulas. A drift here sizes ice & water off the wrong run."""
    if shutil.which('node') is None:
        pytest.skip('node not installed - the measure formulas cannot be run')
    cases = []
    for key in A._ORDER_MEASURES:
        cases.append({'name': key, 'key': key, 'm': MEASURE_FIXTURE})
        cases.append({'name': key + ':blank', 'key': key, 'm': {}})
    fx, out = tmp_path / 'fx.json', tmp_path / 'out.json'
    fx.write_text(json.dumps(cases), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    js = {r['name']: r['value'] for r in json.loads(out.read_text(encoding='utf-8'))}
    for c in cases:
        assert A._ORDER_MEASURES[c['key']](c['m']) == pytest.approx(js[c['name']]), c['name']
