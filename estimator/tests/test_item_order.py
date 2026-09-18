"""Every package lists its materials in the same order.

Good, Better and Best share ONE line_items array and a bundle pick APPENDED
whatever products it brought. Pick a shingle for Good and standing seam for
Best and the metal panel landed after Good's drip edge — the Best card led with
shingle accessories and named its own roof last, and no two estimates matched,
because the real order was "which package did the rep pick first".

The order is the price book's: a row sorts to where the manager put the product
in the catalog. A hand-added row has no catalog_id and stays at the end of its
section. Sorting is per SECTION, so a building's block stays its own block.

The loader runs client-side, so these drive the real app.js functions under
node rather than restating them in Python.
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

# Catalog order IS the standard order: material, material, accessory, accessory,
# then labor. What the manager arranged with ↑↓ in the Price Book.
PRICE_BOOK = {
    'roofing_catalog': [
        {'id': 'm_shingle', 'name': 'Shingles', 'unit': 'SQ', 'cost': 150},
        {'id': 'm_metal', 'name': 'Standing Seam', 'unit': 'SQ', 'cost': 400},
        {'id': 'a_underlay', 'name': 'Underlayment', 'unit': 'SQ', 'cost': 9},
        {'id': 'a_drip', 'name': 'Drip Edge', 'unit': 'LF', 'cost': 2},
        {'id': 'l_install', 'name': 'Install Labor', 'unit': 'SQ', 'cost': 145},
    ],
    'roofing_bundles': [
        {'id': 'b_shingle', 'name': 'Shingle',
         'product_ids': ['m_shingle', 'a_underlay', 'a_drip', 'l_install']},
        {'id': 'b_metal', 'name': 'Metal',
         'product_ids': ['m_metal', 'a_underlay', 'l_install']},
    ],
    'roofing_tier_defaults': {'good': 'b_shingle', 'better': 'b_shingle',
                              'best': 'b_metal'},
}


def _estimate(items=None, sections=None):
    td = {'enabled': True, 'mode': 'gbb', 'line_items': items or [],
          'tier_bundles': {'good': '', 'better': '', 'best': ''}}
    if sections:
        td['sections'] = sections
    return {'trades': {'roofing': td}}


def _run_all(tmp_path, estimate, ops, price_book=None):
    """The whole estimate back, for the ops that answer a question (S._probe)
    rather than rearranging one trade."""
    scenario = tmp_path / 'scenario.json'
    out = tmp_path / 'out.json'
    scenario.write_text(json.dumps({
        'estimate': estimate, 'priceBook': price_book or PRICE_BOOK, 'ops': ops,
    }), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(scenario), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f'bundle_runner.js failed:\n{proc.stderr}'
    return json.loads(out.read_text(encoding='utf-8'))


def _run(tmp_path, estimate, ops, price_book=None):
    return _run_all(tmp_path, estimate, ops, price_book)['trades']['roofing']


def _ids(td):
    return [i.get('catalog_id') or i['name'] for i in td['line_items']]


# ── a bundle row lands in its price-book position ──────────────────────────

def test_a_second_packages_material_does_not_land_at_the_bottom(tmp_path):
    """The reported bug. Shingle on Good, metal on Best: the metal panel used
    to sort after Good's underlayment and drip edge, so the Best card listed
    the roof it sells LAST."""
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'roofing', 'tier': 'good', 'id': 'b_shingle'},
        {'op': 'applyBundle', 'trade': 'roofing', 'tier': 'best', 'id': 'b_metal'}])
    assert _ids(td) == ['m_shingle', 'm_metal', 'a_underlay', 'a_drip', 'l_install']


def test_the_order_is_the_same_whichever_package_was_picked_first(tmp_path):
    """Two reps building the same job in a different order got two different
    estimates. This is the whole point of the change."""
    forward = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'roofing', 'tier': 'good', 'id': 'b_shingle'},
        {'op': 'applyBundle', 'trade': 'roofing', 'tier': 'best', 'id': 'b_metal'}])
    backward = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'roofing', 'tier': 'best', 'id': 'b_metal'},
        {'op': 'applyBundle', 'trade': 'roofing', 'tier': 'good', 'id': 'b_shingle'}])
    assert _ids(forward) == _ids(backward)


def test_a_bundle_on_its_own_still_loads_in_bundle_order(tmp_path):
    td = _run(tmp_path, _estimate(), [
        {'op': 'applyBundle', 'trade': 'roofing', 'tier': 'good', 'id': 'b_shingle'}])
    assert _ids(td) == ['m_shingle', 'a_underlay', 'a_drip', 'l_install']


# ── the Standard order button ──────────────────────────────────────────────

def _hand(name, section=None):
    row = {'id': 'h-' + name, 'name': name, 'unit': 'EA', 'quantity': 1,
           'tiers': {t: {'material_unit_cost': 10, 'included': True}
                     for t in ('good', 'better', 'best')}}
    if section:
        row['section'] = section
    return row


def test_sorting_puts_a_shuffled_estimate_back_in_price_book_order(tmp_path):
    messy = [{'id': 'i1', 'catalog_id': 'l_install', 'name': 'Install Labor'},
             {'id': 'i2', 'catalog_id': 'a_drip', 'name': 'Drip Edge'},
             {'id': 'i3', 'catalog_id': 'm_shingle', 'name': 'Shingles'}]
    td = _run(tmp_path, _estimate(messy),
              [{'op': 'sortStandard', 'trade': 'roofing'}])
    assert _ids(td) == ['m_shingle', 'a_drip', 'l_install']


def test_hand_added_rows_keep_their_own_order_at_the_end(tmp_path):
    """No catalog_id means nothing knows where the row belongs, and the rep who
    typed it does. They stay last, in the order they were added."""
    messy = [_hand('Second extra'), {'id': 'i1', 'catalog_id': 'a_drip', 'name': 'Drip Edge'}]
    messy.insert(0, _hand('First extra'))
    td = _run(tmp_path, _estimate(messy),
              [{'op': 'sortStandard', 'trade': 'roofing'}])
    assert _ids(td) == ['a_drip', 'First extra', 'Second extra']


def test_a_buildings_block_stays_its_own_block(tmp_path):
    """Sections are how a complex keeps seven roofs apart. Sorting across them
    would interleave the buildings — liMove already refuses to cross a section
    for the same reason."""
    rows = [{'id': 'i1', 'catalog_id': 'a_drip', 'name': 'Drip Edge', 'section': 'Garage'},
            {'id': 'i2', 'catalog_id': 'm_shingle', 'name': 'Shingles'},
            {'id': 'i3', 'catalog_id': 'm_shingle', 'name': 'Shingles', 'section': 'Garage'},
            {'id': 'i4', 'catalog_id': 'a_drip', 'name': 'Drip Edge'}]
    td = _run(tmp_path, _estimate(rows, sections=['Garage']),
              [{'op': 'sortStandard', 'trade': 'roofing'}])
    sections = [i.get('section', '') for i in td['line_items']]
    assert sections == ['Garage', 'Garage', '', ''], 'a section block was split'
    assert _ids(td) == ['m_shingle', 'a_drip', 'm_shingle', 'a_drip']


def test_sorting_changes_no_price_and_no_quantity(tmp_path):
    rows = [{'id': 'i1', 'catalog_id': 'l_install', 'name': 'Install Labor',
             'quantity': 30, 'tiers': {'good': {'material_unit_cost': 145, 'included': True}}},
            {'id': 'i2', 'catalog_id': 'm_shingle', 'name': 'Shingles',
             'quantity': 32, 'tiers': {'good': {'material_unit_cost': 150, 'included': True}}}]
    td = _run(tmp_path, _estimate(rows), [{'op': 'sortStandard', 'trade': 'roofing'}])
    by_id = {i['catalog_id']: i for i in td['line_items']}
    assert by_id['m_shingle']['quantity'] == 32
    assert by_id['l_install']['tiers']['good']['material_unit_cost'] == 145


def test_the_button_is_on_the_pricing_tab():
    with open(os.path.join(os.path.dirname(HERE), 'static', 'app.js'),
              encoding='utf-8') as fh:
        js = fh.read()
    assert "onclick=\"sortTradeItemsStandard('${trade}')\"" in js
    assert 'Standard order' in js

# ── Moving a section ───────────────────────────────────────────

def _two_sections():
    """Two named blocks plus an untagged row, in section order Garage, Shed."""
    return [
        {'id': 'g1', 'catalog_id': 'm_shingle', 'name': 'Shingles', 'section': 'Garage'},
        {'id': 'g2', 'catalog_id': 'a_drip', 'name': 'Drip Edge', 'section': 'Garage'},
        {'id': 's1', 'catalog_id': 'm_metal', 'name': 'Standing Seam', 'section': 'Shed'},
        {'id': 'x1', 'catalog_id': 'l_install', 'name': 'Install Labor'},
    ]


def test_moving_a_section_swaps_it_with_its_neighbour(tmp_path):
    td = _run(tmp_path, _estimate(_two_sections(), sections=['Garage', 'Shed']),
              [{'op': 'moveSection', 'trade': 'roofing', 'idx': 0, 'dir': 1}])
    assert td['sections'] == ['Shed', 'Garage']


def test_the_line_item_block_moves_with_the_section(tmp_path):
    """The reason this is not a one-line name swap. The signed contract PDF and
    the invoice print line_items FLAT in stored order with the section name
    suffixed, so reordering only the names would leave those two documents
    describing the old order — one job, two answers."""
    td = _run(tmp_path, _estimate(_two_sections(), sections=['Garage', 'Shed']),
              [{'op': 'moveSection', 'trade': 'roofing', 'idx': 0, 'dir': 1}])
    assert [i.get('section', '') for i in td['line_items']] ==         ['', 'Shed', 'Garage', 'Garage']


def test_a_sections_own_items_keep_their_order_through_a_move(tmp_path):
    td = _run(tmp_path, _estimate(_two_sections(), sections=['Garage', 'Shed']),
              [{'op': 'moveSection', 'trade': 'roofing', 'idx': 0, 'dir': 1}])
    garage = [i['id'] for i in td['line_items'] if i.get('section') == 'Garage']
    assert garage == ['g1', 'g2']


def test_untagged_rows_stay_in_the_general_block_at_the_front(tmp_path):
    """General is hardcoded first in both groupers (app.js and app.py). A move
    must not smuggle an untagged row into a named section."""
    td = _run(tmp_path, _estimate(_two_sections(), sections=['Garage', 'Shed']),
              [{'op': 'moveSection', 'trade': 'roofing', 'idx': 1, 'dir': -1}])
    assert td['line_items'][0]['id'] == 'x1'
    assert 'section' not in td['line_items'][0]


def test_moving_a_section_changes_no_price_and_no_quantity(tmp_path):
    rows = [{'id': 'a1', 'catalog_id': 'm_shingle', 'name': 'Shingles', 'section': 'Garage',
             'quantity': 32, 'tiers': {'good': {'material_unit_cost': 150, 'included': True}}},
            {'id': 'b1', 'catalog_id': 'l_install', 'name': 'Install Labor', 'section': 'Shed',
             'quantity': 30, 'tiers': {'good': {'material_unit_cost': 145, 'included': True}}}]
    td = _run(tmp_path, _estimate(rows, sections=['Garage', 'Shed']),
              [{'op': 'moveSection', 'trade': 'roofing', 'idx': 0, 'dir': 1}])
    by_id = {i['id']: i for i in td['line_items']}
    assert by_id['a1']['quantity'] == 32
    assert by_id['b1']['tiers']['good']['material_unit_cost'] == 145


@pytest.mark.parametrize('idx,dir_', [(0, -1), (1, 1)])
def test_the_arrow_is_dead_at_either_end(tmp_path, idx, dir_):
    out = _run_all(tmp_path, _estimate(_two_sections(), sections=['Garage', 'Shed']),
                   [{'op': 'canMoveSection', 'trade': 'roofing', 'idx': idx, 'dir': dir_}])
    assert out['_probe'] is False


def test_a_move_off_the_end_rearranges_nothing(tmp_path):
    td = _run(tmp_path, _estimate(_two_sections(), sections=['Garage', 'Shed']),
              [{'op': 'moveSection', 'trade': 'roofing', 'idx': 0, 'dir': -1}])
    assert td['sections'] == ['Garage', 'Shed']
    assert [i['id'] for i in td['line_items']] == ['g1', 'g2', 's1', 'x1']


def test_the_arrows_are_on_the_pricing_tab():
    with open(os.path.join(os.path.dirname(HERE), 'static', 'app.js'),
              encoding='utf-8') as fh:
        js = fh.read()
    assert "moveTradeSection('${trade}',${i},-1)" in js
    assert "moveTradeSection('${trade}',${i},1)" in js
    assert 'canMoveTradeSection(trade, i, -1)' in js
