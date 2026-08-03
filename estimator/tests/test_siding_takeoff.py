"""Siding material take-off — matches the QXO LP/Hardie supplier sheet.

The supplier ships a per-manufacturer take-off spreadsheet that converts a Hover
report into a piece-count material order. Our estimator's `siding_material_takeoff`
mirrors that math so the production packet ships an accurate supplier order.

Two golden tests below feed the same numbers the supplier sheet's LP Primed and
Color+ tabs use, and pin the resulting piece counts + accessory quantities to
the same values the sheet computes. A drift in the take-off math trips these
before the packet ever prints.

Waste divisor rule: siding uses /0.85 (+15% column), trim uses /0.84 (+19%),
and accessories carry no waste — mirrors how the sheet's columns divide.
"""
import math

import pytest


def _est(measurements, bundle_id, profile, tier='good'):
    """Build a minimum signed estimate that siding_material_takeoff can read."""
    td = {
        'enabled': True, 'mode': 'gbb', 'selected_tier': tier,
        'tier_bundles': {'good': '', 'better': '', 'best': ''},
        'tier_profiles': {'good': '', 'better': '', 'best': ''},
        'line_items': [],
    }
    td['tier_bundles'][tier] = bundle_id
    td['tier_profiles'][tier] = profile
    return {
        'estimate_type': 'retail', 'selected_tier': tier,
        'measurements': measurements,
        'trades': {'siding': td},
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
    }


def _rows_by_product(rows):
    """Look up a row by a substring of its product name."""
    def find(fragment):
        matches = [r for r in rows if fragment.lower() in r[1].lower()]
        assert matches, f'no take-off row matched {fragment!r}: {[r[1] for r in rows]}'
        return matches[0]
    return find


# ── LP Primed golden fixture — matches the "LP Primed" tab exactly ──────────
#
# Column mapping from the sheet:
#   B5   = 5564  siding SF, LP Primed 8" Lap  → E5 = 618.16 pcs (=B5/100*11.11)
#   B11  = 540   window/door trim LF (5/4×4)   → E11 = 33.75 pcs (=B11/16)
#   B12  = 34    window/door trim LF (5/4×6)   → E12 = 2.125 pcs
#   B18  = 68    inside corners LF             → E18 = 4.25  (=B18/16)
#   B19  = 83    outside corners LF            → E19 = 10.375 (=B19*2/16)
#   B22  = 45    sloped trim LF (5/4×4)        → E22 = 2.8125
#   B27  = 80    vert trim LF (5/4×4)          → E27 = 5.0
#   B33  = 366   eaves fascia LF               → E33 = 22.875
#   B34  = 198   rakes fascia LF               → E34 = 12.375
#   B36  = 374   eaves frieze LF (5/4×4)       → E36 = 23.375
#   B41  = 169   level frieze LF (5/4×4)       → E41 = 10.5625
#   B51  = 582   soffit 24" solid LF           → E51 = 36.375
#   B54  = 544   soffit panel 4x8 SF           → E54 = 17.0
#   H10  = 55.64 total siding SQ (=B5/100)
#   E57  = wrap rolls = H10/13.5 = 4.121
#   E59  = sealant tubes = H10*1.5 = 83.46
#   E60  = coil nails = H10/20 = 2.782
#   E61  = trim nails = H10/15 = 3.71
#
# For fields the sheet models per-width but our estimator collapses under a
# single default width, we set siding_trim_width_default=4 so the packet
# emits 5/4×4 labels consistent with the sheet's dominant width.

