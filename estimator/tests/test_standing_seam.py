"""Standing seam metal — its own trim, and the live-book wiring that ships it.

Costs come from Architectural Sheet Metals & Panels quote EFC31095 (27866
Cragmont, Evergreen, 09/25/2024), a real 26 SQ job. Two things had to be true
before that quote could be used at all, and both are easy to break silently:

  * the panel is quoted per LINEAL FOOT off a 20" coil, and a 1.5" mechanical
    seam takes ~4" of it, so the net coverage is 16". Read the coil width as
    the coverage instead and the panel prices at $256/SQ rather than $320.25 —
    a 25% under-sell that looks completely normal on screen; and
  * b_standing_seam shipped carrying the SHINGLE accessory list, whose edge
    metal is four $0 placeholders. Roofing has had live price books for a long
    time, so fixing the seed alone reaches nobody.
"""


def test_the_panel_price_matches_the_supplier_quote_at_16_inch_coverage(A):
    """$4.27/LF off a 20" coil = $320.25/SQ at 16" net coverage. The quote
    settles the coverage on its own: 89 panels cover 118.7 ft of eave and 13
    ten-foot drip sticks were ordered. At 20" coverage the same panels span
    148 ft and would have needed 15."""
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    assert cat['m_standing_seam']['cost'] == 320.25
    assert round(4.27 / (16 / 12) * 100, 2) == 320.25


def test_the_metal_bundle_carries_no_zero_dollar_shingle_edge_metal(A):
    """The four shingle accessories are $0 placeholders. On a shingle bundle
    that is a manager's blank to fill; on standing seam it silently priced the
    entire edge detail — drip, rake, ridge, sidewall — at nothing."""
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    bundle = next(b for b in A.ROOFING_BUNDLES_SEED if b['id'] == 'b_standing_seam')
    for shingle_only in ('a_drip_edge', 'a_ridge_cap', 'a_starter', 'a_step_flash',
                         'a_pipe_boots'):
        assert shingle_only not in bundle['product_ids'], shingle_only
    for metal in ('a_ss_clips', 'a_ss_drip_d', 'a_ss_rake', 'a_ss_rake_recv',
                  'a_ss_sidewall', 'a_ss_sidewall_recv', 'a_ss_ridge',
                  'a_ss_zeecee', 'a_ss_pipe_boot', 'a_ss_sealants',
                  'x_ss_delivery'):
        assert metal in bundle['product_ids'], metal
        assert cat[metal]['cost'] > 0, f'{metal} shipped unpriced'


def test_zee_cee_orders_two_sticks_per_ten_feet_of_ridge(A):
    """A Zee-Cee closure runs BOTH sides of the ridge, so bundle_lf is 5 where
    every other 10-ft trim is 10. The quote's 6 ridge caps against 12 Zee-Cee
    over 60 LF is exactly this — set it to 10 and the crew is short by half."""
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    assert cat['a_ss_ridge']['bundle_lf'] == 10
    assert cat['a_ss_zeecee']['bundle_lf'] == 5
    import math
    assert math.ceil(60 / cat['a_ss_ridge']['bundle_lf']) == 6
    assert math.ceil(60 / cat['a_ss_zeecee']['bundle_lf']) == 12


