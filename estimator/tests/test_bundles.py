"""Catalog + bundles → tier loading (roofing and siding).

Both trades are built ONE way only: from a flat product catalog plus named
bundles. The loader runs client-side, so these tests drive the REAL functions
lifted out of static/app.js (via node) rather than a Python restatement of them.

What must hold, all of which has bitten before or would ship broken pricing:
  * picking a bundle loads its products into that tier only — tiers are
    independent, so Good can be one system and Best another;
  * switching bundles EXCLUDES the old products from that tier instead of
    leaving both materials priced into the package;
  * an item built before the trade moved to bundles is adopted by name rather
    than duplicated beside its catalog twin;
  * the bundle owns the package's customer copy — picking one replaces BOTH the
    Options-page tagline and the What's Included bullets, so a metal roof can
    never go out described as an architectural shingle;
  * those bullets are BUILT from the products the bundle actually contains, so a
    bundle without soffit can't promise soffit.
"""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'bundle_runner.js')

pytestmark = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node not installed — the loader cannot be run')

PRICE_BOOK = {
    'siding_catalog': [
        {'id': 's_vinyl', 'name': 'Vinyl', 'unit': 'SQ', 'cost': 165, 'measure': 'siding_sq_waste',
         'bullets': ['Vinyl siding', 'Never needs paint']},
        {'id': 's_hardie', 'name': 'James Hardie', 'unit': 'SQ', 'cost': 320, 'measure': 'siding_sq_waste',
         'bullets': ['James Hardie fiber cement', 'Non-combustible']},
        {'id': 'sa_wrap', 'name': 'House Wrap', 'unit': 'SQ', 'cost': 12, 'measure': 'siding_sq_waste',
         'bullets': ['House wrap over the full wall area']},
        # No `bullets` key — the card falls back to the product name.
        {'id': 'sa_soffit', 'name': 'Soffit', 'unit': 'LF', 'cost': 4},
        # Priced and in the scope, but says nothing on the card.
        {'id': 'sx_dump', 'name': 'Dumpster', 'unit': 'LS', 'cost': 500, 'bullets': []},
        # Never shown to the customer at all.
        {'id': 'sx_fee', 'name': 'Overhead', 'unit': 'LS', 'cost': 300,
         'customer_visible': False, 'bullets': ['Should never appear']},
    ],
    'siding_bundles': [
        {'id': 'b_vinyl', 'name': 'Vinyl', 'product_ids': ['s_vinyl', 'sa_wrap'],
         'description': 'Low-maintenance vinyl.'},
        {'id': 'b_hardie', 'name': 'Hardie', 'product_ids': ['s_hardie', 'sa_wrap'],
         'description': 'Fiber cement.',
         'extra_features': ['30-year warranty']},
        # A manager-made bundle with nothing to say: its one product is silenced.
        {'id': 'b_bare', 'name': 'Bare', 'product_ids': ['sx_dump']},
    ],
    'siding_tier_defaults': {'good': 'b_vinyl', 'better': 'b_vinyl', 'best': 'b_hardie'},
}


def _estimate(items=None):
    return {'trades': {'siding': {
        'enabled': True, 'mode': 'gbb', 'line_items': items or [],
        'tier_bundles': {'good': '', 'better': '', 'best': ''}}}}


def _run(tmp_path, estimate, ops, price_book=None):
    scenario = tmp_path / 'scenario.json'
    out = tmp_path / 'out.json'
    scenario.write_text(json.dumps({
        'priceBook': price_book or PRICE_BOOK, 'estimate': estimate, 'ops': ops,
    }), encoding='utf-8')
    r = subprocess.run(['node', RUNNER, str(scenario), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text(encoding='utf-8'))['trades']['siding']


def _included(td, tier):
    return {i['name'] for i in td['line_items']
            if (i.get('tiers') or {}).get(tier, {}).get('included') is not False}


def test_picking_a_bundle_loads_its_products(tmp_path):
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}])
    assert _included(td, 'good') == {'Vinyl', 'House Wrap'}
    assert td['tier_bundles']['good'] == 'b_vinyl'
    vinyl = next(i for i in td['line_items'] if i['name'] == 'Vinyl')
    assert vinyl['tiers']['good']['material_unit_cost'] == 165
    assert vinyl['measure'] == 'siding_sq_waste'      # auto-qty link comes across


