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
        # Priced into the package, never broken out as a row — and no copy
        # written for it, so it says nothing either.
        {'id': 'sx_fee', 'name': 'Overhead', 'unit': 'LS', 'cost': 300,
         'customer_visible': False},
        # Labor: the price row is hidden, the promise is not.
        {'id': 'sl_install', 'name': 'Install Labor', 'unit': 'SQ', 'cost': 90,
         'customer_visible': False,
         'bullets': ['Installed by Project One crews to manufacturer spec']},
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


def test_product_desc_wins_over_bundle_description(tmp_path):
    """A tagline set on the primary material follows the material into any bundle,
    so swapping the material also swaps the customer story. Bundle description is
    the fallback for bundles whose products don't override it."""
    book = json.loads(json.dumps(PRICE_BOOK))
    for p in book['siding_catalog']:
        if p['id'] == 's_hardie':
            p['desc'] = 'From the material itself.'
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'best', 'id': 'b_hardie'}], book)
    assert td['tier_descriptions']['best'] == 'From the material itself.'


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


def test_a_hidden_product_with_no_copy_names_nothing(tmp_path):
    """customer_visible: false hides the priced row. A product nobody wrote copy
    for and nobody shows the price of has no business naming itself either —
    "Overhead" is not a selling point."""
    book = json.loads(json.dumps(PRICE_BOOK))
    next(b for b in book['siding_bundles']
         if b['id'] == 'b_vinyl')['product_ids'].append('sx_fee')
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}], book)
    assert 'Overhead' not in td['tier_features']['good']


def test_hidden_labor_keeps_its_promise_on_the_card(tmp_path):
    """Hiding the ROW must not silence the PROMISE. The customer should never
    read "Install Labor - $9,400" as its own negotiable line, but "Installed by
    Project One crews to manufacturer spec" is one of the strongest bullets on
    the card — and it is the only place the customer is told the work is done by
    our own crews."""
    book = json.loads(json.dumps(PRICE_BOOK))
    next(b for b in book['siding_bundles']
         if b['id'] == 'b_vinyl')['product_ids'].append('sl_install')
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}], book)
    assert 'Installed by Project One crews to manufacturer spec' in td['tier_features']['good']
    # …and the item still prices into the package, hidden or not.
    labor = next(i for i in td['line_items'] if i['name'] == 'Install Labor')
    assert labor['customer_visible'] is False
    assert labor['tiers']['good']['material_unit_cost'] == 90


def test_a_silenced_product_stays_silent_whether_shown_or_not(tmp_path):
    """`bullets: []` is the deliberate-silence marker, and it is independent of
    Show — that split is the whole point of the rule above."""
    book = json.loads(json.dumps(PRICE_BOOK))
    dump = next(p for p in book['siding_catalog'] if p['id'] == 'sx_dump')
    dump['customer_visible'] = False
    next(b for b in book['siding_bundles']
         if b['id'] == 'b_vinyl')['product_ids'].append('sx_dump')
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'}], book)
    assert 'Dumpster' not in td['tier_features']['good']


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


def test_custom_clears_the_tier_to_a_blank_slate(tmp_path):
    """Custom means "the price book doesn't sell this" — starting from the last
    bundle's rows means deleting a dozen of them first."""
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'},
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': '__custom__'}])
    assert td['tier_bundles']['good'] == '__custom__'
    assert _included(td, 'good') == set()


def test_custom_clears_only_its_own_tier(tmp_path):
    """Nothing is DELETED — the rows stay in the shared list, excluded from this
    tier only, so the other packages keep pricing their own systems."""
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_vinyl'},
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'best', 'id': 'b_hardie'},
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': '__custom__'}])
    assert _included(td, 'good') == set()
    assert _included(td, 'best') == {'James Hardie', 'House Wrap'}


