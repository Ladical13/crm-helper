"""Bundle-restricted color pickers on the customer sign page.

Whichever roofing/siding package the customer picks drives which colors
they see. IKO Nordic only shows IKO colors; CertainTeed only CertainTeed;
LP SmartSide only LP colors; James Hardie only Hardie. Anything else and
we're back to the confusion these tests exist to prevent.
"""
import json


# ── helpers ─────────────────────────────────────────────────────────────

def _pb(A):
    """Live seeded price book — same shape the sign page and API see."""
    return A._ensure_bundle_catalogs(A._load_price_book())


def _est(roofing_bundle_ids=None, siding_bundle_ids=None,
         shingle_selection=None, siding_selection=None):
    """Minimal estimate skeleton with the tier→bundle picks the sign
    page reads. Everything else on the estimate is irrelevant here."""
    est = {'trades': {}}
    if roofing_bundle_ids is not None:
        est['trades']['roofing'] = {
            'enabled': True, 'mode': 'gbb',
            'tier_bundles': dict(roofing_bundle_ids),
        }
    if siding_bundle_ids is not None:
        est['trades']['siding'] = {
            'enabled': True, 'mode': 'gbb',
            'tier_bundles': dict(siding_bundle_ids),
        }
    if shingle_selection is not None:
        est['shingle_selection'] = shingle_selection
    if siding_selection is not None:
        est['siding_selection'] = siding_selection
    return est


# ── bundle color resolution ─────────────────────────────────────────────

def test_bundle_colors_for_tier_resolves_iko_and_certainteed(A):
    """The core promise: switching packages swaps the color set. Same
    tier slot; different bundle → different manufacturer's palette."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': 'b_iko_nordic',
                                   'better': 'b_landmark'})
    iko = A._bundle_colors_for_tier(pb, est, 'roofing', 'good')
    ct  = A._bundle_colors_for_tier(pb, est, 'roofing', 'better')

    # Both seeded palettes are non-empty, and IKO resolves its manufacturer
    # colors rather than inheriting CertainTeed's generic asphalt placeholders.
    assert iko and ct
    assert iko == ['Olde Style Weatherwood', 'Summit Grey', 'Granite Black',
                   'Driftshake', 'Shadow Brown', 'Glacier']
    assert ct == ['Silver Birch', 'Georgetown Gray', 'Weathered Wood',
                  'Heather Blend', 'Burnt Sienna', 'Resawn Shake',
                  'Driftwood', 'Moiré Black', 'Black Walnut']


def test_bundle_colors_switch_manufacturers_across_siding(A):
    """Same tier slot on siding — swapping LP → Hardie changes bundles,
    even when the resolved palette overlaps in name (they share the
    neutral color set today). The resolution path is what's under test."""
    pb = _pb(A)
    est = _est(siding_bundle_ids={'good': 'b_lp_standard',
                                  'better': 'b_hardie_primed',
                                  'best':  'b_edco_d4'})
    lp     = A._bundle_colors_for_tier(pb, est, 'siding', 'good')
    hardie = A._bundle_colors_for_tier(pb, est, 'siding', 'better')
    edco   = A._bundle_colors_for_tier(pb, est, 'siding', 'best')

    assert lp and hardie and edco
    # EDCO ships its own published ENTEX palette — a name unique to that seed
    # is the honest proof that the trade + tier picked its palette.
    assert 'Wickertone' in edco
    assert 'Wickertone' not in lp


def test_bundle_colors_falls_back_to_price_book_default(A):
    """No estimate override → fall back to <trade>_tier_defaults.
    Roofing default 'good' is CertainTeed Landmark, so the asphalt
    palette should come through even with empty tier_bundles."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': '', 'better': '', 'best': ''})
    colors = A._bundle_colors_for_tier(pb, est, 'roofing', 'good')
    assert 'Weathered Wood' in colors


# ── the customer-facing composition chain ───────────────────────────────

def test_customer_color_options_appends_the_reps_typed_list(A):
    """The rep's list ADDS to the material's colors. It used to be a
    fallback that only fired when the bundle had nothing to say — which
    for roofing is never, so the Contract tab's field silently did
    nothing and the customer saw only the short preview palette."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': 'b_landmark',
                                   'better': 'b_landmark',
                                   'best':  'b_landmark'})
    ss  = {'enabled': True, 'options': ['Custom A', 'Custom B']}
    opts = A._customer_color_options(pb, est, 'roofing', 'better', ss)
    assert 'Weathered Wood' in opts          # the bundle's own palette
    assert 'Custom A' in opts and 'Custom B' in opts
    # Manufacturer colors lead; the rep's additions follow.
    assert opts.index('Weathered Wood') < opts.index('Custom A')