def test_tiers_are_independent(tmp_path):
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'},
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'best', 'id': 'b_hardie'}])
    assert _included(td, 'good') == {'Vinyl', 'House Wrap'}
    assert _included(td, 'best') == {'James Hardie', 'House Wrap'}


def test_switching_bundles_excludes_the_old_material(tmp_path):
    """Both materials must never price into one package — that doubles the job."""
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'},
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_hardie'}])
    assert _included(td, 'good') == {'James Hardie', 'House Wrap'}


def test_bundle_description_fills_the_options_page(tmp_path):
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'best', 'id': 'b_hardie'}])
    assert td['tier_descriptions']['best'] == 'Fiber cement.'


def test_bundle_features_fill_the_options_page(tmp_path):
    """Built from the bundle's products, in product_ids order, then the bundle's
    own closing bullets."""
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'best', 'id': 'b_hardie'}])
    assert td['tier_features']['best'] == [
        'James Hardie fiber cement', 'Non-combustible',
        'House wrap over the full wall area', '30-year warranty']
    assert td['tier_features']['good'] == []          # this tier only


def test_features_track_the_products_the_bundle_actually_has(tmp_path):
    """The point of deriving them: a bundle without soffit cannot promise soffit,
    and adding it to the bundle adds the line — no copy to keep in step."""
    book = json.loads(json.dumps(PRICE_BOOK))
    bundle = next(b for b in book['siding_bundles'] if b['id'] == 'b_vinyl')
    assert 'Soffit' not in _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}],
        book)['tier_features']['good']

    bundle['product_ids'].append('sa_soffit')
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}], book)
    # No `bullets` key on that product, so the card falls back to its name.
    assert td['tier_features']['good'][-1] == 'Soffit'


def test_a_product_can_be_priced_and_still_say_nothing(tmp_path):
    """`bullets: []` is explicit silence — a dumpster is real work the customer
    doesn't need a bullet about. Distinct from the key being absent."""
    book = json.loads(json.dumps(PRICE_BOOK))
    next(b for b in book['siding_bundles']
         if b['id'] == 'b_vinyl')['product_ids'].append('sx_dump')
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}], book)
    assert 'Dumpster' not in td['tier_features']['good']
    assert 'Dumpster' in {i['name'] for i in td['line_items']}    # still in the scope


def test_a_hidden_product_never_reaches_the_card(tmp_path):
    """customer_visible: false already hides the row on the customer estimate —
    it must not leak back in as a bullet."""
    book = json.loads(json.dumps(PRICE_BOOK))
    next(b for b in book['siding_bundles']
         if b['id'] == 'b_vinyl')['product_ids'].append('sx_fee')
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}], book)
    assert 'Should never appear' not in td['tier_features']['good']


def test_duplicate_bullets_collapse(tmp_path):
    """Two products can legitimately claim the same warranty line; the card must
    not print it twice."""
    book = json.loads(json.dumps(PRICE_BOOK))
    b = next(x for x in book['siding_bundles'] if x['id'] == 'b_hardie')
    b['extra_features'] = ['Non-combustible', '30-year warranty']
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'best', 'id': 'b_hardie'}], book)
    assert td['tier_features']['best'].count('Non-combustible') == 1


def test_swapping_bundles_replaces_the_whole_story(tmp_path):
    """The new product wins — stale copy on a swapped tier is the bug this fixes."""
    est = _estimate()
    est['trades']['siding']['tier_descriptions'] = {'good': '', 'better': '', 'best': 'Fiber cement.'}
    est['trades']['siding']['tier_features'] = {'good': [], 'better': [], 'best': ['James Hardie fiber cement']}
    td = _run(tmp_path, est, [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'best', 'id': 'b_vinyl'}])
    assert td['tier_descriptions']['best'] == 'Low-maintenance vinyl.'
    assert td['tier_features']['best'] == [
        'Vinyl siding', 'Never needs paint', 'House wrap over the full wall area']


