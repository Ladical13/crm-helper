"""Ice & water: linear feet converted to the rolls it is actually bought in.

`a_ice_water` was priced $46.46 per SQUARE while its quantity came from
`eave_valley`, which returns LINEAR FEET, with no conversion. So 400 LF of
eave-and-valley billed as 400 squares: $18,584 of membrane on a 32-square roof,
a 33x overcharge. Not an insurance-only fault — in margin mode sell is derived
FROM cost, so it inflated the retail quote too.

A 2-square roll is 200 SF of 36"-wide membrane and covers 200/3 = 66.67 LF, and
you buy whole rolls, which is what bundle_lf's ceil is for. $95/roll confirmed
off a supplier invoice.
"""
import math

import app as A


def _seed():
    return {p['id']: p for p in A._ensure_bundle_catalogs(A._load_price_book())
            ['roofing_catalog']}['a_ice_water']


def test_it_converts_linear_feet_to_rolls():
    p = _seed()
    assert p['measure'] == 'eave_valley'      # already doubles for iw_second_row
    assert p['bundle_lf'] == 66.67            # 200 SF / 3 ft width
    assert p['bundle_unit'] == 'rolls'


def test_it_is_priced_per_roll():
    assert _seed()['cost'] == 95.0


def test_a_realistic_roof_costs_what_the_supplier_charges():
    """180 LF eave + 40 LF valley with the second course on."""
    lf = 180 * 2 + 40
    rolls = math.ceil(lf / _seed()['bundle_lf'] - 1e-9)
    assert rolls == 6
    assert rolls * _seed()['cost'] == 570.0
    # What it used to bill, kept as the thing that must never come back.
    assert lf * 46.46 == 18584.0


def test_the_audit_no_longer_flags_it():
    pb = A._ensure_bundle_catalogs(A._load_price_book())
    flagged = [f for f in A.pricebook_audit(pb)['findings']
               if f['product_id'] == 'a_ice_water']
    assert not flagged, 'the fix should satisfy the check that found it'


# ── Reaching a book that is already saved ─────────────────────────────────

def test_the_conversion_backfills_onto_a_saved_book():
    """A live book saved before the product had a pack size would otherwise
    keep pricing the raw measure — the seed alone reaches nobody."""
    assert 'bundle_lf' in A._PRODUCT_BACKFILL_FIELDS
    assert 'bundle_unit' in A._PRODUCT_BACKFILL_FIELDS


def test_a_manager_who_set_their_own_pack_size_keeps_it():
    """Absence is the test, never falsiness — the same contract every other
    backfill field follows."""
    pb = {'roofing_catalog': [{'id': 'a_ice_water', 'name': 'I&W', 'unit': 'LF',
                               'cost': 88.0, 'measure': 'eave_valley',
                               'bundle_lf': 50, 'bundle_unit': 'rolls'}],
          'roofing_bundles': [], 'roofing_tier_defaults': {}}
    out = A._ensure_bundle_catalogs(pb)
    live = {p['id']: p for p in out['roofing_catalog']}['a_ice_water']
    assert live['bundle_lf'] == 50, 'a hand-set pack size must survive'


def test_the_cost_moves_with_the_unit():
    """The trap this pair exists to avoid: a live book that gained only the
    conversion would price 6 rolls at $46.46 and be wrong by half in the OTHER
    direction. The migration only fires while the live number is still the
    untouched old default."""
    assert A._PRODUCT_COST_MIGRATIONS['roofing']['a_ice_water'] == (46.46, 95.0)


def test_a_manager_who_repriced_it_is_left_alone():
    pb = {'roofing_catalog': [{'id': 'a_ice_water', 'name': 'I&W', 'unit': 'SQ',
                               'cost': 52.0, 'measure': 'eave_valley'}],
          'roofing_bundles': [], 'roofing_tier_defaults': {}}
    out = A._ensure_bundle_catalogs(pb)
    live = {p['id']: p for p in out['roofing_catalog']}['a_ice_water']
    assert live['cost'] == 52.0, 'only the untouched default is migrated'


def test_an_untouched_default_is_migrated():
    pb = {'roofing_catalog': [{'id': 'a_ice_water', 'name': 'I&W', 'unit': 'SQ',
                               'cost': 46.46, 'measure': 'eave_valley'}],
          'roofing_bundles': [], 'roofing_tier_defaults': {}}
    out = A._ensure_bundle_catalogs(pb)
    live = {p['id']: p for p in out['roofing_catalog']}['a_ice_water']
    assert live['cost'] == 95.0
    assert live['bundle_lf'] == 66.67, 'conversion must arrive with the price'
