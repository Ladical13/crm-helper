"""PBR exposed fastener metal — priced off the supplier sheet, checked against the roof.

Costs come from Architectural Sheet Metals & Panels quote EFC38429 (195 J J
Kelly Rd, Lyons, 09/10/2026) — the same roof, same supplier and same Roofr
report as the standing seam quote EFC38421, so the two metal systems are
priced against one set of measurements.

Three things have to hold, and each fails silently:

  * "(36 LIN)" on the panel is NET COVERAGE, not coil width, so a lineal foot
    covers 3 SF. The quote proves it: 1667 LF x 3 ft = 50.01 SQ over a 49.45 SQ
    roof. Any narrower reading would not cover the roof at all;
  * every trim count on the quote is ceil(Roofr footage / 10), and each product
    has to be wired to the measure that reproduces its count; and
  * roofing has had live price books for months, so a new bundle reaches
    nobody unless it is on _LATE_BUNDLE_IDS.
"""
import math
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The Roofr report for 195 J J Kelly Rd, in feet.
ROOF = {'eave': 221 + 1/12, 'valley': 93 + 10/12, 'ridge': 160.0,
        'rake': 275.0, 'wall': 29.5, 'step': 19.5, 'transition': 42 + 2/12,
        'unspecified': 106 + 1/12}
ROOF_SQ = 49.45

# EFC38429 line for line: product id -> (qty ordered, $ each).
QUOTE_STICKS = {
    'a_pbr_drip':       (23, 12.72),
    'a_pbr_ridge':      (16, 24.44),
    'a_pbr_sidewall':   (2,  24.44),
    'a_pbr_headwall':   (14, 23.44),
    'a_pbr_valley':     (10, 45.88),
    'a_pbr_rake':       (28, 25.94),
    'a_pbr_transition': (5,  34.66),
}
QUOTE_MATERIAL_SUBTOTAL = 12869.59


def _cat(A):
    return {p['id']: p for p in A.ROOFING_CATALOG_SEED}


def _raw(measure):
    """What MEASURE_DEFS computes for this roof, for the measures PBR uses."""
    r = ROOF
    return {
        'eave': r['eave'], 'rake': r['rake'], 'valley': r['valley'],
        'ridge_hip': r['ridge'], 'step': r['step'], 'transition': r['transition'],
        'headwall': r['wall'] + r['unspecified'],
        'ridge_2x_headwall': 2 * r['ridge'] + r['wall'] + r['unspecified'],
    }[measure]


def test_the_panel_is_quoted_at_36_inch_net_coverage(A):
    """$5.15/LF over 3 SF per foot = $171.67/SQ, stored delivered."""
    assert round(5.15 / (36 / 12) * 100, 2) == 171.67 == A._PBR_PRETAX['m_pbr']
    assert _cat(A)['m_pbr']['cost'] == 180.24
    # 1667 LF at 36" is the panel square footage the per-SQ lines divide by,
    # and it has to cover the roof — a narrower reading could not.
    assert A._PBR_PANEL_SQ == 1667 * 3 / 100
    assert A._PBR_PANEL_SQ > ROOF_SQ
    assert 1667 * (34 / 12) / 100 < ROOF_SQ


def test_every_stored_cost_is_its_supplier_price_times_the_uplift(A):
    """Same supplier, same 3.95% tax, same 15-day validity as standing seam,
    so the same _SS_UPLIFT — and delivery, which the quote does not tax,
    carries the buffer alone."""
    cat = _cat(A)
    for pid, pre in A._PBR_PRETAX.items():
        assert cat[pid]['cost'] == round(pre * A._SS_UPLIFT, 2), pid
    for pid, pre in A._PBR_PRETAX_UNTAXED.items():
        assert cat[pid]['cost'] == round(pre * A._SS_BUFFER, 2), pid


def test_the_per_square_lines_divide_the_quote_by_the_panel_it_bought(A):
    sq = A._PBR_PANEL_SQ
    assert round((5000 * 0.10 + 2500 * 0.15) / sq, 2) == A._PBR_PRETAX['a_pbr_fasteners']
    assert round((67 * 6.47 + 5 * 9.90) / sq, 2) == A._PBR_PRETAX['a_pbr_sealants']


def test_every_trim_count_matches_the_quote_off_the_roofr_report(A):
    """The measure on each trim product is right exactly when ceil(footage /
    stick) reproduces what the supplier ordered for this roof."""
    cat = _cat(A)
    for pid, (ordered, each) in QUOTE_STICKS.items():
        p = cat[pid]
        assert p['bundle_lf'] == 10 and p['bundle_unit'] == 'sticks', pid
        assert math.ceil(_raw(p['measure']) / p['bundle_lf'] - 1e-9) == ordered, pid
        assert A._PBR_PRETAX[pid] == each, pid