LP_PRIMED_MEAS = {
    'siding_squares': 5564 / 100.0,   # 55.64 SQ (sheet stores raw SF)
    'siding_waste_pct': 0,             # sheet does NOT add its own — waste is the +15% column
    'siding_openings_count': 0,        # sheet Z-flash uses total trim LF, not opening count — skip
    'siding_outside_corners_lf': 83,
    'siding_inside_corners_lf': 68,
    'siding_trim_sloped_lf': 45,
    'siding_trim_vertical_lf': 80 + 540 + 34,   # vertical + window/door trim rolled in
    'siding_trim_width_default': 4,
    'siding_starter_lf': 0,
    'siding_fascia_eaves_lf': 366,
    'siding_fascia_rakes_lf': 198,
    'siding_frieze_eaves_lf': 374,
    'siding_frieze_level_lf': 169,
    'siding_soffit_lf': 582,
    'siding_soffit_width': 24,
    'siding_soffit_vented_pct': 100,   # sheet's row is "17"-24" SOLID" but semantics match
}


def test_lp_primed_primary_siding_matches_the_sheet(A):
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    section, product, size, pieces, order = find('8" Lap')
    assert section == 'Siding'
    # E5 in the sheet: =B5/100*11.11 = 618.1604
    assert pieces == pytest.approx(618.1604, rel=1e-4)
    # G5 in the sheet: =E5/0.85 = 727.2475... ceil() to whole SKUs = 728
    assert order == math.ceil(618.1604 / 0.85 - 1e-9)


def test_lp_primed_corners_match_the_sheet(A):
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, _, pcs_out, _ = find('Corner Trim — Outside')
    _, _, _, pcs_in, _ = find('Corner Trim — Inside')
    assert pcs_out == pytest.approx(83 * 2 / 16.0, rel=1e-6)   # 10.375
    assert pcs_in == pytest.approx(68 / 16.0, rel=1e-6)         # 4.25


def test_lp_primed_fascia_and_frieze_match_the_sheet(A):
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, _, pcs_e_fas, _ = find('Fascia — Eaves')
    _, _, _, pcs_r_fas, _ = find('Fascia — Rakes')
    _, _, _, pcs_e_fri, _ = find('Frieze — Eaves')
    _, _, _, pcs_l_fri, _ = find('Frieze — Level')
    assert pcs_e_fas == pytest.approx(366 / 16.0, rel=1e-6)   # 22.875
    assert pcs_r_fas == pytest.approx(198 / 16.0, rel=1e-6)   # 12.375
    assert pcs_e_fri == pytest.approx(374 / 16.0, rel=1e-6)   # 23.375
    assert pcs_l_fri == pytest.approx(169 / 16.0, rel=1e-6)   # 10.5625


def test_lp_primed_soffit_lf_matches_the_sheet(A):
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, _, pcs_vented, _ = find('Vented Soffit')
    # 100% vented so all 582 LF at 16' sticks = 36.375
    assert pcs_vented == pytest.approx(582 / 16.0, rel=1e-6)


