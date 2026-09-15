"""Ice & water: priced by the linear foot, bought in rolls.

`a_ice_water` was priced $46.46 per SQUARE while its quantity came from
`eave_valley`, which returns LINEAR FEET, with no conversion. So 400 LF of
eave-and-valley billed as 400 squares: $18,584 of membrane on a 32-square roof,
a 33x overcharge. Not an insurance-only fault — in margin mode sell is derived
FROM cost, so it inflated the retail quote too.

That was fixed by pricing per roll (bundle_lf 66.67, $95). On 2026-09-15 it
moved to per FOOT ($95 / 66.67 = $1.43) so the price follows the roof instead
of jumping $95 at every roll boundary. The material order sheet still buys
whole rolls, with waste — see test_material_order.py.

The live book was the reason for the careful migration: it carried the roll
size with a per-foot price ($1.55), which quoted 400 LF as 6 x $1.55 = $9.30.
"""
import app as A


def _seed():
    return {p['id']: p for p in A._ensure_bundle_catalogs(A._load_price_book())
            ['roofing_catalog']}['a_ice_water']


def _live(**fields):
    p = {'id': 'a_ice_water', 'name': 'I&W', 'unit': 'LF', 'measure': 'eave_valley'}
    p.update(fields)
    out = A._ensure_bundle_catalogs({'roofing_catalog': [p], 'roofing_bundles': [],
                                     'roofing_tier_defaults': {}})
    return {x['id']: x for x in out['roofing_catalog']}['a_ice_water']


def test_it_is_priced_by_the_linear_foot():
    p = _seed()
    assert p['measure'] == 'eave_valley'      # already doubles for iw_second_row
    assert p['unit'] == 'LF'
    assert 'bundle_lf' not in p and 'bundle_unit' not in p
    assert p['cost'] == 1.43                  # $95 a 66.67 LF roll


def test_a_realistic_roof_costs_what_the_membrane_costs():
    """180 LF eave + 40 LF valley with the second course on."""
    lf = 180 * 2 + 40
    assert round(lf * _seed()['cost'], 2) == 572.0
    # What it used to bill, kept as the thing that must never come back.
    assert lf * 46.46 == 18584.0


def test_the_audit_does_not_flag_it():
    pb = A._ensure_bundle_catalogs(A._load_price_book())
    flagged = [f for f in A.pricebook_audit(pb)['findings']
               if f['product_id'] == 'a_ice_water']
    assert not flagged


# ── Reaching a book that is already saved ─────────────────────────────────

def test_the_live_book_loses_the_roll_and_keeps_its_price():
    """The production shape: roll size with a per-foot price. Dropping the roll
    is what turns $9.30 back into ~$620; the manager's $1.55 is theirs."""
    live = _live(cost=1.55, bundle_lf=66.67, bundle_unit='rolls')
    assert 'bundle_lf' not in live and 'bundle_unit' not in live
    assert live['cost'] == 1.55


def test_an_untouched_roll_default_moves_price_and_unit_together():
    live = _live(cost=95.0, bundle_lf=66.67, bundle_unit='rolls')
    assert live['cost'] == 1.43
    assert 'bundle_lf' not in live


def test_a_manager_priced_roll_keeps_its_roll():
    """$98 a roll with the roll size gone would bill $98 a FOOT. A cost that
    still reads per-pack keeps the pack."""
    live = _live(cost=98.0, bundle_lf=66.67, bundle_unit='rolls')
    assert live['cost'] == 98.0
    assert live['bundle_lf'] == 66.67


def test_a_manager_who_set_their_own_pack_size_keeps_it():
    live = _live(cost=1.20, bundle_lf=50, bundle_unit='rolls')
    assert live['bundle_lf'] == 50


def test_the_seed_never_puts_the_roll_back():
    """Backfill copies only fields the SEED has, so a removed roll stays gone."""
    live = _live(cost=1.55)
    assert 'bundle_lf' not in live


def test_an_estimate_built_per_roll_heals_on_the_next_measurement_apply():
    """A line built before the switch carries its own bundle_lf, and
    measuredQty reads the LINE, not the book - so a Roofr import on that
    estimate kept producing "3 LF" at a per-foot price. applyMeasurements drops
    the stale pack once the Price Book product no longer has one."""
    import os
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                           'static', 'app.js'), encoding='utf-8').read()
    body = js[js.index('function applyMeasurements() {'):]
    body = body[:body.index('\n}\n')]
    heal = body.index("item.catalog_id === 'a_ice_water' && item.bundle_lf")
    assert body.index('delete item.bundle_lf') > heal
    assert heal < body.index('measuredQty(item)'), 'must heal BEFORE measuring'


def test_the_cost_migration_steps():
    assert A._PRODUCT_COST_MIGRATIONS['roofing']['a_ice_water'] == [(46.46, 1.43),
                                                                   (95.0, 1.43)]


def test_a_manager_who_repriced_it_is_left_alone():
    assert _live(unit='SQ', cost=52.0)['cost'] == 52.0


def test_the_original_per_square_default_is_migrated():
    assert _live(unit='SQ', cost=46.46)['cost'] == 1.43