def test_swap_overwrites_hand_edited_copy(tmp_path):
    """A rep's edits lose to the bundle: better a re-type than a wrong roof described."""
    est = _estimate()
    est['trades']['siding']['tier_descriptions'] = {'good': '', 'better': '', 'best': 'Rep wrote this.'}
    est['trades']['siding']['tier_features'] = {'good': [], 'better': [], 'best': ['Rep bullet']}
    td = _run(tmp_path, est, [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'best', 'id': 'b_hardie'}])
    assert td['tier_descriptions']['best'] == 'Fiber cement.'
    assert 'Rep bullet' not in td['tier_features']['best']


def test_bundle_without_copy_leaves_existing_copy_alone(tmp_path):
    """Nothing to replace it with — don't blank the card."""
    est = _estimate()
    est['trades']['siding']['tier_descriptions'] = {'good': 'Keep me.', 'better': '', 'best': ''}
    est['trades']['siding']['tier_features'] = {'good': ['Keep this too'], 'better': [], 'best': []}
    td = _run(tmp_path, est, [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_bare'}])
    assert td['tier_descriptions']['good'] == 'Keep me.'
    assert td['tier_features']['good'] == ['Keep this too']


def test_build_defaults_seeds_copy_on_every_tier(tmp_path):
    td = _run(tmp_path, _estimate(), [{'op': 'buildDefaults', 'trade': 'siding'}])
    vinyl = ['Vinyl siding', 'Never needs paint', 'House wrap over the full wall area']
    assert td['tier_features']['good'] == vinyl
    assert td['tier_features']['better'] == vinyl
    assert td['tier_features']['best'][0] == 'James Hardie fiber cement'
    assert td['tier_descriptions']['best'] == 'Fiber cement.'


def test_bundle_features_are_copied_not_shared(tmp_path):
    """Two tiers on the same bundle must not end up aliasing one array."""
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'},
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'better', 'id': 'b_vinyl'}])
    assert td['tier_features']['good'] == td['tier_features']['better']
    assert td['tier_features']['good'] is not td['tier_features']['better']


def test_build_defaults_seeds_every_tier_from_the_price_book(tmp_path):
    td = _run(tmp_path, _estimate(), [{'op': 'buildDefaults', 'trade': 'siding'}])
    assert td['tier_bundles'] == {'good': 'b_vinyl', 'better': 'b_vinyl', 'best': 'b_hardie'}
    assert _included(td, 'best') == {'James Hardie', 'House Wrap'}


def test_rebuild_does_not_stack_items(tmp_path):
    td = _run(tmp_path, _estimate(), [{'op': 'buildDefaults', 'trade': 'siding'},
                                      {'op': 'buildDefaults', 'trade': 'siding'}])
    names = [i['name'] for i in td['line_items']]
    assert sorted(names) == sorted(set(names))


def test_legacy_item_is_adopted_not_duplicated(tmp_path):
    """A pre-bundle estimate's 'House Wrap' must not gain a catalog twin."""
    legacy = [{'id': 'old1', 'name': 'House Wrap', 'unit': 'SQ', 'quantity': 20,
               'tiers': {t: {'material_unit_cost': 9, 'labor_unit_cost': 0,
                             'description': '', 'notes': '', 'included': True}
                         for t in ('good', 'better', 'best')}}]
    td = _run(tmp_path, _estimate(legacy), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}])
    wraps = [i for i in td['line_items'] if i['name'] == 'House Wrap']
    assert len(wraps) == 1
    assert wraps[0]['id'] == 'old1'                    # the rep's row, kept
    assert wraps[0]['catalog_id'] == 'sa_wrap'         # now catalog-backed
    assert wraps[0]['quantity'] == 20                  # quantity survives
    assert wraps[0]['tiers']['good']['material_unit_cost'] == 12   # repriced


def test_custom_keeps_the_tier_as_built(tmp_path):
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'},
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': '__custom__'}])
    assert td['tier_bundles']['good'] == '__custom__'
    assert _included(td, 'good') == {'Vinyl', 'House Wrap'}


