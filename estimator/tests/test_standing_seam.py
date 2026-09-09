"""Standing seam metal — its own trim, and the live-book wiring that ships it.

Costs come from Architectural Sheet Metals & Panels quote EFC38421 (195 J J
Kelly Rd, Lyons, 09/09/2026) — a real 49.45 SQ, 13-facet job checked line for
line against its own Roofr report. It replaced EFC31095 (09/2024), which was a
MECHANICAL SEAM quote: we sell snap-lock, and the clip is a different part at a
different price, not two years of drift.

Four things have to be true before a supplier sheet can be used at all, and
every one of them fails silently:

  * the panel is quoted per LINEAL FOOT off a 20" coil, and a 1.5" seam takes
    ~4" of it, so the net coverage is 16". Read the coil width as the coverage
    instead and the panel prices at $247.80/SQ rather than $309.75 — a 25%
    under-sell that looks completely normal on screen;
  * b_standing_seam shipped carrying the SHINGLE accessory list, whose edge
    metal is four $0 placeholders. Roofing has had live price books for a long
    time, so fixing the seed alone reaches nobody;
  * the Z-closure runs both sides of every ridge AND both sides of every
    valley — it is our valley detail on snap-lock, so nothing else on the bid
    covers a valley. Sized off ridge alone, 93 LF of valley bought nothing; and
  * Roofr reports wall flashing and transitions on every report, and the
    parser read past both, so a metal roof's headwall and transitions priced
    at zero with nothing on screen to say so.
"""


def test_the_panel_price_matches_the_supplier_quote_at_16_inch_coverage(A):
    """$4.13/LF off a 20" coil = $309.75/SQ at 16" net coverage, stored
    delivered. Reading the coil width as the coverage gives $247.80 and
    under-sells the panel by 25%."""
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    assert round(4.13 / (16 / 12) * 100, 2) == 309.75 == A._SS_PRETAX['m_standing_seam']
    assert cat['m_standing_seam']['cost'] == 325.21


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
                  'a_ss_sidewall', 'a_ss_sidewall_recv', 'a_ss_headwall',
                  'a_ss_ridge', 'a_ss_zeecee', 'a_ss_transition',
                  'a_ss_pipe_boot', 'a_ss_sealants',
                  'x_ss_delivery'):
        assert metal in bundle['product_ids'], metal
        assert cat[metal]['cost'] > 0, f'{metal} shipped unpriced'


def test_zee_cee_orders_two_sticks_per_ten_feet_of_ridge(A):
    """A Z-closure runs BOTH sides of the ridge, so 60 LF of ridge takes 6 cap
    sticks and 12 closure sticks — get this wrong and the crew is short by
    half. EFC31095's 6-against-12 over 60 LF is exactly this.

    It used to be carried by bundle_lf 5 against everything else's 10. That
    spelling worked and could not be extended: the doubling lived in the pack
    size, so there was nowhere to add the valley. The measure doubles now
    (ridge_valley_2x) and the pack size is the honest 10 ft the stick actually
    is — same answer on a ridge, and the valley is reachable."""
    import math
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    assert cat['a_ss_ridge']['bundle_lf'] == 10
    assert cat['a_ss_zeecee']['bundle_lf'] == 10
    assert cat['a_ss_zeecee']['measure'] == 'ridge_valley_2x'
    ridge, valley = 60.0, 0.0
    assert math.ceil(ridge / cat['a_ss_ridge']['bundle_lf']) == 6
    # what MEASURE_DEFS.ridge_valley_2x computes, then the pack rounding
    assert math.ceil(2 * (ridge + valley) / cat['a_ss_zeecee']['bundle_lf']) == 12


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
                  'a_ss_sidewall', 'a_ss_sidewall_recv', 'a_ss_headwall',
                  'a_ss_ridge', 'a_ss_zeecee', 'a_ss_transition',
                  'a_ss_pipe_boot', 'a_ss_sealants',
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

    # A book still carrying the untouched $400 placeholder is corrected, and
    # so is one that stopped at EFC31095's $320.25 — the migration is a list of
    # steps precisely so a book cannot be stranded on an intermediate value.
    assert _cost(A._ensure_bundle_catalogs(_book(400))) == 325.21
    assert _cost(A._ensure_bundle_catalogs(_book(320.25))) == 325.21
    # ...and a manager who priced it themselves keeps their number. This is the
    # whole reason the migration tests equality instead of just overwriting.
    assert _cost(A._ensure_bundle_catalogs(_book(455))) == 455
    # Idempotent: the corrected value is not a trigger, so re-running a
    # migrated book cannot walk the price anywhere.
    assert _cost(A._ensure_bundle_catalogs(_book(325.21))) == 325.21


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


# ── EFC38421: the reprice, and the three things the old book could not see ──

def test_every_stored_cost_is_its_supplier_price_times_the_uplift(A):
    """Costs are stored DELIVERED — pre-tax price x _SS_UPLIFT — so the book
    carries the sales tax it had no line for and one deliberate cushion.

    This is the check that keeps the cushion a DECISION. Let the literals drift
    from _SS_PRETAX and the buffer goes back to being whatever falls out of
    which prices happen to be stale, which is how it came to swing from +5.3%
    on a simple gable to -3.6% on a wall-heavy roof."""
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    for pid, pre in A._SS_PRETAX.items():
        assert cat[pid]['cost'] == round(pre * A._SS_UPLIFT, 2), pid
    for pid, pre in A._SS_PRETAX_UNTAXED.items():
        # Delivery and set-up are not taxed on the quote. Taxing them here
        # would be inventing a charge the supplier does not make.
        assert cat[pid]['cost'] == round(pre * A._SS_BUFFER, 2), pid