def test_customer_color_options_dedupes_against_the_bundle(A):
    """A rep re-typing a color the material already offers must not make
    it appear twice in the customer's dropdown."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': 'b_landmark',
                                   'better': 'b_landmark',
                                   'best':  'b_landmark'})
    ss  = {'enabled': True, 'options': ['weathered wood', 'Custom A']}
    opts = A._customer_color_options(pb, est, 'roofing', 'better', ss)
    lower = [o.casefold() for o in opts]
    assert lower.count('weathered wood') == 1
    assert 'Custom A' in opts


def test_autoseeded_option_list_is_not_appended(A):
    """Every estimate saved before this carries options pre-filled by the
    browser from DEFAULT_SHINGLE_COLORS. Appending those to a CertainTeed
    dropdown would offer the customer colors CertainTeed does not make, so
    an exact match against the seed is treated as 'nobody typed this'."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': 'b_landmark',
                                   'better': 'b_landmark',
                                   'best':  'b_landmark'})
    ss  = {'enabled': True, 'options': list(A.DEFAULT_SHINGLE_COLORS)}
    opts = A._customer_color_options(pb, est, 'roofing', 'better', ss)
    assert 'Weathered Wood' in opts
    assert 'Hunter Green' not in opts     # generic seed name, not a Landmark color
    assert 'Barkwood' not in opts