def test_custom_twice_does_not_wipe_a_hand_built_package(tmp_path):
    """Re-selecting Custom on a tier that is already custom is the re-render and
    reopened-estimate case. It must never clear the rows the rep just typed."""
    est = _estimate()
    td = _run(tmp_path, est, [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': '__custom__'}])
    td['line_items'].append({
        'id': 'hand1', 'name': 'Rep cedar package', 'unit': 'SQ', 'quantity': 12,
        'tiers': {t: {'material_unit_cost': 400, 'labor_unit_cost': 0, 'description': '',
                      'notes': '', 'included': t == 'good'}
                  for t in ('good', 'better', 'best')}})
    est2 = {'trades': {'siding': td}}
    td2 = _run(tmp_path, est2, [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': '__custom__'}])
    assert _included(td2, 'good') == {'Rep cedar package'}


def test_leaving_custom_for_a_bundle_drops_the_custom_name(tmp_path):
    """The bundle names itself — a stale hand-typed name over a Hardie card is
    the same drift the description swap exists to prevent."""
    est = _estimate()
    est['trades']['siding']['tier_bundle_names'] = {'good': 'Rep cedar package', 'better': '', 'best': ''}
    td = _run(tmp_path, est, [
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': '__custom__'},
        {'op': 'applyBundle', 'trade': 'siding', 'tier': 'good', 'id': 'b_hardie'}])
    assert td['tier_bundle_names']['good'] == ''
    assert _included(td, 'good') == {'James Hardie', 'House Wrap'}


def test_non_bundle_trade_is_untouched(tmp_path):
    """gutters/other still use the per-tier template model."""
    est = {'trades': {'gutters': {'enabled': True, 'mode': 'gbb', 'line_items': []}}}
    scenario = tmp_path / 's.json'
    out = tmp_path / 'o.json'
    scenario.write_text(json.dumps({'priceBook': PRICE_BOOK, 'estimate': est, 'ops': [
        {'op': 'applyBundle', 'trade': 'gutters', 'tier': 'good', 'id': 'b_vinyl'}]}),
        encoding='utf-8')
    r = subprocess.run(['node', RUNNER, str(scenario), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    td = json.loads(out.read_text(encoding='utf-8'))['trades']['gutters']
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


# ── what the customer actually reads ───────────────────────────────────
# The two rules above only pay off on the customer's page, and that page is
# rendered by app.py from the saved estimate — so it gets its own check rather
# than trusting the loader's output.

def _siding_gbb_est(**td_kw):
    """A signed-ready siding estimate: one visible material line, one hidden
    labor line, priced identically."""
    def cell(cost):
        return {'material_unit_cost': cost, 'labor_unit_cost': 0,
                'description': '', 'notes': '', 'included': True}
    td = {
        'enabled': True, 'mode': 'gbb', 'selected_tier': 'good',
        # NOT __custom__: a custom tier drops its stored bullets on purpose
        # (see test_package_cards.py). This fixture is about labor staying out
        # of the customer's row list while staying in their price, so the tier
        # names no bundle and keeps the promise the estimate was saved with.
        'tier_bundles': {'good': '', 'better': '', 'best': ''},
        'tier_features': {'good': ['Installed by Project One crews to manufacturer spec'],
                          'better': [], 'best': []},
        'tier_descriptions': {'good': '', 'better': '', 'best': ''},
        'line_items': [
            {'id': 'i1', 'name': 'LP SmartSide - Lap 8"', 'unit': 'SQ', 'quantity': 26,
             'customer_visible': True,
             'tiers': {t: cell(240) for t in ('good', 'better', 'best')}},
            {'id': 'i2', 'name': 'Install Labor', 'unit': 'SQ', 'quantity': 24,
             'customer_visible': False,
             'tiers': {t: cell(90) for t in ('good', 'better', 'best')}},
        ],
    }
    td.update(td_kw)
    return {
        'estimate_type': 'retail', 'salesperson': 'luke', 'selected_tier': 'good',
        'customer': {'name': 'Dana Reyes', 'email': 'dana@example.com', 'phone': '9705550123',
                     'address': {'street': '12 Elm St', 'city': 'Loveland', 'state': 'CO'}},
        'shingle_selection': {'enabled': False, 'options': [], 'chosen': ''},
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
        'trades': {'siding': td},
    }


def _customer_page(client, est):
    r = client.post('/api/estimates', json=est)
    assert r.status_code in (200, 201), r.data
    est_id = r.get_json()['estimate_id']
    token = client.post(f'/api/estimates/{est_id}/share').get_json()['token']
    page = client.get(f'/sign/{token}')
    assert page.status_code == 200
    return page.get_data(as_text=True)


def test_hidden_labor_is_priced_in_but_never_broken_out(client, A):
    """The customer sees the material row and the promise, never the labor row
    — but the labor is in the number they're signing."""
    est = _siding_gbb_est()
    html = _customer_page(client, est)
    assert 'LP SmartSide' in html
    assert 'Install Labor' not in html, 'labor must not be broken out as its own row'
    assert 'Installed by Project One crews to manufacturer spec' in html
    # 26*240 + 24*90 at 35% margin — the labor is in the total the customer signs.
    assert A.calc_selected_total(est) == pytest.approx((26 * 240 + 24 * 90) / 0.65)


def test_a_custom_package_shows_the_name_the_rep_gave_it(client):
    """A Custom tier has no bundle to name it, so the rep's name is what the
    customer reads on the package card."""
    est = _siding_gbb_est(tier_bundle_names={'good': 'Rep Cedar Package',
                                             'better': '', 'best': ''})
    assert 'Rep Cedar Package' in _customer_page(client, est)


def test_a_bundle_package_card_carries_no_extra_name(client):
    """Nothing to show, and nothing invented — the card stays plain
    Good/Better/Best."""
    html = _customer_page(client, _siding_gbb_est())
    assert '<div class="cv-tier-system">' not in html   # the CSS rule always ships


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
