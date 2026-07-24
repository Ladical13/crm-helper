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
    than duplicated beside its catalog twin.
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
        {'id': 's_vinyl', 'name': 'Vinyl', 'unit': 'SQ', 'cost': 165, 'measure': 'siding_sq_waste'},
        {'id': 's_hardie', 'name': 'James Hardie', 'unit': 'SQ', 'cost': 320, 'measure': 'siding_sq_waste'},
        {'id': 'sa_wrap', 'name': 'House Wrap', 'unit': 'SQ', 'cost': 12, 'measure': 'siding_sq_waste'},
    ],
    'siding_bundles': [
        {'id': 'b_vinyl', 'name': 'Vinyl', 'product_ids': ['s_vinyl', 'sa_wrap'],
         'description': 'Low-maintenance vinyl.'},
        {'id': 'b_hardie', 'name': 'Hardie', 'product_ids': ['s_hardie', 'sa_wrap'],
         'description': 'Fiber cement.'},
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