def test_pinned_material_beats_the_tier_bundle_on_every_tier(A):
    """The Contract tab's 'Material being installed' picker names what is
    actually going on the roof. It bypasses the tier lookup entirely, so
    the palette holds across all three packages — and works on an
    insurance claim, where no tier is being sold at all."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': 'b_landmark',
                                   'better': 'b_landmark',
                                   'best':  'b_landmark'})
    ss  = {'enabled': True, 'options': [], 'material_bundle_id': 'b_iko_nordic'}
    for tier in ('good', 'better', 'best'):
        opts = A._customer_color_options(pb, est, 'roofing', tier, ss)
        assert 'Summit Grey' in opts          # IKO Nordic
        assert 'Weathered Wood' not in opts   # the Landmark palette is not consulted


def test_customer_color_options_falls_back_to_reps_typed_list(A):
    """A manager-made bundle without a material SKU has no palette. The
    rep's typed options must fill the gap — the customer never sees an
    empty color menu."""
    pb = _pb(A)
    # Point every tier at a bundle whose material has no colors:
    # sb_vinyl_dutch uses s_vinyl_dutch (Legacy vinyl — no colors[]).
    est = _est(roofing_bundle_ids={'good': 'sb_vinyl_dutch',
                                   'better': 'sb_vinyl_dutch',
                                   'best':  'sb_vinyl_dutch'})
    # The vinyl bundle is on the siding side — for roofing it resolves
    # to no material, so we're on the fallback path.
    ss  = {'enabled': True, 'options': ['Roof Red', 'Roof Blue']}
    opts = A._customer_color_options(pb, est, 'roofing', 'good', ss)
    assert opts == ['Roof Red', 'Roof Blue']


def test_customer_color_options_final_fallback_is_the_default_list(A):
    """When both bundle and rep list are empty, fall to
    DEFAULT_SHINGLE_COLORS / DEFAULT_SIDING_COLORS — never an empty
    <select> in front of the customer."""
    pb = {}
    est = _est(roofing_bundle_ids={'good': '', 'better': '', 'best': ''})
    opts_r = A._customer_color_options(pb, est, 'roofing', 'good',
                                       {'enabled': True, 'options': []})
    opts_s = A._customer_color_options(pb, est, 'siding',  'good',
                                       {'enabled': True, 'options': []})
    assert opts_r == list(A.DEFAULT_SHINGLE_COLORS)
    assert opts_s == list(A.DEFAULT_SIDING_COLORS)


# ── the tier→colors map inlined into the sign page ─────────────────────

def test_tier_colors_map_covers_every_tier_for_every_enabled_selection(A):
    """The inline JSON blob the sign page reads must carry a list for
    every tier, so the color dropdown can swap on tier change without
    ever going empty."""
    pb = _pb(A)
    est = _est(
        roofing_bundle_ids={'good': 'b_landmark', 'better': 'b_northgate',
                            'best':  'b_standing_seam'},
        siding_bundle_ids={'good': 'b_lp_standard', 'better': 'b_hardie_primed',
                           'best':  'b_edco_d4'},
        shingle_selection={'enabled': True, 'options': []},
        siding_selection={'enabled':  True, 'options': []},
    )
    m = A._tier_colors_map(pb, est)
    assert set(m.keys()) == {'roofing', 'siding'}
    for trade in ('roofing', 'siding'):
        for tier in ('good', 'better', 'best'):
            assert m[trade][tier], f'empty color list for {trade}/{tier}'


def test_tier_colors_map_omits_trades_with_no_customer_selection(A):
    """Rep didn't turn on the customer siding picker → siding stays out
    of the map, so the inline script does nothing for that trade."""
    pb = _pb(A)
    est = _est(
        roofing_bundle_ids={'good': 'b_landmark', 'better': 'b_landmark',
                            'best': 'b_landmark'},
        shingle_selection={'enabled': True, 'options': []},
    )
    m = A._tier_colors_map(pb, est)
    assert 'roofing' in m and 'siding' not in m


# ── the POST handler round-trip ─────────────────────────────────────────

def _make_signable_estimate(client, A, ss=None, sds=None,
                             trades_enabled=('roofing',)):
    """Create an estimate ready for the sign form and return (id, token).
    Writes the doc to disk directly rather than round-tripping the API —
    the sign page reads it via est_find_by_token either way."""
    doc = client.post('/api/estimates', json={}).get_json()
    eid = doc['estimate_id']
    doc = A.est_load(eid)
    doc.setdefault('trades', {})
    for tk in trades_enabled:
        doc['trades'][tk] = {
            'enabled': True, 'mode': 'gbb', 'line_items': [],
            'tier_bundles': {'good': '', 'better': '', 'best': ''},
        }
    if ss is not None:
        doc['shingle_selection'] = ss
    if sds is not None:
        doc['siding_selection']  = sds
    token = 'tok-' + eid
    doc['share_token'] = token
    A.est_save(doc)
    return eid, token


def test_sign_post_saves_shingle_and_siding_colors(client, A):
    """Both colors ride through the POST → both land on the doc.
    Signature blob records them so the packet has authoritative values."""
    ss  = {'enabled': True, 'options': [], 'chosen': ''}
    sds = {'enabled': True, 'options': [], 'chosen': ''}
    eid, token = _make_signable_estimate(client, A, ss=ss, sds=sds,
                                         trades_enabled=('roofing', 'siding'))
    assert token, 'sign token should have been minted for this test'
    # Sign the estimate with a color for each trade.
    r = client.post(f'/sign/{token}', data={
        'sig_name':      'Ada Lovelace',
        'sig_email':     'ada@example.com',
        'selected_tier': 'better',
        'tier_roofing':  'better',
        'tier_siding':   'better',
        'shingle_color': 'Weathered Wood',
        'siding_color':  'Iron Gray',
        'agree':         'on',
    })
    assert r.status_code == 200, r.data
    doc = client.get(f'/api/estimates/{eid}').get_json()

    # Selection blobs updated
    assert doc['shingle_selection']['chosen'] == 'Weathered Wood'
    assert doc['siding_selection']['chosen']  == 'Iron Gray'
    # And mirrored onto the trade dicts so the printed estimate + packet
    # see the same values without any extra plumbing.
    assert doc['trades']['roofing']['colors']['shingle_color'] == 'Weathered Wood'
    assert doc['trades']['siding']['colors']['siding_color']   == 'Iron Gray'
    # The signature record captures both.
    assert doc['signature']['shingle_color'] == 'Weathered Wood'
    assert doc['signature']['siding_color']  == 'Iron Gray'


def test_sign_post_rejects_missing_siding_color_when_asked(client, A):
    """Signing must not slip past a required color."""
    sds = {'enabled': True, 'options': [], 'chosen': ''}
    eid, token = _make_signable_estimate(client, A, sds=sds,
                                         trades_enabled=('siding',))
    assert token
    r = client.post(f'/sign/{token}', data={
        'sig_name':      'Ada Lovelace',
        'selected_tier': 'better',
        'tier_siding':   'better',
        'agree':         'on',
        # deliberately no siding_color
    })
    assert r.status_code == 400
    assert b'siding color' in r.data.lower()


# ── rep helper is public enough for tests ──────────────────────────────

def test_default_color_lists_exist_and_are_populated(A):
    """The final fallback constants must exist and be non-trivial —
    without them a missing bundle + missing rep list = empty menu."""
    assert isinstance(A.DEFAULT_SHINGLE_COLORS, list) and A.DEFAULT_SHINGLE_COLORS
    assert isinstance(A.DEFAULT_SIDING_COLORS,  list) and A.DEFAULT_SIDING_COLORS


# ── published manufacturer palettes ─────────────────────────────────────

def test_edco_and_euroshield_ship_published_colors_not_invented_ones(A):
    """These three palettes shipped with names nobody at EDCO or Euroshield
    would recognise. A SHORT palette only under-sells; an INVENTED one puts a
    color on a signed contract that cannot be ordered."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': 'b_edco', 'better': 'b_edco',
                                   'best': 'b_edco'},
               siding_bundle_ids={'good': 'b_edco_d4', 'better': 'b_edco_d4',
                                  'best': 'b_edco_d4'})
    roof = A._bundle_colors_for_tier(pb, est, 'roofing', 'better')
    side = A._bundle_colors_for_tier(pb, est, 'siding', 'better')

    # Real EDCO names (edcoproducts.com, 2026-09-04)
    assert {'Statuary Bronze', 'Hartford Green', 'T-Tone'} <= set(roof)
    assert {'Wickertone', 'Claytone', 'Driftwood Gray'} <= set(side)
    # The invented ones are gone
    for gone in ('Copper Penny', 'Bone White', 'Regal Blue', 'Burgundy'):
        assert gone not in roof
    for gone in ('Musket Brown', 'Coastal Sage', 'Silver Gray', 'Regal Red'):
        assert gone not in side


