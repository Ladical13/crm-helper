"""The Material/Labor split, and the labor lines that feed it.

Labor is priced as its OWN catalog line here — one generic Labor line per
square plus two adders — never as a split on each product, because a catalog
product carries a single `cost`. Every builder therefore writes
`labor_unit_cost: 0`, and for a long time the panel split on that field: the
Labor column read $0.00 on every bid ever written, which looks exactly like a
bid that forgot to charge for labor.

So the split is decided by the LINE (`kind` on its catalog product), and these
tests hold that down on both sides of the wire.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RUNNER = HERE / 'labor_split_runner.js'

# Same contract as test_parity.py: without node this would go green having
# checked nothing, so it skips loudly and CI fails the run on any skip.
pytestmark = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node is required to run the real app.js functions')


def _run(fixtures, tmp_path):
    fin, fout = tmp_path / 'in.json', tmp_path / 'out.json'
    fin.write_text(json.dumps(fixtures))
    proc = subprocess.run([shutil.which('node'), str(RUNNER), str(fin), str(fout)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(fout.read_text())


# ── The seeded labor lines ──────────────────────────────────────────────

def test_the_generic_labor_line_is_145_a_square(A):
    """One line covers tear-off AND install. We do not split the two."""
    p = next(x for x in A.ROOFING_CATALOG_SEED if x['id'] == 'l_labor')
    assert p['cost'] == 145
    assert p['unit'] == 'SQ'
    assert p['measure'] == 'squares_waste'
    assert p['kind'] == 'labor'


def test_both_labor_adders_ride_their_own_measurement(A):
    """Steep and extra-layer price themselves off the takeoff, so they sit at
    zero qty — and a zero-qty line never prices and never prints — on a roof
    that needs neither. A rep cannot forget to add them."""
    by_id = {x['id']: x for x in A.ROOFING_CATALOG_SEED}
    assert by_id['l_steep']['measure'] == 'steep_waste'
    assert by_id['l_extra_layer']['measure'] == 'extra_layer_squares'
    assert by_id['l_steep']['kind'] == by_id['l_extra_layer']['kind'] == 'labor'


def test_the_adders_ship_unpriced_so_the_banner_catches_them(A):
    """Deliberate: the rate is the manager's to set. A steep roof whose rate is
    still $0 trips the red unpriced-line banner rather than quietly bidding the
    extra work at nothing — a plausible invented rate would never be flagged
    again. Delete this test the day real rates are seeded."""
    by_id = {x['id']: x for x in A.ROOFING_CATALOG_SEED}
    assert by_id['l_steep']['cost'] == 0
    assert by_id['l_extra_layer']['cost'] == 0


def test_every_seeded_roofing_bundle_carries_the_labor_lines(A):
    """A roofing bundle that ships without labor bids the roof with no labor
    cost at all — the exact failure this whole change exists to end."""
    for bundle in A.ROOFING_BUNDLES_SEED:
        for pid in ('l_labor', 'l_steep', 'l_extra_layer'):
            assert pid in bundle['product_ids'], f"{bundle['id']} is missing {pid}"


# ── Migrating a book that predates all of this ──────────────────────────

def _legacy_book(A):
    """A roofing book saved back when labor was Tear-Off + Install, with no
    `kind` on anything — i.e. what is actually on the volume today."""
    cat = [dict(p) for p in A.ROOFING_CATALOG_SEED
           if p['id'] not in ('l_labor', 'l_steep', 'l_extra_layer')]
    for p in cat:
        p.pop('kind', None)
    return {
        'roofing_catalog': cat,
        'roofing_bundles': [{'id': 'b_landmark', 'name': 'CertainTeed Landmark',
                             'product_ids': ['m_landmark', 'l_tearoff', 'l_install']}],
    }


def test_the_old_pair_collapses_to_exactly_one_labor_line(A):
    """Both old ids supersede to l_labor. The swap loop skips an id already
    present, so a bundle carrying BOTH ends up with one l_labor — billing the
    roof's labor twice would be worse than the bug being fixed."""
    out = A._ensure_bundle_catalogs(_legacy_book(A))
    ids = next(b for b in out['roofing_bundles'] if b['id'] == 'b_landmark')['product_ids']
    assert ids.count('l_labor') == 1
    assert 'l_tearoff' not in ids and 'l_install' not in ids


def test_the_new_adders_reach_a_book_that_already_has_roofing_bundles(A):
    """product_ids is not a copy field, so a new product in the seed bundle
    reaches nobody. _LATE_BUNDLE_PRODUCTS is the only route onto a live book."""
    out = A._ensure_bundle_catalogs(_legacy_book(A))
    ids = next(b for b in out['roofing_bundles'] if b['id'] == 'b_landmark')['product_ids']
    assert 'l_steep' in ids and 'l_extra_layer' in ids
    assert ids.count('l_steep') == 1


def test_kind_is_backfilled_onto_a_live_books_labor_products(A):
    """`kind` decides which column a line lands in, and every live book's
    labor products predate the field. Without the backfill the split would
    only ever be right on a fresh volume — which is nobody's volume."""
    out = A._ensure_bundle_catalogs(_legacy_book(A))
    by_id = {p['id']: p for p in out['roofing_catalog']}
    assert by_id['l_tearoff']['kind'] == 'labor'
    assert by_id['m_landmark'].get('kind') != 'labor'