def test_the_uplift_is_tax_times_cushion_and_not_a_bare_number(A):
    assert A._SS_UPLIFT == round(A._SS_TAX * A._SS_BUFFER, 4)
    assert A._SS_TAX > 1 and A._SS_BUFFER >= 1


def test_the_z_closure_reaches_the_valley(A):
    """Z-Flash IS the valley detail on snap-lock — there is no separate valley
    pan on the bid — so a measure that only sees the ridge leaves every valley
    buying nothing. EFC38421 ordered 51 sticks for 2x160 ridge + 2x93.83
    valley = 507.67 LF, which is exactly ceil(507.67/10)."""
    import math
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    z = cat['a_ss_zeecee']
    assert z['measure'] == 'ridge_valley_2x' and z['bundle_lf'] == 10
    assert math.ceil(2 * (160.0 + 93.833) / 10) == 51


def test_headwall_and_transition_are_priced_products(A):
    """Roofr hands us both footages on every report. Until they were products
    a metal bid priced them at nothing — $703 on EFC38421's roof — and the rep
    had to know from experience to add them by hand."""
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    for pid, measure in (('a_ss_headwall', 'headwall'),
                         ('a_ss_transition', 'transition')):
        assert cat[pid]['measure'] == measure
        assert cat[pid]['cost'] > 0
        assert cat[pid]['bundle_lf'] == 10


def test_the_rake_cap_is_priced_not_just_its_receiver(A):
    """A 2pc rake is receiver + cap. EFC38421 ordered 28 receivers for its 275
    LF of rake and no cap at all — the half that actually sheds water — so the
    book must carry it even though that quote does not."""
    cat = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    assert cat['a_ss_rake']['cost'] == round(32.80 * A._SS_UPLIFT, 2)
    assert cat['a_ss_rake']['measure'] == cat['a_ss_rake_recv']['measure'] == 'rake'


def test_a_live_book_on_the_old_mechanical_seam_prices_is_migrated(A):
    """The whole point of the exercise: roofing has had saved books since long
    before any of this, and _ensure_bundle_catalogs never overwrites a saved
    cost. Without the migration the correction reaches nobody."""
    pb = {'roofing_catalog': [
              {'id': 'm_standing_seam', 'name': 'SS', 'unit': 'SQ', 'cost': 320.25,
               'measure': 'squares_waste'},
              {'id': 'a_ss_clips', 'name': 'Clips', 'unit': 'SQ', 'cost': 23.85,
               'measure': 'squares_waste'},
              {'id': 'a_ss_zeecee', 'name': 'Zee', 'unit': 'LF', 'cost': 32.82,
               'measure': 'ridge_hip', 'bundle_lf': 5, 'bundle_unit': 'sticks'}],
          'roofing_bundles': [], 'roofing_tier_defaults': {}}
    live = {p['id']: p for p in A._ensure_bundle_catalogs(pb)['roofing_catalog']}
    assert live['m_standing_seam']['cost'] == 325.21
    assert live['a_ss_clips']['cost'] == 14.87
    # The measure moves too — _PRODUCT_BACKFILL_FIELDS only fills what is
    # ABSENT, and every live book already has one.
    assert live['a_ss_zeecee']['cost'] == 11.65
    assert live['a_ss_zeecee']['measure'] == 'ridge_valley_2x'
    assert live['a_ss_zeecee']['bundle_lf'] == 10


def test_the_panel_migration_chains_from_the_original_placeholder(A):
    """m_standing_seam has to land on the same number from the original $400
    placeholder AND from EFC31095's $320.25, which is why a migration is a
    LIST of steps. A book that never saw the first correction must not be
    stranded on it."""
    for old in (400, 320.25):
        pb = {'roofing_catalog': [{'id': 'm_standing_seam', 'name': 'SS',
                                   'unit': 'SQ', 'cost': old,
                                   'measure': 'squares_waste'}],
              'roofing_bundles': [], 'roofing_tier_defaults': {}}
        live = {p['id']: p for p in A._ensure_bundle_catalogs(pb)['roofing_catalog']}
        assert live['m_standing_seam']['cost'] == 325.21, old


def test_a_manager_who_repriced_the_metal_is_left_alone(A):
    """Same one-directional rule every cost migration follows: it fires only
    while the live number is still the untouched previous default."""
    pb = {'roofing_catalog': [{'id': 'm_standing_seam', 'name': 'SS', 'unit': 'SQ',
                               'cost': 355.0, 'measure': 'squares_waste'},
                              {'id': 'a_ss_zeecee', 'name': 'Zee', 'unit': 'LF',
                               'cost': 32.82, 'measure': 'valley', 'bundle_lf': 5}],
          'roofing_bundles': [], 'roofing_tier_defaults': {}}
    live = {p['id']: p for p in A._ensure_bundle_catalogs(pb)['roofing_catalog']}
    assert live['m_standing_seam']['cost'] == 355.0
    # measure was hand-changed away from the old seed, so it stays put
    assert live['a_ss_zeecee']['measure'] == 'valley'