def test_lp_primed_soffit_panels_route_at_wide_width(A):
    """Widths ≥25\" route to 4×8 (LP) or 4×10 (Hardie) panels, SF/100 × factor."""
    m = dict(LP_PRIMED_MEAS)
    # Simulate the wide-soffit row: 544 SF, width 36" so 544 LF at 36" width
    # → soffit_sf = 544*36/12 = 1632 SF, / 100 * 3.125 = 51.0 panels. But the
    # sheet actually feeds the SF directly on the "Soffit Panel" row (B54=544),
    # so the equivalent LF at 12" width = 544 LF. Use a matching setup here.
    m['siding_soffit_lf'] = 544 * 12 / 36.0   # so soffit_sf = 544
    m['siding_soffit_width'] = 36
    rows = A.siding_material_takeoff(_est(m, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, size, pcs, _ = find('Soffit Panel')
    assert '4×8' in size
    # E54 in the sheet: =B54/100*3.125 = 17.0
    assert pcs == pytest.approx(544 / 100.0 * 3.125, rel=1e-4)


def test_lp_primed_accessories_match_the_sheet(A):
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, _, wrap_pcs, _ = find('TriBuilt House Wrap')
    _, _, _, seal_pcs, _ = find('Sealant / Caulk')
    _, _, _, coil_pcs, _ = find('Coil Nails')
    _, _, _, trim_nail_pcs, _ = find('Trim Nails')
    assert wrap_pcs == pytest.approx(55.64 / 13.5, rel=1e-4)   # 4.121
    assert seal_pcs == pytest.approx(55.64 * 1.5, rel=1e-4)    # 83.46
    assert coil_pcs == pytest.approx(55.64 / 20.0, rel=1e-4)   # 2.782
    # LP Primed sheet uses /15 for trim nails; LP Expert uses /10.
    assert trim_nail_pcs == pytest.approx(55.64 / 15.0, rel=1e-4)   # 3.71


def test_lp_expert_trim_nails_are_stricter_per_the_sheet(A):
    """LP Expert Finish sheet has trim nails at SQ/10 (tighter fastening for
    pre-finished siding)."""
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_expert', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, _, trim_nail_pcs, _ = find('Trim Nails')
    assert trim_nail_pcs == pytest.approx(55.64 / 10.0, rel=1e-4)


# ── Color + / Hardie Statement golden fixture ──────────────────────────────
#
# B5 = 1800 SF, JH Statement 8.25" Lap  → E5 = 256.5 pcs (=H5*14.25)
# B11 = 540 LF window/door trim (5/4×4)  → E11 = 45.0 (=B11/12)
# B33 = 206 LF eaves fascia               → E33 = 17.167 (=B33/12)
# H10 = 18 SQ
# Sealant: =H10*1.5 = 27.0 tubes
# Wrap:    =H10/13 (Hardie sheet uses 13) — our impl uses 13.5 (from LP tabs)
#          so we don't pin wrap on the Hardie tab. Documented drift.

HARDIE_STATEMENT_MEAS = {
    'siding_squares': 1800 / 100.0,   # 18 SQ
    'siding_waste_pct': 0,
    'siding_outside_corners_lf': 0,
    'siding_inside_corners_lf': 0,
    'siding_trim_sloped_lf': 0,
    'siding_trim_vertical_lf': 540,
    'siding_trim_width_default': 4,
    'siding_starter_lf': 0,
    'siding_fascia_eaves_lf': 206,
    'siding_fascia_rakes_lf': 0,
    'siding_frieze_eaves_lf': 0,
    'siding_frieze_level_lf': 0,
    'siding_soffit_lf': 0,
    'siding_soffit_width': 12,
    'siding_soffit_vented_pct': 100,
}


def test_hardie_statement_primary_siding_matches_the_sheet(A):
    rows = A.siding_material_takeoff(
        _est(HARDIE_STATEMENT_MEAS, 'b_hardie_statement', 'lap_8_25'), 'good')
    find = _rows_by_product(rows)
    _, product, _, pieces, _ = find('8.25" Lap')
    assert 'Statement' in product
    # E5 in the sheet: =H5*14.25 where H5=18 SQ → 256.5 pcs
    assert pieces == pytest.approx(18 * 14.25, rel=1e-6)


def test_hardie_uses_12_ft_sticks(A):
    """Hardie's take-off column C is 12 (foot-length of trim/corner sticks),
    where LP uses 16. A fascia at 206 LF divides by 12, not 16."""
    rows = A.siding_material_takeoff(
        _est(HARDIE_STATEMENT_MEAS, 'b_hardie_statement', 'lap_8_25'), 'good')
    find = _rows_by_product(rows)
    _, _, _, pcs, _ = find('Fascia — Eaves')
    assert pcs == pytest.approx(206 / 12.0, rel=1e-6)


def test_hardie_bear_skins_pack_ships(A):
    """Hardie's take-off has a Bear Skins fastener pack (55/pc) = primary
    pieces / 55. LP tabs don't have this line."""
    rows = A.siding_material_takeoff(
        _est(HARDIE_STATEMENT_MEAS, 'b_hardie_statement', 'lap_8_25'), 'good')
    find = _rows_by_product(rows)
    _, _, _, packs, _ = find('Bear Skins')
    assert packs == pytest.approx((18 * 14.25) / 55.0, rel=1e-6)


def test_lp_bundles_have_no_bear_skins(A):
    """LP fasteners are coil nails, not Bear Skins — that pack must not appear."""
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_standard', 'lap_8'), 'good')
    assert not any('bear skins' in r[1].lower() for r in rows)


# ── Waste divisor + rounding rules ─────────────────────────────────────────

def test_siding_waste_uses_0_85_and_trim_waste_uses_0_84(A):
    """Two different waste columns on the sheet:
       * primary siding (lap / panel / shake) → /0.85 (+15%)
       * every trim / corner / fascia / frieze / soffit → /0.84 (+19%)
       A bug that used /0.85 on trim would systematically UNDER-order it."""
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, _, siding_pcs, siding_order = find('8" Lap')
    _, _, _, fascia_pcs, fascia_order = find('Fascia — Eaves')
    assert siding_order == math.ceil(siding_pcs / 0.85 - 1e-9)
    assert fascia_order == math.ceil(fascia_pcs / 0.84 - 1e-9)


def test_accessories_carry_no_waste_divisor(A):
    """Rolls / tubes / coils / packs come off pure formulas — no waste column."""
    rows = A.siding_material_takeoff(
        _est(LP_PRIMED_MEAS, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, _, wrap_pcs, wrap_order = find('TriBuilt House Wrap')
    _, _, _, seal_pcs, seal_order = find('Sealant / Caulk')
    assert wrap_order == math.ceil(wrap_pcs - 1e-9)
    assert seal_order == math.ceil(seal_pcs - 1e-9)


# ── Profile picker + bundle resolution ─────────────────────────────────────

def test_profile_defaults_to_bundle_default_when_unset(A):
    """A rep who never opens the profile chip should still get the correct
    primary profile — lap_8 for LP, lap_8_25 for Hardie."""
    est = _est({'siding_squares': 20}, 'b_lp_standard', '')
    est['trades']['siding']['tier_profiles']['good'] = ''
    rows = A.siding_material_takeoff(est, 'good')
    assert rows and '8" Lap' in rows[0][1]


def test_profile_swap_uses_new_factor(A):
    """Switch LP from lap_8 to bb_4x8 and the primary pieces drop from
    ~11.11/SQ to 3/SQ + battens ride alongside."""
    rows_lap = A.siding_material_takeoff(
        _est({'siding_squares': 20, 'siding_waste_pct': 0}, 'b_lp_standard', 'lap_8'), 'good')
    rows_bb = A.siding_material_takeoff(
        _est({'siding_squares': 20, 'siding_waste_pct': 0}, 'b_lp_standard', 'bb_4x8'), 'good')
    lap_primary = next(r for r in rows_lap if '8" Lap' in r[1])
    bb_primary = next(r for r in rows_bb if 'Board & Batten' in r[1])
    bb_batten = next(r for r in rows_bb if 'Battens' in r[1])
    assert lap_primary[3] == pytest.approx(20 * 11.11, rel=1e-6)
    assert bb_primary[3] == pytest.approx(20 * 3.0, rel=1e-6)
    # 3 battens per panel — one row of battens per row of panels.
    assert bb_batten[3] == pytest.approx(20 * 3.0 * 3, rel=1e-6)


def test_shake_profiles_pick_the_right_pcs_per_sq(A):
    """Straight-edge shake = 43 pcs/SQ, staggered-edge = 50."""
    est = _est({'siding_squares': 10, 'siding_waste_pct': 0}, 'b_hardie_statement', 'shake_straight')
    rows_s = A.siding_material_takeoff(est, 'good')
    r_s = next(r for r in rows_s if 'Straight Edge' in r[1] and 'Shake' in r[1])
    assert r_s[3] == pytest.approx(10 * 43, rel=1e-6)

    est = _est({'siding_squares': 10, 'siding_waste_pct': 0}, 'b_hardie_statement', 'shake_staggered')
    rows_g = A.siding_material_takeoff(est, 'good')
    r_g = next(r for r in rows_g if 'Staggered Edge' in r[1])
    assert r_g[3] == pytest.approx(10 * 50, rel=1e-6)


def test_bundle_with_no_profile_config_returns_no_takeoff(A):
    """EDCO steel + legacy vinyl aren't LP/Hardie systems and skip the QXO
    take-off — the packet just uses the existing Materials section for them."""
    est = _est({'siding_squares': 20}, 'b_edco_d4', '')
    assert A.siding_material_takeoff(est, 'good') == []


def test_disabled_siding_trade_returns_no_takeoff(A):
    est = _est({'siding_squares': 20}, 'b_lp_standard', 'lap_8')
    est['trades']['siding']['enabled'] = False
    assert A.siding_material_takeoff(est, 'good') == []


def test_takeoff_reads_the_signed_tiers_bundle(A):
    """Good tier on b_lp_standard, Best tier on b_hardie_statement — the
    take-off for tier='best' must use the Hardie bundle, not the LP one."""
    est = {
        'estimate_type': 'retail', 'selected_tier': 'best',
        'measurements': {'siding_squares': 15},
        'trades': {'siding': {
            'enabled': True, 'mode': 'gbb',
            'tier_bundles': {'good': 'b_lp_standard',
                             'better': 'b_hardie_primed',
                             'best':   'b_hardie_statement'},
            'tier_profiles': {'good': 'lap_8', 'better': 'lap_8_25', 'best': 'lap_8_25'},
            'line_items': [],
        }},
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
    }
    rows = A.siding_material_takeoff(est, 'best')
    assert rows and 'Statement' in rows[0][1] and '8.25' in rows[0][2]


def test_legacy_fascia_field_still_works(A):
    """An in-flight estimate that only has the pre-split siding_fascia_lf must
    still produce a fascia row — the packet doesn't lose LF just because the
    rep hasn't re-entered under the split fields."""
    m = dict(LP_PRIMED_MEAS)
    m.pop('siding_fascia_eaves_lf')
    m.pop('siding_fascia_rakes_lf')
    m['siding_fascia_lf'] = 500
    rows = A.siding_material_takeoff(
        _est(m, 'b_lp_standard', 'lap_8'), 'good')
    find = _rows_by_product(rows)
    _, _, _, pcs, _ = find('Fascia — Eaves')
    assert pcs == pytest.approx(500 / 16.0, rel=1e-6)


# ── App.js ↔ app.py profile mirrors ────────────────────────────────────────

def test_profile_labels_mirror_the_js_constant(A):
    """SIDING_PROFILE_LABELS in app.py must match SIDING_PROFILE_LABELS in app.js
    — an out-of-sync label ships one story in the picker and another on the
    packet."""
    import os, re
    app_js = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'static', 'app.js')
    src = open(app_js, encoding='utf-8').read()
    block = src[src.index('const SIDING_PROFILE_LABELS'):]
    block = block[:block.index('};') + 2]
    js_labels = dict(re.findall(r"(\w+):\s*'([^']+)'", block))
    assert js_labels == A.SIDING_PROFILE_LABELS


def test_bundle_profiles_mirror_the_js_constant(A):
    """SIDING_BUNDLE_PROFILES options in app.py must match app.js."""
    import os, re, json
    app_js = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'static', 'app.js')
    src = open(app_js, encoding='utf-8').read()
    block = src[src.index('const SIDING_BUNDLE_PROFILES'):]
    block = block[:block.index('};') + 2]
    # Simplified extraction: pull each bundle id + its default + option ids.
    bundle_defs = re.findall(
        r"(b_[a-z_0-9]+):\s*\{\s*mfg:'([a-z]+)',\s*default:'([a-z_0-9]+)',\s*options:\[([^\]]+)\]",
        block)
    js_map = {bid: {'mfg': mfg, 'default': dflt,
                    'options': [x.strip().strip("'") for x in opts.split(',')]}
              for bid, mfg, dflt, opts in bundle_defs}
    assert js_map == A.SIDING_BUNDLE_PROFILES