def test_the_bundle_reproduces_the_quote(A):
    """Price the whole PBR material list off the Roofr report at the panel the
    supplier bought, and land within half a percent of EFC38429. The gap is
    the closures: the supplier rounds per 3-ft piece (~5% over footage)."""
    cat = _cat(A)
    bundle = next(b for b in A.ROOFING_BUNDLES_SEED if b['id'] == 'b_pbr')
    total = 0.0
    for pid in bundle['product_ids']:
        if pid not in A._PBR_PRETAX:
            continue          # not on this quote: underlayment, I&W, labor...
        p = cat[pid]
        if p['measure'] == 'squares_waste':
            qty = A._PBR_PANEL_SQ
        elif p.get('bundle_lf'):
            qty = math.ceil(_raw(p['measure']) / p['bundle_lf'] - 1e-9)
        else:
            qty = math.ceil(_raw(p['measure']) - 1e-9)
        total += qty * A._PBR_PRETAX[pid]
    assert abs(total / QUOTE_MATERIAL_SUBTOTAL - 1) < 0.005, total


def test_outside_closures_cover_ridge_and_headwall_but_not_transition():
    """EFC38429 ordered 160 outside closures. Ridge both sides + headwall is
    152 pieces; adding the transition would need 166 and the order would be
    short — so the supplier does not close the transition, and neither do we."""
    ridge_head = 2 * ROOF['ridge'] + ROOF['wall'] + ROOF['unspecified']
    assert math.ceil(ridge_head / 3) <= 160 < math.ceil((ridge_head + ROOF['transition']) / 3)
    assert math.ceil(ROOF['eave'] / 3) <= 77


def test_the_closure_measure_is_in_app_js_and_audited_as_linear_feet(A):
    src = open(os.path.join(HERE, 'static', 'app.js'), encoding='utf-8').read()
    m = re.search(r"^\s{2}ridge_2x_headwall:\s*\{\s*label:'([^']+)',\s*calc:m => (.+?) \},$",
                  src, re.M)
    assert m, 'ridge_2x_headwall missing from MEASURE_DEFS'
    assert 'LF' in m.group(1)
    assert m.group(2) == ('2 * mnum(m.ridge_hip_lf) + mnum(m.wall_flash_lf) '
                          '+ mnum(m.unspecified_lf)')
    assert A.MEASURE_DIMENSIONS['ridge_2x_headwall'] == 'LF'


def test_the_bundle_carries_metal_trim_not_shingle_placeholders(A):
    cat = _cat(A)
    bundle = next(b for b in A.ROOFING_BUNDLES_SEED if b['id'] == 'b_pbr')
    for shingle_only in ('a_drip_edge', 'a_ridge_cap', 'a_starter',
                         'a_step_flash', 'a_pipe_boots'):
        assert shingle_only not in bundle['product_ids'], shingle_only
    for pid in list(A._PBR_PRETAX) + list(A._PBR_PRETAX_UNTAXED) + ['a_ss_pipe_boot']:
        assert pid in bundle['product_ids'], pid
        assert cat[pid]['cost'] > 0, pid
    # Nothing a supplier sheet sells is labor.
    for pid in list(A._PBR_PRETAX) + list(A._PBR_PRETAX_UNTAXED):
        assert A._guess_cost_class(pid, cat[pid]['name']) == 'material', pid


def test_the_bundle_reaches_a_book_that_already_has_roofing(A):
    """A missing bundle id on a saved book reads as "the manager deleted it",
    so without _LATE_BUNDLE_IDS nobody in production would ever see PBR."""
    saved = {
        'roofing_catalog': [dict(p) for p in A.ROOFING_CATALOG_SEED
                            if not p['id'].startswith(('a_pbr_', 'x_pbr_', 'm_pbr'))],
        'roofing_bundles': [dict(b) for b in A.ROOFING_BUNDLES_SEED
                            if b['id'] != 'b_pbr'],
        'roofing_tier_defaults': dict(A.ROOFING_TIER_DEFAULTS_SEED),
    }
    pb = A._ensure_bundle_catalogs(saved)
    by_id = {b['id']: b for b in pb['roofing_bundles']}
    cat = {p['id']: p for p in pb['roofing_catalog']}
    assert 'b_pbr' in by_id
    for pid in by_id['b_pbr']['product_ids']:
        assert pid in cat, f'{pid} missing from the live catalog'
    # It is an option on the menu, not a new default for anybody's tiers.
    assert pb['roofing_tier_defaults'] == A.ROOFING_TIER_DEFAULTS_SEED


def test_no_pbr_line_trips_the_price_book_audit(A):
    """Closures are under a dollar a foot, which is exactly the audit's
    "per-foot price in a pack line" shape — so they are priced per LF with no
    bundle_lf. A finding on correct data gets the whole audit ignored."""
    pb = A._ensure_bundle_catalogs({})
    r = A.pricebook_audit(pb)
    flagged = {f['product_id']: f['issues'] for f in r['findings']}
    for pid in list(A._PBR_PRETAX) + list(A._PBR_PRETAX_UNTAXED):
        assert pid not in flagged, (pid, flagged.get(pid))
    # And the check is live: the same closure priced per 3-ft piece IS flagged.
    as_piece = dict(next(p for p in pb['roofing_catalog']
                         if p['id'] == 'a_pbr_closure_in'),
                    cost=1.86, bundle_lf=3, bundle_unit='pieces')
    assert 'pack_cost_unconverted' in {
        i['code'] for i in A._audit_product(as_piece, True, 'roofing')}