def test_non_bundle_trade_is_untouched(tmp_path):
    """windows/gutters/other still use the per-tier template model."""
    est = {'trades': {'windows': {'enabled': True, 'mode': 'gbb', 'line_items': []}}}
    scenario = tmp_path / 's.json'
    out = tmp_path / 'o.json'
    scenario.write_text(json.dumps({'priceBook': PRICE_BOOK, 'estimate': est, 'ops': [
        {'op': 'applyBundle', 'trade': 'windows', 'tier': 'good', 'id': 'b_vinyl'}]}),
        encoding='utf-8')
    r = subprocess.run(['node', RUNNER, str(scenario), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    td = json.loads(out.read_text(encoding='utf-8'))['trades']['windows']
    assert td['line_items'] == []
    assert 'tier_bundles' not in td


# ── single-price (simple) bundle trades ────────────────────────────────
# Commercial sells as ONE system at ONE price, so its bundle load must produce
# FLAT items (unit_cost / unit_price) rather than per-tier ones. A tier-shaped
# item in a simple-mode trade totals $0 — the bug this path exists to prevent.

COMM_BOOK = {
    'commercial_catalog': [
        {'id': 'cm_tpo', 'name': 'TPO Membrane', 'unit': 'SQ', 'cost': 100, 'measure': 'comm_sq_waste'},
        {'id': 'cm_epdm', 'name': 'EPDM Membrane', 'unit': 'SQ', 'cost': 120, 'measure': 'comm_sq_waste'},
        {'id': 'ca_iso', 'name': 'Polyiso', 'unit': 'SQ', 'cost': 50, 'measure': 'comm_sq_waste'},
        {'id': 'cl_labor', 'name': 'Re-Roof Labor', 'unit': 'SQ', 'cost': 400, 'measure': 'comm_labor_reroof'},
    ],
    'commercial_bundles': [
        {'id': 'cb_tpo', 'name': 'TPO', 'product_ids': ['cm_tpo', 'ca_iso', 'cl_labor']},
        {'id': 'cb_epdm', 'name': 'EPDM', 'product_ids': ['cm_epdm', 'ca_iso', 'cl_labor']},
    ],
    'commercial_tier_defaults': {'good': 'cb_epdm', 'better': 'cb_tpo', 'best': 'cb_tpo'},
    'commercial_simple_default': 'cb_tpo',
}


def _comm(items=None, mode='simple', **kw):
    td = {'enabled': True, 'line_items': items or [], 'simple_bundle': ''}
    if mode:
        td['mode'] = mode
    td.update(kw)
    return {'trades': {'commercial': td},
            'pricing': {'mode': 'margin', 'global_rate': 29,
                        'tier_rates': {}, 'trade_rates': {}, 'per_trade_overrides': {}}}


def _run_comm(tmp_path, estimate, ops):
    scenario = tmp_path / 'scenario.json'
    out = tmp_path / 'out.json'
    scenario.write_text(json.dumps({
        'priceBook': COMM_BOOK, 'estimate': estimate, 'ops': ops,
    }), encoding='utf-8')
    r = subprocess.run(['node', RUNNER, str(scenario), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text(encoding='utf-8'))['trades']['commercial']


def test_simple_bundle_builds_flat_priced_items(tmp_path):
    """The whole point: flat unit_cost/unit_price, never a per-tier shape."""
    td = _run_comm(tmp_path, _comm(), [
        {'op': 'applySimpleBundle', 'trade': 'commercial', 'id': 'cb_tpo'}])
    assert td['simple_bundle'] == 'cb_tpo'
    assert {i['name'] for i in td['line_items']} == {'TPO Membrane', 'Polyiso', 'Re-Roof Labor'}
    for i in td['line_items']:
        assert 'tiers' not in i, f"{i['name']} built with a per-tier shape — would price at $0"
        assert i['unit_cost'] > 0
    tpo = next(i for i in td['line_items'] if i['name'] == 'TPO Membrane')
    assert tpo['measure'] == 'comm_sq_waste'
    # 29% gross margin: 100 / (1 - .29) = 140.85
    assert tpo['unit_price'] == pytest.approx(140.85)


def test_build_defaults_on_simple_trade_uses_the_simple_path(tmp_path):
    """buildBundleDefaults must dispatch on mode — a simple trade seeded with
    tier-shaped items is exactly the $0-estimate bug."""
    td = _run_comm(tmp_path, _comm(), [
        {'op': 'buildDefaults', 'trade': 'commercial'}])
    assert td['simple_bundle'] == 'cb_tpo'          # commercial_simple_default
    assert td['line_items']
    assert all('tiers' not in i for i in td['line_items'])


def test_build_defaults_on_gbb_commercial_still_uses_tiers(tmp_path):
    """Flipped to Good/Better/Best, the same trade takes the per-tier path."""
    td = _run_comm(tmp_path, _comm(mode='gbb'), [
        {'op': 'buildDefaults', 'trade': 'commercial'}])
    assert td['tier_bundles']['better'] == 'cb_tpo'
    assert all('tiers' in i for i in td['line_items'])


def test_swapping_system_replaces_products_and_keeps_quantities(tmp_path):
    """Re-picking must not stack TPO and EPDM into one bid, and must not wipe
    the squares the rep already entered."""
    td = _run_comm(tmp_path, _comm(), [
        {'op': 'applySimpleBundle', 'trade': 'commercial', 'id': 'cb_tpo'}])
    for i in td['line_items']:
        i['quantity'] = 44
    td2 = _run_comm(tmp_path, _comm(items=td['line_items']), [
        {'op': 'applySimpleBundle', 'trade': 'commercial', 'id': 'cb_epdm'}])
    names = [i['name'] for i in td2['line_items']]
    assert 'EPDM Membrane' in names
    assert 'TPO Membrane' not in names, 'old system left priced alongside the new one'
    iso = next(i for i in td2['line_items'] if i['name'] == 'Polyiso')
    assert iso['quantity'] == 44, 'carried-over product lost its quantity'


def test_hand_added_rows_survive_a_system_swap(tmp_path):
    extra = {'id': 'x1', 'name': 'Crane rental', 'unit': 'LS', 'quantity': 1,
             'unit_cost': 900, 'unit_price': 1200}
    td = _run_comm(tmp_path, _comm(items=[extra]), [
        {'op': 'applySimpleBundle', 'trade': 'commercial', 'id': 'cb_tpo'}])
    assert 'Crane rental' in {i['name'] for i in td['line_items']}


def test_mode_round_trip_keeps_the_bundle_link(tmp_path):
    """Simple -> G/B/B -> Simple must preserve catalog_id. Both converters
    rebuild items from a field whitelist, and without the link a later system
    swap can't tell which rows it owns — so it STACKS the new system on top of
    the old one and the bid quietly contains two membranes."""
    td = _run_comm(tmp_path, _comm(), [
        {'op': 'applySimpleBundle', 'trade': 'commercial', 'id': 'cb_tpo'},
        {'op': 'setMode', 'trade': 'commercial', 'mode': 'gbb'},
        {'op': 'setMode', 'trade': 'commercial', 'mode': 'simple'}])
    ids = [i.get('catalog_id') for i in td['line_items']]
    assert all(ids), f'catalog_id lost on the round trip: {ids}'

    # And prove the consequence is actually gone: swapping still replaces.
    td2 = _run_comm(tmp_path, _comm(items=td['line_items']), [
        {'op': 'applySimpleBundle', 'trade': 'commercial', 'id': 'cb_epdm'}])
    names = [i['name'] for i in td2['line_items']]
    assert 'EPDM Membrane' in names
    assert 'TPO Membrane' not in names, 'two membranes in one bid'


def test_setting_the_mode_a_trade_is_already_in_is_a_no_op(tmp_path):
    """Commercial's default mode is simple with no explicit `mode` key. The
    guard has to resolve that default, or 'switch to simple' converts flat
    items as though they were tiered and zeroes their price."""
    td = _run_comm(tmp_path, _comm(mode=None), [
        {'op': 'applySimpleBundle', 'trade': 'commercial', 'id': 'cb_tpo'},
        {'op': 'setMode', 'trade': 'commercial', 'mode': 'simple'}])
    tpo = next(i for i in td['line_items'] if i['name'] == 'TPO Membrane')
    assert tpo['unit_price'] == pytest.approx(140.85)
    assert 'tiers' not in tpo