def test_the_metal_trim_reaches_a_book_that_already_has_roofing(A):
    """_BUNDLE_COPY_FIELDS does not include product_ids, so editing the seed
    bundle changes nothing on a volume that already saved a roofing book. The
    live path is _LATE_BUNDLE_PRODUCTS to add and _BUNDLE_PRODUCT_SUPERSEDED to
    remove; without both, production keeps billing a metal roof for shingle
    edge metal at $0."""
    saved = {
        'roofing_catalog': [dict(p) for p in A.ROOFING_CATALOG_SEED
                            if not p['id'].startswith(('a_ss_', 'x_ss_'))],
        'roofing_bundles': [
            {'id': 'b_standing_seam', 'name': 'Standing Seam',
             'product_ids': ['m_standing_seam', 'a_underlayment', 'a_ice_water',
                             'a_drip_edge', 'a_ridge_cap', 'a_starter',
                             'a_pipe_boots', 'a_step_flash', 'a_decking',
                             'l_tearoff', 'l_install', 'x_dumpster', 'x_permit']},
            # An asphalt bundle where the same accessories are correct.
            {'id': 'b_landmark', 'name': 'CertainTeed Landmark',
             'product_ids': ['m_landmark', 'a_drip_edge', 'a_ridge_cap',
                             'a_starter', 'a_step_flash', 'a_pipe_boots']},
        ],
        'roofing_tier_defaults': dict(A.ROOFING_TIER_DEFAULTS_SEED),
    }
    pb = A._ensure_bundle_catalogs(saved)
    by_id = {b['id']: b for b in pb['roofing_bundles']}
    cat = {p['id']: p for p in pb['roofing_catalog']}

    ss = by_id['b_standing_seam']['product_ids']
    for shingle_only in ('a_drip_edge', 'a_ridge_cap', 'a_starter',
                         'a_step_flash', 'a_pipe_boots'):
        assert shingle_only not in ss, f'{shingle_only} survived into a live metal bundle'
    for metal in ('a_ss_clips', 'a_ss_drip_d', 'a_ss_rake', 'a_ss_rake_recv',
                  'a_ss_sidewall', 'a_ss_sidewall_recv', 'a_ss_ridge',
                  'a_ss_zeecee', 'a_ss_pipe_boot', 'a_ss_sealants',
                  'x_ss_delivery'):
        assert metal in ss, f'{metal} never reached a live book'
        assert metal in cat, f'{metal} missing from the live catalog'
    # Nothing is listed twice: _LATE_BUNDLE_PRODUCTS appends before
    # _BUNDLE_PRODUCT_SUPERSEDED swaps, and both dedupe. If either stopped, the
    # metal bid would carry two of every trim line.
    assert len(ss) == len(set(ss)), ss

    # Scoped to the ONE bundle — asphalt still runs shingle accessories.
    lm = by_id['b_landmark']['product_ids']
    assert 'a_drip_edge' in lm and 'a_starter' in lm
    assert not any(p.startswith('a_ss_') for p in lm), lm


def test_the_corrected_panel_price_reaches_a_book_that_already_has_roofing(A):
    """Cost is never copied onto a live book — a saved cost is the manager's
    price. That protection is also why correcting a seed placeholder reaches
    nobody, and here it matters: the trim is now priced as its own lines, so a
    book still holding the old all-in $400 double-bills every metal bid."""
    def _book(cost):
        cat = [dict(p) for p in A.ROOFING_CATALOG_SEED]
        for p in cat:
            if p['id'] == 'm_standing_seam':
                p['cost'] = cost
        return {'roofing_catalog': cat,
                'roofing_bundles': [dict(b) for b in A.ROOFING_BUNDLES_SEED],
                'roofing_tier_defaults': dict(A.ROOFING_TIER_DEFAULTS_SEED)}

    def _cost(pb):
        return next(p['cost'] for p in pb['roofing_catalog']
                    if p['id'] == 'm_standing_seam')

    # A book still carrying the untouched $400 placeholder is corrected.
    assert _cost(A._ensure_bundle_catalogs(_book(400))) == 320.25
    # ...and a manager who priced it themselves keeps their number. This is the
    # whole reason the migration tests equality instead of just overwriting.
    assert _cost(A._ensure_bundle_catalogs(_book(455))) == 455
    # Idempotent: the corrected value is not the trigger, so re-running a
    # migrated book cannot walk the price anywhere.
    assert _cost(A._ensure_bundle_catalogs(_book(320.25))) == 320.25


def test_roofing_is_seeded_from_app_py_not_price_book_json():
    """price_book.json carried a second roofing catalog that had already gone
    stale — no bullets, no colors, old bundle copy, and b_standing_seam still
    listing the shingle trim. _seed_data_dir copies that file to a fresh volume
    verbatim, so whichever source won was decided by which file someone edited.
    Siding was consolidated into app.py for this reason; roofing follows."""
    import json
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, 'price_book.json'), encoding='utf-8') as fh:
        pb = json.load(fh)
    for key in ('roofing_catalog', 'roofing_bundles', 'roofing_tier_defaults'):
        assert key not in pb, (
            f'{key} is back in price_book.json — it is now seeded from '
            'ROOFING_CATALOG_SEED/ROOFING_BUNDLES_SEED in app.py')