def test_euroshield_offers_colors_not_product_lines(A):
    """'Rundle Slate' is a separate Euroshield product, not a color of the
    one being sold — it had no business in a color dropdown."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': 'b_euroshield',
                                   'better': 'b_euroshield',
                                   'best': 'b_euroshield'})
    colors = A._bundle_colors_for_tier(pb, est, 'roofing', 'better')
    assert {'Black', 'Grey', 'Brown', 'Driftwood'} == set(colors)


def test_standing_seam_keeps_the_generic_metal_palette(A):
    """Its color comes off whichever coil the supplier runs for the job, so
    it must NOT inherit EDCO's shingle color card."""
    pb = _pb(A)
    est = _est(roofing_bundle_ids={'good': 'b_standing_seam',
                                   'better': 'b_standing_seam',
                                   'best': 'b_standing_seam'})
    colors = A._bundle_colors_for_tier(pb, est, 'roofing', 'better')
    assert 'Statuary Bronze' not in colors
    assert colors == [c['name'] for c in A._ROOF_METAL_COLORS]


def test_migration_upgrades_a_live_book_but_spares_a_curated_one(A):
    """Same contract every other seed migration keeps: swap the shipped
    placeholder, never a manager's own list."""
    import copy
    shipped = {'roofing_catalog': [{'id': 'm_edco',
                                    'colors': copy.deepcopy(A._EDCO_ROOF_COLORS_V1)}],
               'siding_catalog': [], 'roofing_bundles': [], 'siding_bundles': [],
               'exterior_catalog': []}
    A._migrate_edco_euroshield_visuals(shipped)
    assert shipped['roofing_catalog'][0]['colors'] == A._EDCO_ROOF_COLORS

    mine = [{'name': 'Shop Special', 'hex': '#123456'}]
    curated = {'roofing_catalog': [{'id': 'm_edco', 'colors': copy.deepcopy(mine)}],
               'siding_catalog': [], 'roofing_bundles': [], 'siding_bundles': [],
               'exterior_catalog': []}
    A._migrate_edco_euroshield_visuals(curated)
    assert curated['roofing_catalog'][0]['colors'] == mine


def test_migration_is_idempotent(A):
    """It runs on every price-book GET, so a second pass must change nothing."""
    import copy
    pb = _pb(A)
    once = copy.deepcopy(pb)
    A._migrate_edco_euroshield_visuals(pb)
    assert pb['exterior_catalog'] == once['exterior_catalog']
    assert pb['exterior_catalog_seed_versions'] == once['exterior_catalog_seed_versions']