def test_a_managers_own_labor_price_survives(A):
    """Cost is never copied from the seed onto a live book — a saved cost is
    the manager's price. l_labor arriving at $145 must not overwrite it."""
    book = _legacy_book(A)
    book['roofing_catalog'].append(
        {'id': 'l_labor', 'name': 'Labor', 'unit': 'SQ', 'cost': 168})
    out = A._ensure_bundle_catalogs(book)
    assert next(p for p in out['roofing_catalog'] if p['id'] == 'l_labor')['cost'] == 168


# ── The split itself, run out of the real app.js ────────────────────────

def _book():
    return {'roofing_catalog': [
        {'id': 'm_landmark'}, {'id': 'a_underlayment'},
        {'id': 'l_labor', 'kind': 'labor'}, {'id': 'l_steep', 'kind': 'labor'},
        {'id': 'l_extra_layer', 'kind': 'labor'}]}


def _line(catalog_id, qty, cost):
    return {'catalog_id': catalog_id, 'name': catalog_id, 'quantity': qty,
            'tiers': {t: {'material_unit_cost': cost, 'labor_unit_cost': 0}
                      for t in ('good', 'better', 'best')}}


def test_labor_picks_up_the_base_line_and_both_adders(tmp_path):
    """The whole point: Labor is base + steep + extra layer, not $0."""
    fx = [{'price_book': _book(), 'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
           'line_items': [_line('m_landmark', 35.2, 142), _line('a_underlayment', 35.2, 9.10),
                          _line('l_labor', 35.2, 145), _line('l_steep', 8.8, 35),
                          _line('l_extra_layer', 35.2, 40)]}}}]
    got = _run(fx, tmp_path)[0]
    assert got['labor'] == pytest.approx(35.2 * 145 + 8.8 * 35 + 35.2 * 40)
    assert got['material'] == pytest.approx(35.2 * 142 + 35.2 * 9.10)
    assert got['material'] + got['labor'] == pytest.approx(got['cost'])


def test_the_split_never_changes_the_total(tmp_path):
    """Whichever bucket a line lands in, cost is the same sum — so a
    misclassification can never move a margin or a bid price."""
    items = [_line('m_landmark', 10, 100), _line('l_labor', 10, 145)]
    a = _run([{'price_book': _book(), 'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
               'line_items': items}}}], tmp_path)[0]
    # Same lines, but the catalog has lost its `kind` — classification now
    # falls back to the id prefix and must still total the same.
    plain = {'roofing_catalog': [{'id': p['id']} for p in _book()['roofing_catalog']]}
    b = _run([{'price_book': plain, 'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
               'line_items': items}}}], tmp_path)[0]
    assert a['cost'] == pytest.approx(b['cost'])
    assert a['labor'] == pytest.approx(b['labor'])   # l_ prefix is the fallback


def test_a_hand_added_row_counts_as_material(tmp_path):
    """No catalog_id means nothing to classify against. Material is the safer
    default and the columns still sum to the same total."""
    fx = [{'price_book': _book(), 'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
           'line_items': [{'name': 'Chimney rebuild', 'quantity': 1,
                           'tiers': {'better': {'material_unit_cost': 900,
                                                'labor_unit_cost': 0}}}]}}}]
    got = _run(fx, tmp_path)[0]
    assert got['material'] == pytest.approx(900) and got['labor'] == 0


def test_a_legacy_estimate_carrying_labor_unit_cost_still_totals(tmp_path):
    """Nothing rewrites saved estimates, so a row that does carry the old field
    must keep counting — it just lands in one bucket rather than splitting."""
    item = _line('l_labor', 10, 100)
    item['tiers']['better']['labor_unit_cost'] = 45
    fx = [{'price_book': _book(), 'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
           'line_items': [item]}}}]
    got = _run(fx, tmp_path)[0]
    assert got['cost'] == pytest.approx(1450)
    assert got['labor'] == pytest.approx(1450)


@pytest.mark.parametrize('layers,expected_passes', [
    (None, 0),   # blank takeoff must never invent an adder
    ('', 0),
    (0, 0),      # new construction — nothing to tear off
    (1, 0),      # the normal job carries no adder
    (2, 1),
    (3, 2),
])
def test_extra_layer_squares_only_charges_beyond_the_first(tmp_path, layers, expected_passes):
    m = {'roof_squares': 32, 'waste_pct': 10, 'low_slope_squares': 0}
    if layers is not None:
        m['existing_layers'] = layers
    got = _run([{'measure_only': True, 'measurements': m}], tmp_path)[0]
    assert got['extra_layer_squares'] == pytest.approx(32 * 1.1 * expected_passes)


def test_extra_layer_squares_excludes_low_slope(tmp_path):
    """Mirrors squares_waste: a rolled-roof section is not torn off by the
    shingle crew, so it must not be charged an extra-layer pass either."""
    got = _run([{'measure_only': True, 'measurements': {
        'roof_squares': 32, 'low_slope_squares': 12, 'waste_pct': 10,
        'existing_layers': 2}}], tmp_path)[0]
    assert got['extra_layer_squares'] == pytest.approx(20 * 1.1)
