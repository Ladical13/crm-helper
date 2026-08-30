"""Visualizer — the product-photo overlay that ships with the /sign page and the signed PDF.

The rep uploads a house photo, selects roof, siding, trim/fascia, soffit, and
door surfaces, picks a product/color per tier (and, for siding, a style), and
saves three composites — one per
Good/Better/Best tier. Server-side we store the pointers under
`est['visualizer']` and embed the renders into the customer-facing PDF and
sign page.

Invariants worth pinning:
  * `colors` (roofing + siding) and `styles` (siding) survive on a live
    price book — a book saved before the fields existed picks them up on
    the next GET. Without this the field pickers stay empty forever.
  * The `visualizer` block round-trips through PUT /api/estimates unchanged —
    it's not on any server-managed whitelist, and _merge does not strip
    unknown top-level keys. If somebody adds a whitelist someday, this
    catches it.
  * POST .../visualizer/asset decodes base64, writes to UPLOADS_DIR/<est>/
    and updates the pointer in est.visualizer.
  * When tier_renders are present the signed PDF is meaningfully bigger
    (an image was embedded), and the /sign page emits an <img> pointing
    at /uploads/.
"""
import base64
import copy
import io
import json
import os

import pytest

from conftest import TEST_DATA_DIR


# ── seed backfill ─────────────────────────────────────────────────────

def test_colors_backfill_onto_a_live_roofing_book(client, A):
    """A price book that shipped before `colors` existed must adopt the
    seed's colors on the next GET — otherwise every existing manager's
    Landmark product renders an empty color picker."""
    book = client.get('/api/pricebook').get_json()
    landmark = next(p for p in book['roofing_catalog'] if p['id'] == 'm_landmark')
    landmark.pop('colors', None)
    r = client.put('/api/pricebook', json=book)
    assert r.status_code == 200

    fresh = client.get('/api/pricebook').get_json()
    fresh_landmark = next(p for p in fresh['roofing_catalog'] if p['id'] == 'm_landmark')
    assert isinstance(fresh_landmark.get('colors'), list) and fresh_landmark['colors']
    assert all(isinstance(c, dict) and c.get('hex') for c in fresh_landmark['colors'])


def test_styles_backfill_onto_a_live_siding_book(client, A):
    """Siding styles (lap / B&B / shake / panel) are the visualizer's other
    axis for siding — a Hardie ColorPlus lap and a Hardie ColorPlus
    board-and-batten are the same color but different renders. Without
    styles the style row on the picker is empty."""
    book = client.get('/api/pricebook').get_json()
    hardie = next(p for p in book['siding_catalog'] if p['id'] == 's_hardie_primed')
    hardie.pop('styles', None)
    hardie.pop('colors', None)
    r = client.put('/api/pricebook', json=book)
    assert r.status_code == 200

    fresh = client.get('/api/pricebook').get_json()
    fresh_hardie = next(p for p in fresh['siding_catalog'] if p['id'] == 's_hardie_primed')
    assert isinstance(fresh_hardie.get('styles'), list) and fresh_hardie['styles']
    assert isinstance(fresh_hardie.get('colors'), list) and fresh_hardie['colors']
    assert {s['id'] for s in fresh_hardie['styles']} >= {'s_lap', 's_bnb', 's_shake', 's_panel'}


def test_seed_colors_are_deep_copied(A):
    """Regression: the copy helpers must not share the color/style dicts
    with the seed, or a client PUT that edits a color mutates the seed
    constant for every other request in the process."""
    seed = A.ROOFING_CATALOG_SEED
    landmark_seed = next(p for p in seed if p['id'] == 'm_landmark')
    copied = A._copy_seed_product(landmark_seed)
    copied['colors'][0]['name'] = 'MUTATED'
    assert landmark_seed['colors'][0]['name'] != 'MUTATED'


# ── estimate round-trip ────────────────────────────────────────────────

def test_visualizer_block_round_trips_through_put(client):
    """The whole `visualizer` sub-doc must be preserved by a normal
    estimate PUT — the frontend PUTs the entire estimate on save, and the
    server does not whitelist visualizer under SERVER_MANAGED_FIELDS."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['visualizer'] = {
            'base_image': f'{eid}/vb_test.jpg',
            'roof_mask':  f'{eid}/vm_roof.png',
            'siding_mask': f'{eid}/vm_side.png',
            'selections': {
                'roofing': {'better': {'bundle_id': 'b_northgate',
                                       'color_hex': '#2f2d2b',
                                       'color_name': 'Charcoal Black'}},
                'siding': {'better': {'bundle_id': 'b_hardie_primed',
                                      'style_id': 's_lap',
                                      'style_name': 'Lap Siding',
                                      'color_hex': '#4a4d4f',
                                      'color_name': 'Iron Gray'}},
            },
            'tier_renders': {'better': f'{eid}/vr_better.jpg'},
        }
        assert client.put(f'/api/estimates/{eid}', json=doc).status_code == 200
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['visualizer']['selections']['roofing']['better']['color_hex'] == '#2f2d2b'
        assert fresh['visualizer']['tier_renders']['better'] == f'{eid}/vr_better.jpg'
    finally:
        client.delete(f'/api/estimates/{eid}')


# ── asset + state endpoints ────────────────────────────────────────────

# One-pixel JPEG (base64) — a real, valid image so the asset endpoint's
# strict base64 validation and file write run end-to-end without loading a
# camera photo. Generated once with PIL.new('RGB',(1,1)).save('JPEG').
_ONE_PX_JPEG_B64 = (
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHR'
    'ofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgy'
    'IRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMj'
    'L/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/'
    '8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAk'
    'M2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dn'
    'd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW'
    '19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBg'
    'cICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHB'
    'CSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2'
    'hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbH'
    'yMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDnqKKK8k/Qz//Z'
)


def test_asset_endpoint_writes_the_upload_and_updates_the_pointer(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        r = client.post(
            f'/api/estimates/{eid}/visualizer/asset',
            json={'kind': 'render', 'tier': 'better', 'ext': 'jpg',
                  'content_b64': _ONE_PX_JPEG_B64},
        )
        assert r.status_code == 201, r.get_data(as_text=True)
        body = r.get_json()
        assert body['url'].startswith(f'/uploads/{eid}/')
        # The file lives on disk under UPLOADS_DIR/<est>/.
        rel = body['filename']
        on_disk = os.path.join(TEST_DATA_DIR, 'uploads', rel)
        assert os.path.isfile(on_disk)
        # And est.visualizer.tier_renders.better points at it.
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['visualizer']['tier_renders']['better'] == rel
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_asset_endpoint_rejects_junk_base64(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        r = client.post(
            f'/api/estimates/{eid}/visualizer/asset',
            json={'kind': 'render', 'tier': 'good', 'ext': 'jpg',
                  'content_b64': 'not-real-base64!!!'},
        )
        assert r.status_code == 400
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_asset_endpoint_validates_kind_role_tier(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        # mask without role
        r = client.post(f'/api/estimates/{eid}/visualizer/asset',
                        json={'kind': 'mask', 'ext': 'png',
                              'content_b64': _ONE_PX_JPEG_B64})
        assert r.status_code == 400
        # render without tier
        r = client.post(f'/api/estimates/{eid}/visualizer/asset',
                        json={'kind': 'render', 'ext': 'jpg',
                              'content_b64': _ONE_PX_JPEG_B64})
        assert r.status_code == 400
        # unknown kind
        r = client.post(f'/api/estimates/{eid}/visualizer/asset',
                        json={'kind': 'garbage', 'ext': 'jpg',
                              'content_b64': _ONE_PX_JPEG_B64})
        assert r.status_code == 400
    finally:
        client.delete(f'/api/estimates/{eid}')


@pytest.mark.parametrize('role', ['roof', 'siding', 'trim', 'soffit', 'door'])
def test_asset_endpoint_accepts_and_stores_each_surface_mask(client, role):
    """Every independently selectable surface has its own stored mask."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        r = client.post(
            f'/api/estimates/{eid}/visualizer/asset',
            json={'kind': 'mask', 'role': role, 'ext': 'png',
                  'content_b64': _ONE_PX_JPEG_B64},
        )
        assert r.status_code == 201, r.get_data(as_text=True)
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['visualizer'][f'{role}_mask'] == r.get_json()['filename']
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_pricebook_exposes_door_options_for_the_designer(client):
    """Exterior door visual choices must arrive with every live price book."""
    book = client.get('/api/pricebook').get_json()
    assert book['exterior_doors']
    assert all(d.get('name') and d.get('brand') == 'ProVia' and d.get('colors')
               for d in book['exterior_doors'])


def test_pricebook_seeds_the_manager_exterior_catalog(client):
    """Existing roof/siding/ProVia palettes become uploader rows on first use."""
    book = client.get('/api/pricebook').get_json()
    rows = book['exterior_catalog']
    assert rows
    assert {row['category'] for row in rows} >= {'roof', 'siding', 'door'}
    assert all(row.get('product_id', '').startswith('extp_') for row in rows)
    assert all(repr(row.get('hex', '')).startswith("'#") and len(row['hex']) == 7 for row in rows)


def test_lp_expertfinish_seed_matches_the_2025_sales_sheet(A):
    expected_colors = [
        'Snowscape White', 'Sand Dunes', 'Desert Stone', 'Quarry Gray',
        'Prairie Clay', 'Terra Brown', 'Harvest Honey', 'Timberland Suede',
        'Garden Sage', 'Redwood Red', 'Tundra Gray', 'Summit Blue',
        'Rapids Blue', 'Cavern Steel', 'Midnight Shadow', 'Abyss Black',
    ]
    expected_styles = [
        'Lap Joint Siding - 6 in.', 'Lap Joint Siding - 8 in.',
        'Shakes - Straight Edge', 'Shakes - Staggered Edge',
        'Panel - NGSE 4 x 8 ft.', 'Panel - NGSE 4 x 10 ft.',
        'Nickel Gap - 8 in.', 'Vertical Siding - 16 in.',
    ]
    product = next(p for p in A.SIDING_CATALOG_SEED if p['id'] == 's_lp_expert')

    assert [c['name'] for c in product['colors']] == expected_colors
    assert product['colors'][0]['hex'] == '#f2f1f1'
    assert product['colors'][-1]['hex'] == '#2b3131'
    assert [s['name'] for s in product['styles']] == expected_styles
    assert {s['pattern_id'] for s in product['styles']} == {
        'lap', 'shake_straight', 'shake_staggered', 'panel', 'nickel_gap'}
    assert '5/15/50' in ' '.join(product['bullets'])


def test_lp_expertfinish_migrates_legacy_visuals_without_touching_price_or_custom_rows(A):
    legacy_product = {
        'id': 's_lp_expert', 'name': 'LP SmartSide Expert Finish 8" Lap',
        'unit': 'SQ', 'cost': 999.99,
        'colors': copy.deepcopy(A._SIDING_NEUTRAL_COLORS),
        'styles': copy.deepcopy(A._LP_STANDARD_STYLES),
    }
    legacy_rows = [
        {
            'category': 'siding', 'brand': 'LP SmartSide',
            'product': 'LP SmartSide Expert Finish', 'style': style['name'],
            'pattern_id': style['pattern_id'], 'color': color['name'],
            'hex': color['hex'], 'price_book_bundle': 'b_lp_expert',
        }
        for style in A._LP_STANDARD_STYLES
        for color in A._SIDING_NEUTRAL_COLORS
    ]
    custom = {
        'category': 'siding', 'brand': 'LP SmartSide',
        'product': 'LP SmartSide Expert Finish', 'style': 'Custom Profile',
        'color': 'Project One Blue', 'hex': '#123456',
        'price_book_bundle': 'b_lp_expert',
    }
    pb = {
        'siding_catalog': [legacy_product],
        'siding_bundles': [{
            'id': 'b_lp_expert', 'name': 'LP SmartSide Expert Finish',
            'product_ids': ['s_lp_expert'],
        }],
        'siding_tier_defaults': {},
        'exterior_catalog': A._normalize_exterior_catalog(legacy_rows + [custom]),
    }

    A._ensure_bundle_catalogs(pb)

    live = next(p for p in pb['siding_catalog'] if p['id'] == 's_lp_expert')
    assert live['cost'] == 999.99
    assert [c['name'] for c in live['colors']] == [
        c['name'] for c in A._LP_EXPERTFINISH_COLORS]
    assert [s['name'] for s in live['styles']] == [
        s['name'] for s in A._LP_EXPERTFINISH_STYLES]
    official = [r for r in pb['exterior_catalog']
                if r['product'] == 'LP SmartSide ExpertFinish']
    assert len(official) == 16 * 8
    assert {r['pattern_id'] for r in official} == {
        'lap', 'shake_straight', 'shake_staggered', 'panel', 'nickel_gap'}
    assert not any(r['product'] == 'LP SmartSide Expert Finish'
                   and r['color'] == 'Arctic White'
                   for r in pb['exterior_catalog'])
    assert any(r['color'] == 'Project One Blue' and r['hex'] == '#123456'
               for r in pb['exterior_catalog'])
    assert A._LP_EXPERTFINISH_EXTERIOR_MIGRATION in (
        pb['exterior_catalog_seed_versions'])

    # Once the version marker is saved, a manager may delete a seeded row and
    # it stays deleted on subsequent reads.
    pb['exterior_catalog'] = [r for r in pb['exterior_catalog']
                              if not (r['product'] == 'LP SmartSide ExpertFinish'
                                      and r['style'] == 'Shakes - Staggered Edge'
                                      and r['color'] == 'Abyss Black')]
    A._ensure_bundle_catalogs(pb)
    assert not any(r['product'] == 'LP SmartSide ExpertFinish'
                   and r['style'] == 'Shakes - Staggered Edge'
                   and r['color'] == 'Abyss Black'
                   for r in pb['exterior_catalog'])


def test_hardie_statement_seed_matches_the_2026_regional_catalog(A):
    product = next(p for p in A.SIDING_CATALOG_SEED
                   if p['id'] == 's_hardie_statement')
    assert [c['name'] for c in product['colors']] == [
        'Arctic White', 'Cobble Stone', 'Navajo Beige', 'Khaki Brown',
        'Monterey Taupe', 'Pearl Gray', 'Timber Bark', 'Rich Espresso',
        'Mountain Sage', 'Gray Slate', 'Light Mist', 'Boothbay Blue',
        'Night Gray', 'Evening Blue', 'Aged Pewter', 'Iron Gray',
        'Countrylane Red',
    ]
    assert product['colors'][0]['hex'] == '#f0f0e4'
    assert product['colors'][-1]['hex'] == '#713c37'
    assert [s['name'] for s in product['styles']] == [
        'Hardie Plank - Select Cedarmill', 'Hardie Plank - Smooth',
        'Hardie Panel + Trim Batten - Rustic Grain',
        'Hardie Panel + Trim Batten - Smooth',
        'Hardie Shingle - Straight Edge Panel',
        'Hardie Shingle - Staggered Edge Panel',
        'Hardie Panel - Select Cedarmill', 'Hardie Panel - Smooth',
        'Hardie Panel - Sierra 8',
    ]
    assert [s['pattern_id'] for s in product['styles']] == [
        'lap', 'lap', 'bnb', 'bnb', 'shake_straight', 'shake_staggered',
        'panel', 'panel', 'sierra8']
    copy_text = ' '.join(product['bullets'])
    assert 'HZ5' in copy_text
    assert 'noncombustible and/or Class A' in copy_text
    assert '30-year non-prorated limited substrate warranty' in copy_text
    assert '15-year limited ColorPlus finish warranty' in copy_text
    assert 'rot proof' not in copy_text


def test_new_pricebook_exposes_one_official_hardie_statement_palette(client):
    rows = client.get('/api/pricebook').get_json()['exterior_catalog']
    hardie = [r for r in rows if r['category'] == 'siding'
              and r['price_book_bundle'] == 'b_hardie_statement']
    assert len(hardie) == 17 * 9
    assert {r['brand'] for r in hardie} == {'James Hardie'}
    assert {r['product'] for r in hardie} == {
        'James Hardie Statement Collection'}
    assert {r['style'] for r in hardie} == {
        'Hardie Plank - Select Cedarmill', 'Hardie Plank - Smooth',
        'Hardie Panel + Trim Batten - Rustic Grain',
        'Hardie Panel + Trim Batten - Smooth',
        'Hardie Shingle - Straight Edge Panel',
        'Hardie Shingle - Staggered Edge Panel',
        'Hardie Panel - Select Cedarmill', 'Hardie Panel - Smooth',
        'Hardie Panel - Sierra 8'}
    assert {r['pattern_id'] for r in hardie} == {
        'lap', 'bnb', 'shake_straight', 'shake_staggered', 'panel', 'sierra8'}


def test_hardie_and_lp_component_palettes_follow_the_supplied_sheets(client):
    rows = client.get('/api/pricebook').get_json()['exterior_catalog']

    def matching(bundle, category):
        return [r for r in rows if r['price_book_bundle'] == bundle
                and r['category'] == category]

    lp_trim = matching('b_lp_expert', 'trim')
    lp_soffit = matching('b_lp_expert', 'soffit')
    assert len(lp_trim) == 16
    assert len(lp_soffit) == 16 * 2
    assert {r['style'] for r in lp_trim} == {'Trim & Fascia'}
    assert {r['style'] for r in lp_soffit} == {
        'Closed - 12, 16 or 24 in.', 'Vented - 12, 16 or 24 in.'}

    hardie_trim = matching('b_hardie_statement', 'trim')
    hardie_soffit = matching('b_hardie_statement', 'soffit')
    assert len(hardie_trim) == 4 * 2
    assert {r['color'] for r in hardie_trim} == {
        'Arctic White', 'Cobble Stone', 'Iron Gray', 'Timber Bark'}
    assert {r['style'] for r in hardie_trim} == {'Rustic Grain', 'Smooth'}
    assert len(hardie_soffit) == 4
    assert {r['color'] for r in hardie_soffit} == {'Arctic White'}
    assert {r['style'] for r in hardie_soffit} == {
        'Vented Smooth', 'Non-Vented Smooth',
        'Non-Vented Select Cedarmill', 'Vented Select Cedarmill'}


def test_component_migration_preserves_custom_rows_and_manager_deletions(A):
    custom = {
        'category': 'trim', 'brand': 'Local Supplier',
        'product': 'Custom Aluminum Fascia', 'style': '6 in.',
        'color': 'Project One Blue', 'hex': '#123456',
        'price_book_bundle': 'b_lp_expert',
    }
    pb = {
        'siding_catalog': [{
            'id': 's_lp_expert', 'cost': 777,
            'styles': copy.deepcopy(A._LP_EXPERTFINISH_LEGACY_STYLES),
        }, {
            'id': 's_hardie_statement', 'cost': 888,
            'styles': copy.deepcopy(A._HARDIE_STATEMENT_LEGACY_STYLES),
        }],
        'siding_bundles': [],
        'siding_tier_defaults': {},
        'exterior_catalog': A._normalize_exterior_catalog([custom]),
        'exterior_catalog_seed_versions': [
            A._LP_EXPERTFINISH_EXTERIOR_MIGRATION,
            A._HARDIE_STATEMENT_EXTERIOR_MIGRATION,
        ],
    }

    A._ensure_bundle_catalogs(pb)

    lp = next(p for p in pb['siding_catalog'] if p['id'] == 's_lp_expert')
    hardie = next(p for p in pb['siding_catalog']
                  if p['id'] == 's_hardie_statement')
    assert lp['cost'] == 777 and lp['styles'] == A._LP_EXPERTFINISH_STYLES
    assert hardie['cost'] == 888 and hardie['styles'] == A._HARDIE_STATEMENT_STYLES
    assert any(r['product'] == 'Custom Aluminum Fascia'
               for r in pb['exterior_catalog'])
    assert A._SIDING_COMPONENT_EXTERIOR_MIGRATION in (
        pb['exterior_catalog_seed_versions'])

    pb['exterior_catalog'] = [
        r for r in pb['exterior_catalog']
        if not (r['product'] == 'Hardie Trim'
                and r['style'] == 'Smooth'
                and r['color'] == 'Iron Gray')]
    A._ensure_bundle_catalogs(pb)
    assert not any(r['product'] == 'Hardie Trim'
                   and r['style'] == 'Smooth'
                   and r['color'] == 'Iron Gray'
                   for r in pb['exterior_catalog'])


def test_hardie_statement_migrates_legacy_visuals_without_touching_price_or_custom_rows(A):
    legacy_product = {
        'id': 's_hardie_statement',
        'name': 'James Hardie Statement Collection 8.25" Lap',
        'unit': 'SQ', 'cost': 888.88,
        'colors': copy.deepcopy(A._SIDING_NEUTRAL_COLORS),
        'styles': copy.deepcopy(A._HARDIE_STYLES),
        'bullets': copy.deepcopy(A._HARDIE_STATEMENT_LEGACY_BULLETS),
    }
    legacy_rows = [
        {
            'category': 'siding', 'brand': 'James Hardie',
            'product': 'James Hardie Statement Collection',
            'style': style['name'], 'pattern_id': style['pattern_id'],
            'color': color['name'], 'hex': color['hex'],
            'price_book_bundle': 'b_hardie_statement',
        }
        for style in A._HARDIE_STYLES
        for color in A._SIDING_NEUTRAL_COLORS
    ]
    custom = {
        'category': 'siding', 'brand': 'James Hardie',
        'product': 'James Hardie Statement Collection',
        'style': 'Custom Profile', 'color': 'Project One Blue',
        'hex': '#123456', 'price_book_bundle': 'b_hardie_statement',
    }
    pb = {
        'siding_catalog': [legacy_product],
        'siding_bundles': [{
            'id': 'b_hardie_statement',
            'name': 'James Hardie Statement Collection',
            'product_ids': ['s_hardie_statement'],
            'description': A._HARDIE_STATEMENT_LEGACY_DESCRIPTION,
        }],
        'siding_tier_defaults': {},
        'exterior_catalog': A._normalize_exterior_catalog(
            legacy_rows + [custom]),
    }

    A._ensure_bundle_catalogs(pb)

    live = next(p for p in pb['siding_catalog']
                if p['id'] == 's_hardie_statement')
    assert live['cost'] == 888.88
    assert live['colors'] == A._HARDIE_STATEMENT_COLORS
    assert live['styles'] == A._HARDIE_STATEMENT_STYLES
    assert live['bullets'] == A._HARDIE_STATEMENT_BULLETS
    bundle = next(b for b in pb['siding_bundles']
                  if b['id'] == 'b_hardie_statement')
    assert '30-year non-prorated limited coverage' in bundle['description']
    official = [r for r in pb['exterior_catalog']
                if r['product'] == 'James Hardie Statement Collection'
                and r['category'] == 'siding'
                and r['style'] != 'Custom Profile']
    assert len(official) == 17 * 9
    assert not any(r['style'] == 'Lap Siding' and r['color'] == 'Sail Cloth'
                   for r in pb['exterior_catalog'])
    assert any(r['style'] == 'Custom Profile'
               and r['color'] == 'Project One Blue'
               and r['hex'] == '#123456'
               for r in pb['exterior_catalog'])
    assert A._HARDIE_STATEMENT_EXTERIOR_MIGRATION in (
        pb['exterior_catalog_seed_versions'])

    # A saved version marker means manager deletions remain intentional.
    pb['exterior_catalog'] = [
        r for r in pb['exterior_catalog']
        if not (r['style'] == 'Hardie Shingle - Staggered Edge Panel'
                and r['color'] == 'Countrylane Red')]
    A._ensure_bundle_catalogs(pb)
    assert not any(r['style'] == 'Hardie Shingle - Staggered Edge Panel'
                   and r['color'] == 'Countrylane Red'
                   for r in pb['exterior_catalog'])


def test_landmark_seed_matches_the_2026_brochure(A):
    product = next(p for p in A.ROOFING_CATALOG_SEED
                   if p['id'] == 'm_landmark')
    assert [c['name'] for c in product['colors']] == [
        'Silver Birch', 'Georgetown Gray', 'Weathered Wood',
        'Heather Blend', 'Burnt Sienna', 'Resawn Shake',
        'Driftwood', 'Moiré Black', 'Black Walnut',
    ]
    assert product['colors'][0]['hex'] == '#a6aaa8'
    assert product['colors'][-1]['hex'] == '#383934'
    copy_text = ' '.join(product['bullets'])
    assert 'UL 2218 Class 3' in copy_text
    assert 'NailTrak' in copy_text
    assert 'QuadraBond' in copy_text
    assert 'CertaSeal' in copy_text
    assert '10-year SureStart' in copy_text
    assert '25-year StreakFighter' in copy_text
    assert '15-year 110 mph' in copy_text
    assert '130 mph' not in copy_text


def test_new_pricebook_exposes_one_official_landmark_palette(client):
    rows = client.get('/api/pricebook').get_json()['exterior_catalog']
    landmark = [r for r in rows if r['price_book_bundle'] == 'b_landmark']
    assert len(landmark) == 9
    assert {r['brand'] for r in landmark} == {'CertainTeed'}
    assert {r['product'] for r in landmark} == {'Landmark'}
    assert {r['style'] for r in landmark} == {'Architectural Shingle'}


def test_landmark_migrates_legacy_visuals_without_touching_price_or_custom_rows(A):
    legacy_product = {
        'id': 'm_landmark', 'name': 'CertainTeed Landmark (Architectural Shingle)',
        'unit': 'SQ', 'cost': 777.77,
        'colors': copy.deepcopy(A._ROOF_ASPHALT_COLORS),
        'bullets': copy.deepcopy(A._LANDMARK_LEGACY_BULLETS),
    }
    legacy_rows = [
        {
            'category': 'roof', 'brand': '',
            'product': 'CertainTeed Landmark',
            'color': color['name'], 'hex': color['hex'],
            'price_book_bundle': 'b_landmark',
        }
        for color in A._ROOF_ASPHALT_COLORS
    ]
    custom = {
        'category': 'roof', 'brand': 'CertainTeed', 'product': 'Landmark',
        'style': 'Custom Blend', 'color': 'Project One Blend',
        'hex': '#123456', 'price_book_bundle': 'b_landmark',
    }
    pb = {
        'roofing_catalog': [legacy_product],
        'roofing_bundles': [{
            'id': 'b_landmark', 'name': 'CertainTeed Landmark',
            'product_ids': ['m_landmark'],
            'description': A._LANDMARK_LEGACY_DESCRIPTION,
        }],
        'roofing_tier_defaults': {},
        'exterior_catalog': A._normalize_exterior_catalog(legacy_rows + [custom]),
    }

    A._ensure_bundle_catalogs(pb)

    live = next(p for p in pb['roofing_catalog'] if p['id'] == 'm_landmark')
    assert live['cost'] == 777.77
    assert live['colors'] == A._LANDMARK_COLORS
    assert live['bullets'] == A._LANDMARK_BULLETS
    bundle = next(b for b in pb['roofing_bundles'] if b['id'] == 'b_landmark')
    assert 'Class 3 impact resistance' in bundle['description']
    official = [r for r in pb['exterior_catalog']
                if r['product'] == 'Landmark'
                and r['style'] == 'Architectural Shingle']
    assert len(official) == 9
    assert {r['brand'] for r in official} == {'CertainTeed'}
    assert not any(r['product'] == 'CertainTeed Landmark' and not r['style']
                   and r['color'] == 'Charcoal Black'
                   for r in pb['exterior_catalog'])
    assert any(r['style'] == 'Custom Blend'
               and r['color'] == 'Project One Blend'
               and r['hex'] == '#123456'
               for r in pb['exterior_catalog'])
    assert A._LANDMARK_EXTERIOR_MIGRATION in (
        pb['exterior_catalog_seed_versions'])

    # A saved version marker means manager deletions remain intentional.
    pb['exterior_catalog'] = [r for r in pb['exterior_catalog']
                              if not (r['style'] == 'Architectural Shingle'
                                      and r['color'] == 'Black Walnut')]
    A._ensure_bundle_catalogs(pb)
    assert not any(r['style'] == 'Architectural Shingle'
                   and r['color'] == 'Black Walnut'
                   for r in pb['exterior_catalog'])


def test_iko_nordic_seed_matches_the_2026_brochure(A):
    product = next(p for p in A.ROOFING_CATALOG_SEED
                   if p['id'] == 'm_iko_nordic')
    assert [c['name'] for c in product['colors']] == [
        'Olde Style Weatherwood', 'Summit Grey', 'Granite Black',
        'Driftshake', 'Shadow Brown', 'Glacier',
    ]
    assert product['colors'][0]['hex'] == '#524d46'
    assert product['colors'][-1]['hex'] == '#34332c'
    copy_text = ' '.join(product['bullets'])
    assert 'polymer-modified asphalt' in copy_text
    assert 'ArmourZone' in copy_text
    assert '130 mph limited wind warranty' in copy_text
    assert '15 years of Iron Clad Protection' in copy_text
    assert '10-year limited blue-green algae resistance warranty' in copy_text
    assert 'freeze-thaw' not in copy_text


def test_new_pricebook_exposes_one_official_iko_nordic_palette(client):
    rows = client.get('/api/pricebook').get_json()['exterior_catalog']
    iko = [r for r in rows if r['price_book_bundle'] == 'b_iko_nordic']
    assert len(iko) == 6
    assert {r['brand'] for r in iko} == {'IKO'}
    assert {r['style'] for r in iko} == {'Performance Shingle'}


def test_iko_nordic_migrates_legacy_visuals_without_touching_price_or_custom_rows(A):
    legacy_product = {
        'id': 'm_iko_nordic', 'name': 'IKO Nordic (Impact-Resistant Shingle)',
        'unit': 'SQ', 'cost': 999.99,
        'colors': copy.deepcopy(A._ROOF_ASPHALT_COLORS),
        'bullets': copy.deepcopy(A._IKO_NORDIC_LEGACY_BULLETS),
    }
    legacy_rows = [
        {
            'category': 'roof', 'brand': '', 'product': 'IKO Nordic',
            'color': color['name'], 'hex': color['hex'],
            'price_book_bundle': 'b_iko_nordic',
        }
        for color in A._ROOF_ASPHALT_COLORS
    ]
    custom = {
        'category': 'roof', 'brand': 'IKO', 'product': 'IKO Nordic',
        'style': 'Custom Blend', 'color': 'Project One Blend',
        'hex': '#123456', 'price_book_bundle': 'b_iko_nordic',
    }
    pb = {
        'roofing_catalog': [legacy_product],
        'roofing_bundles': [{
            'id': 'b_iko_nordic', 'name': 'IKO Nordic',
            'product_ids': ['m_iko_nordic'],
            'description': A._IKO_NORDIC_LEGACY_DESCRIPTION,
        }],
        'roofing_tier_defaults': {},
        'exterior_catalog': A._normalize_exterior_catalog(legacy_rows + [custom]),
    }

    A._ensure_bundle_catalogs(pb)

    live = next(p for p in pb['roofing_catalog'] if p['id'] == 'm_iko_nordic')
    assert live['cost'] == 999.99
    assert live['colors'] == A._IKO_NORDIC_COLORS
    assert live['bullets'] == A._IKO_NORDIC_BULLETS
    bundle = next(b for b in pb['roofing_bundles'] if b['id'] == 'b_iko_nordic')
    assert 'ArmourZone' in bundle['description']
    official = [r for r in pb['exterior_catalog']
                if r['product'] == 'IKO Nordic'
                and r['style'] == 'Performance Shingle']
    assert len(official) == 6
    assert {r['brand'] for r in official} == {'IKO'}
    assert not any(r['product'] == 'IKO Nordic' and not r['style']
                   and r['color'] == 'Weathered Wood'
                   for r in pb['exterior_catalog'])
    assert any(r['style'] == 'Custom Blend'
               and r['color'] == 'Project One Blend'
               and r['hex'] == '#123456'
               for r in pb['exterior_catalog'])
    assert A._IKO_NORDIC_EXTERIOR_MIGRATION in (
        pb['exterior_catalog_seed_versions'])

    # A saved version marker means manager deletions remain intentional.
    pb['exterior_catalog'] = [r for r in pb['exterior_catalog']
                              if not (r['style'] == 'Performance Shingle'
                                      and r['color'] == 'Glacier')]
    A._ensure_bundle_catalogs(pb)
    assert not any(r['style'] == 'Performance Shingle'
                   and r['color'] == 'Glacier'
                   for r in pb['exterior_catalog'])


def test_manager_can_replace_and_import_exterior_catalog(client, A):
    original = client.get('/api/exterior-catalog').get_json()['entries']
    try:
        rows = [
            {'category': 'roof', 'brand': 'Test Roofing', 'product': 'Storm 1',
             'style': 'Architectural', 'color': 'Graphite', 'color_code': 'G1',
             'hex': '#112233', 'price_book_bundle': 'b_landmark', 'active': True},
            {'category': 'paint', 'brand': 'Test Paint', 'product': 'Exterior',
             'style': 'Satin', 'color': 'Night', 'color_code': 'P9',
             'hex': '#223344', 'applies_to': 'door', 'active': 'yes'},
        ]
        saved = client.put('/api/exterior-catalog', json={'entries': rows})
        assert saved.status_code == 200, saved.get_json()
        body = saved.get_json()
        assert body['count'] == 2
        assert body['entries'][1]['applies_to'] == 'door'
        assert body['entries'][0]['id'].startswith('ext_')

        imported = client.post('/api/exterior-catalog/import', json={'rows': [
            dict(rows[0], hex='#334455'),
            {'category': 'doors', 'brand': 'ProVia', 'product': 'Legacy',
             'style': '440', 'color': 'Coal Black', 'hex': '#242424'},
        ]})
        assert imported.status_code == 200, imported.get_json()
        result = imported.get_json()
        assert result['imported'] == 2 and result['added'] == 1 and result['count'] == 3
        graphite = next(e for e in result['entries'] if e['color'] == 'Graphite')
        assert graphite['hex'] == '#334455'
        assert any(e['category'] == 'door' and e['product'] == 'Legacy'
                   for e in result['entries'])
    finally:
        assert client.put('/api/exterior-catalog', json={'entries': original}).status_code == 200


def test_exterior_catalog_rejects_bad_rows_and_rep_writes(client, A, monkeypatch):
    bad = client.put('/api/exterior-catalog', json={'entries': [{
        'category': 'roof', 'product': 'Test', 'color': 'Black', 'hex': 'black'}]})
    assert bad.status_code == 400
    assert 'hex' in bad.get_json()['error']
    bad_paint = client.post('/api/exterior-catalog/import', json={'rows': [{
        'category': 'paint', 'product': 'Test', 'color': 'Blue', 'hex': '#112233',
        'applies_to': 'roof'}]})
    assert bad_paint.status_code == 400
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **kw: False)
    assert client.put('/api/exterior-catalog', json={'entries': []}).status_code == 403
    assert client.post('/api/exterior-catalog/import', json={'rows': []}).status_code == 403
    assert client.get('/api/exterior-catalog/template.csv').status_code == 403


def test_exterior_catalog_template_and_frontend_wiring(client):
    response = client.get('/api/exterior-catalog/template.csv')
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert text.startswith('category,brand,product,style,color,color_code,hex')
    assert 'ProVia' in text and 'Sherwin-Williams' in text
    assert '\r\ntrim,' in text and '\r\nsoffit,' in text
    js = open(os.path.join(os.path.dirname(__file__), '..', 'static', 'app.js'),
              encoding='utf-8').read()
    html = open(os.path.join(os.path.dirname(__file__), '..', 'static', 'index.html'),
                encoding='utf-8').read()
    assert 'function exParseCsv' in js
    assert "fetch('/api/exterior-catalog/import'" in js
    assert 'nickel_gap:' in js
    assert 'function _vzExteriorGroups' in js
    assert 'exterior-catalog-modal' in html and 'openExteriorCatalog()' in html


def test_provia_replaces_placeholder_menu_without_mutating_custom_or_saved_choices(A):
    book = {'exterior_doors': [{'id': 'steel-6-panel'}, {'id': 'custom-door', 'name': 'Dealer custom'}]}
    A._ensure_bundle_catalogs(book)
    assert {d['id'] for d in book['exterior_doors']} == {
        'custom-door', 'provia-signet', 'provia-ascent', 'provia-legacy'}
    assert all(not d.get('pattern_id') for d in book['exterior_doors'] if d.get('brand') == 'ProVia')
    book['exterior_doors'][1]['colors'][0]['name'] = 'Manager edit'
    assert A.EXTERIOR_DOOR_OPTIONS_SEED[0]['colors'][0]['name'] == 'Snow Mist'
    empty = {'exterior_doors': []}
    A._ensure_bundle_catalogs(empty)
    assert empty['exterior_doors'] == []


@pytest.fixture
def detector(monkeypatch):
    from estimator import exterior_detection as d
    monkeypatch.setenv('EXTERIOR_AUTO_DETECT', '1')
    monkeypatch.setenv('FAL_KEY', 'test-only-not-a-live-key')
    # All tests must explicitly stub inference. No real customer image or
    # billable model call can leave the suite even if local env keys exist.
    monkeypatch.setattr(d, '_json', lambda *a, **kw: pytest.fail('Unmocked inference call'))
    return d


def _image_uri(image):
    buf = io.BytesIO()
    image.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def test_detection_submit_disables_fal_payload_storage(monkeypatch):
    from estimator import exterior_detection as d
    monkeypatch.setenv('FAL_KEY', 'test-only-not-a-live-key')
    captured = {}

    class Response:
        status_code = 202

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, _size):
            yield b'{"request_id":"test"}'

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return Response()

    monkeypatch.setattr(d.requests, 'request', fake_request)
    assert d._json('POST', d.MODEL_URL, {'image_url': 'private'}) == {'request_id': 'test'}
    assert captured['headers']['X-Fal-Store-IO'] == '0'
    assert captured['headers']['X-Fal-Object-Lifecycle-Preference'] == (
        '{"expiration_duration_seconds":3600}')
    assert captured['headers']['Authorization'] == 'Key test-only-not-a-live-key'


def test_detection_converts_binary_masks_to_alpha_and_rejects_low_confidence(detector):
    from PIL import Image
    mask = Image.new('L', (3, 2), 0)
    mask.putpixel((1, 0), 255)
    uri = _image_uri(mask)
    result = detector.combine_masks({'masks': [{'url': uri}], 'scores': [0.9]}, (3, 2))
    image = detector.decode_image(result['mask'])
    assert image.getpixel((1, 0)) == (255, 255, 255, 255)
    assert image.getpixel((0, 0))[3] == 0
    assert detector.combine_masks({'masks': [{'url': uri}], 'scores': [0.1]}, (3, 2))['status'] == 'not_found'
    assert detector.combine_masks({'masks': []}, (3, 2))['status'] == 'not_found'
    with pytest.raises(detector.DetectionError):
        detector.combine_masks({'masks': [{'url': uri}]}, (4, 3))
    with pytest.raises(detector.DetectionError):
        detector.combine_masks({'masks': [{'url': 'https://untrusted.invalid/image.png'}]}, (3, 2))


@pytest.mark.parametrize(('role', 'prompt'), [
    ('roof', 'roof'),
    ('siding', 'exterior wall siding'),
    ('trim', 'fascia boards, window trim, door trim, corner trim'),
    ('soffit', 'soffit under roof eaves'),
    ('door', 'entry door'),
])
def test_detection_requests_each_independent_surface(detector, monkeypatch,
                                                     role, prompt):
    from PIL import Image
    captured = {}
    rid = role + '-test'
    root = 'https://queue.fal.run/fal-ai/sam-3/requests/' + rid

    def fake_json(method, url, payload=None):
        assert method == 'POST'
        captured.update(payload)
        return {'request_id': rid, 'status_url': root + '/status',
                'response_url': root}

    monkeypatch.setattr(detector, '_json', fake_json)
    detector.submit(role, _image_uri(Image.new('RGB', (2, 2), 'white')))

    assert captured['prompt'] == prompt
    assert captured['return_multiple_masks'] is True


def test_detection_requires_explicit_setup_and_existing_authorized_estimate(client, anon, detector, monkeypatch):
    eid = client.post('/api/estimates', json={'salesperson': 'luke'}).get_json()['estimate_id']
    endpoint = f'/api/estimates/{eid}/visualizer/detection'
    try:
        assert anon.post(endpoint, json={}).status_code == 401
        with anon.session_transaction() as session:
            session['user'] = 'someone-else'
        assert anon.post(endpoint, json={}).status_code == 403
        assert client.post('/api/estimates/not-real/visualizer/detection', json={}).status_code == 404
        monkeypatch.delenv('EXTERIOR_AUTO_DETECT')
        assert client.get('/api/visualizer/capabilities').get_json()['auto_detect'] is False
        assert client.post(endpoint, json={}).status_code == 503
        monkeypatch.setenv('EXTERIOR_AUTO_DETECT', '1')
        assert client.post(endpoint, json={'role': 'other'}).status_code == 400
        assert client.post(endpoint, json={'role': 'roof', 'photo_key': 'photo1', 'image': 'bad'}).status_code == 400
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_detection_queue_ticket_scope_throttle_and_mask_round_trip(client, detector, monkeypatch):
    from PIL import Image
    uri = _image_uri(Image.new('RGB', (4, 3), 'white'))
    rid = 'test-request'
    root = 'https://queue.fal.run/fal-ai/sam-3/requests/' + rid
    calls = []

    def fake_json(method, url, payload=None):
        calls.append((method, url, payload))
        if method == 'POST':
            assert payload['prompt'] == 'roof'
            assert payload['sync_mode'] is True and payload['apply_mask'] is False
            return {'request_id': rid, 'status_url': root + '/status', 'response_url': root}
        if url.endswith('/status'):
            return {'status': 'COMPLETED'}
        return {'masks': [{'url': uri}], 'scores': [0.95]}

    monkeypatch.setattr(detector, '_json', fake_json)
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    other = client.post('/api/estimates', json={}).get_json()['estimate_id']
    endpoint = f'/api/estimates/{eid}/visualizer/detection'
    body = {'role': 'roof', 'photo_key': 'photo1', 'image': uri}
    try:
        response = client.post(endpoint, json=body)
        assert response.status_code == 202, response.get_json()
        ticket = response.get_json()['ticket']
        assert 'test-only-not-a-live-key' not in ticket
        assert client.get(endpoint, query_string={'ticket': ticket + 'bad'}).status_code == 400
        assert client.get(f'/api/estimates/{other}/visualizer/detection', query_string={'ticket': ticket}).status_code == 403
        result = client.get(endpoint, query_string={'ticket': ticket}).get_json()
        assert result['status'] == 'complete' and result['photo_key'] == 'photo1' and result['role'] == 'roof'
        mask = client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'mask', 'role': 'roof', 'ext': 'png', 'content_b64': result['mask']})
        assert mask.status_code == 201
        # A normal estimate save (or hostile stale client) cannot reset the
        # submission cooldown and turn a click into unlimited paid requests.
        client.put(f'/api/estimates/{eid}', json={'_visualizer_detection_attempts': {'roof': 1}})
        assert client.post(endpoint, json=body).status_code == 429
        assert len([c for c in calls if c[0] == 'POST']) == 1
    finally:
        client.delete(f'/api/estimates/{eid}')
        client.delete(f'/api/estimates/{other}')


def test_detection_never_follows_untrusted_queue_urls(detector, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(detector, '_json', lambda *a, **kw: {
        'request_id': 'test', 'status_url': 'https://evil.invalid/status', 'response_url': 'https://evil.invalid/result'})
    with pytest.raises(detector.DetectionError):
        detector.submit('roof', _image_uri(Image.new('RGB', (2, 2), 'white')))


def test_dropdowns_use_live_catalog_and_ignore_stale_detection_in_node():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for designer UI behavior checks')
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const src = fs.readFileSync(process.argv[1], 'utf8');
const from = src.indexOf('const _SIDING_PATTERN_SVG =');
const to = src.indexOf('// ── Service Worker registration', from);
const picker = {innerHTML: ''};
const doc = {getElementById: id => id === 'vz-picker-body' ? picker : null, querySelector:()=>null,
  querySelectorAll: () => [], createElement: () => ({getContext: () => ({drawImage(){}}), toDataURL: () => 'data:image/jpeg;base64,abcd'})};
const context = vm.createContext({assert, document: doc, setTimeout: fn => fn(),
  S: {estimate_id:'a', trades:{roofing:{tier_bundles:{better:'chosen'}}}},
  priceBook: {roofing_tier_defaults:{good:'default',better:'default',best:'default'},
    roofing_bundles:[{id:'default',name:'Default',product_ids:['m_default']},{id:'chosen',name:'Quoted',product_ids:['m_chosen']}],
    roofing_catalog:[{id:'m_default', colors:[{name:'White',hex:'#ffffff'}]}, {id:'m_chosen', colors:[{name:'Black',hex:'#222222'}]}],
    exterior_doors:[{id:'provia-signet',name:'ProVia Signet',preview_only:true,colors:[{name:'Snow Mist',hex:'#eeeeee'}]}]},
  TIERS:['good','better','best'], setDirty(){}, esc:s=>String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'),
  confirm:()=>true, alert:()=>{}});
vm.runInContext(src.slice(from,to),context);
vm.runInContext(`
  _vzResetState();
  assert.equal(_vzBundleFor('roofing','better').id, 'chosen');
  _vzRenderPicker();
  assert.equal(_vzGet().selections.roofing.good.color_name, 'White');
  assert.equal(_vzGet().selections.roofing.better.color_name, 'Black');
  assert(document.getElementById('vz-picker-body').innerHTML.includes('<select'));
  assert(document.getElementById('vz-picker-body').innerHTML.includes('ProVia Signet'));
  _vzChooseBundle('roofing','default');
  assert.equal(S.trades.roofing.tier_bundles.better, 'chosen'); // no pricing mutation
  _vzPickDoorOption('provia-signet');
  assert.equal(_vzGet().selections.doors.better.preview_only, true);
  assert.equal(_vzGet().selections.doors.better.pattern_id, '');
  _vzPickDoorOption('');
  assert.equal(_vzGet().selections.doors.better, undefined);
  priceBook.siding_tier_defaults = {good:'b_lp_expert', better:'b_lp_expert', best:'b_lp_expert'};
  priceBook.siding_bundles = [
    {id:'b_lp_expert',name:'LP ExpertFinish',product_ids:['s_lp']},
    {id:'b_hardie_statement',name:'Hardie Statement',product_ids:['s_hardie']}
  ];
  priceBook.siding_catalog = [
    {id:'s_lp',colors:[{name:'LP White',hex:'#eeeeee'}],styles:[{id:'lap',name:'Lap',pattern_id:'lap'}]},
    {id:'s_hardie',colors:[{name:'Hardie White',hex:'#dddddd'}],styles:[{id:'smooth',name:'Smooth',pattern_id:'lap'}]}
  ];
  priceBook.exterior_catalog = [
    {category:'siding',active:true,product_id:'ext_lp',brand:'LP',product:'ExpertFinish',style:'Lap 8',color:'LP White',hex:'#eeeeee',price_book_bundle:'b_lp_expert',pattern_id:'lap'},
    {category:'trim',active:true,product_id:'ext_lp_trim',brand:'LP',product:'Trim',style:'Trim',color:'LP White',hex:'#eeeeee',price_book_bundle:'b_lp_expert'},
    {category:'soffit',active:true,product_id:'ext_lp_soffit',brand:'LP',product:'Soffit',style:'Vented',color:'LP White',hex:'#eeeeee',price_book_bundle:'b_lp_expert'},
    {category:'siding',active:true,product_id:'ext_hardie',brand:'Hardie',product:'Statement',style:'Smooth',color:'Hardie White',hex:'#dddddd',price_book_bundle:'b_hardie_statement',pattern_id:'lap'},
    {category:'trim',active:true,product_id:'ext_hardie_trim',brand:'Hardie',product:'Trim',style:'Smooth',color:'Hardie White',hex:'#dddddd',price_book_bundle:'b_hardie_statement'},
    {category:'soffit',active:true,product_id:'ext_hardie_soffit',brand:'Hardie',product:'Soffit',style:'Vented',color:'Hardie White',hex:'#dddddd',price_book_bundle:'b_hardie_statement'}
  ];
  S.trades.siding = {tier_bundles:{better:'b_lp_expert'}};
  _vzEnsureTier('better');
  assert.equal(_vzGet().selections.trim.better.bundle_id, 'b_lp_expert');
  assert.equal(_vzGet().selections.soffit.better.bundle_id, 'b_lp_expert');
  _vzChooseProduct('siding','ext_hardie');
  assert.equal(_vzGet().selections.trim.better.bundle_id, 'b_hardie_statement');
  assert.equal(_vzGet().selections.soffit.better.bundle_id, 'b_hardie_statement');
  assert.equal(S.trades.siding.tier_bundles.better, 'b_lp_expert'); // visual change never reprices
  globalThis.oldState = vzState;
  vzState.photoKey = 'first';
  assert(_vzIsCurrent(vzState,'first'));
  vzState.photoKey = 'second';
  assert(!_vzIsCurrent(vzState,'first'));
  S = {estimate_id:'b'};
  assert(!_vzIsCurrent(oldState,'second'));
  _vzResetState();
  assert.equal(vzState.roofMask, null);
  assert.equal(vzState.trimMask, null);
  assert.equal(vzState.soffitMask, null);
`,context);
// Exercise the asynchronous result boundary: another photo becomes current
// while the submit request is in flight. No polling or pixel write may follow.
vm.runInContext(`
  vzCapabilities = {auto_detect:true};
  vzState.photoImg = {}; vzState.canvas = {width:10,height:10};
  vzState.photoKey = 'test-photo';
  globalThis.submits = 0;
  _vzDetectionJSON = async () => { submits++; S = {estimate_id:'c'}; return {ticket:'ticket'}; };
  _vzRedrawAll = () => {};
  _vzDetectionUI = () => {};
  globalThis.finished = _vzAutoDetect();
`,context);
context.finished.then(() => {
  assert.equal(context.S.estimate_id,'c');
  assert.equal(context.submits,5);
}).catch(e => { console.error(e); process.exitCode=1; });
"""
    source = Path(__file__).resolve().parents[1] / 'static' / 'app.js'
    result = subprocess.run([node, '-e', script, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_state_endpoint_updates_selections_only(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        # Seed a tier_renders pointer that must NOT get wiped when the
        # rep PUTs a fresh selections dict.
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['visualizer'] = {'tier_renders': {'good': f'{eid}/vr_good.jpg'}}
        client.put(f'/api/estimates/{eid}', json=doc)

        r = client.put(
            f'/api/estimates/{eid}/visualizer/state',
            json={'selections': {'roofing': {'good': {'color_hex': '#333333'}}}},
        )
        assert r.status_code == 200
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['visualizer']['selections']['roofing']['good']['color_hex'] == '#333333'
        # tier_renders survived — selections doesn't touch it.
        assert fresh['visualizer']['tier_renders']['good'] == f'{eid}/vr_good.jpg'
    finally:
        client.delete(f'/api/estimates/{eid}')


@pytest.mark.parametrize('role', ['gutter', 'window', 'metal', 'shutter', 'stucco'])
def test_additional_surface_masks_are_isolated_by_elevation(client, role):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        response = client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'mask', 'role': role, 'elevation_id': 'rear',
            'elevation_name': 'Rear', 'ext': 'png',
            'content_b64': _ONE_PX_JPEG_B64,
        })
        assert response.status_code == 201, response.get_data(as_text=True)
        fresh = client.get(f'/api/estimates/{eid}').get_json()['visualizer']
        assert fresh['elevations']['rear']['masks'][role] == response.get_json()['filename']
        assert fresh['elevations']['rear']['name'] == 'Rear'
        # Rear assets never leak into the legacy Front pointer fields.
        assert not fresh.get(f'{role}_mask')
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_replacing_one_elevation_keeps_the_other_elevation_render(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        front = client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'render', 'tier': 'better', 'elevation_id': 'front',
            'elevation_name': 'Front', 'ext': 'jpg', 'content_b64': _ONE_PX_JPEG_B64,
        }).get_json()['filename']
        client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'render', 'tier': 'better', 'elevation_id': 'rear',
            'elevation_name': 'Rear', 'ext': 'jpg', 'content_b64': _ONE_PX_JPEG_B64,
        })
        replacement = client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'base', 'elevation_id': 'rear', 'elevation_name': 'Rear',
            'ext': 'jpg', 'content_b64': _ONE_PX_JPEG_B64,
        })
        assert replacement.status_code == 201
        vz = client.get(f'/api/estimates/{eid}').get_json()['visualizer']
        assert vz['elevations']['front']['tier_renders']['better'] == front
        assert vz['tier_renders']['better'] == front  # legacy mirror remains Front
        assert vz['elevations']['rear']['tier_renders'] == {}
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_visualizer_state_saves_scope_names_provia_and_invalidates_stale_views(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        for elevation in ('front', 'rear'):
            client.post(f'/api/estimates/{eid}/visualizer/asset', json={
                'kind': 'render', 'tier': 'best', 'elevation_id': elevation,
                'elevation_name': elevation.title(), 'ext': 'jpg',
                'content_b64': _ONE_PX_JPEG_B64,
            })
        result = client.put(f'/api/estimates/{eid}/visualizer/state', json={
            'scope': ['roof', 'siding', 'gutter', 'window', 'metal', 'shutter', 'stucco'],
            'concept_names': {'good': 'Classic', 'better': 'Modern', 'best': 'Bold'},
            'favorite_tier': 'best', 'active_elevation_id': 'rear',
            'elevation_names': {'front': 'Street', 'rear': 'Back yard'},
            'elevation_order': ['front', 'rear'], 'invalidate_other_renders': True,
            'provia_specs': {'best': {'access_code': 'PV-123', 'model': '460',
                                      'glass': 'Privacy', 'notes': 'Black handleset'}},
        })
        assert result.status_code == 200, result.get_json()
        vz = result.get_json()['visualizer']
        assert vz['scope'][-5:] == ['gutter', 'window', 'metal', 'shutter', 'stucco']
        assert vz['concept_names']['best'] == 'Bold' and vz['favorite_tier'] == 'best'
        assert vz['elevations']['front']['name'] == 'Street'
        assert vz['elevations']['front']['tier_renders'] == {}
        assert vz['elevations']['rear']['tier_renders']['best']
        assert vz['provia_specs']['best']['access_code'] == 'PV-123'
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_visualizer_state_can_persist_an_empty_new_elevation(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        result = client.put(f'/api/estimates/{eid}/visualizer/state', json={
            'elevation_names': {'front': 'Street', 'garage': 'Detached garage'},
            'elevation_order': ['front', 'garage'],
            'active_elevation_id': 'garage',
        })
        assert result.status_code == 200, result.get_json()
        vz = result.get_json()['visualizer']
        assert vz['active_elevation_id'] == 'garage'
        assert vz['elevation_order'] == ['front', 'garage']
        assert vz['elevations']['garage'] == {
            'id': 'garage', 'name': 'Detached garage', 'base_image': None,
            'masks': {}, 'tier_renders': {},
        }
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_manager_texture_upload_and_new_catalog_categories(client):
    original = client.get('/api/exterior-catalog').get_json()['entries']
    try:
        upload = client.post('/api/exterior-catalog/texture', data={
            'file': (io.BytesIO(base64.b64decode(_ONE_PX_JPEG_B64)), 'swatch.jpg')
        }, content_type='multipart/form-data')
        assert upload.status_code == 201, upload.get_json()
        texture = upload.get_json()['texture_ref']
        assert texture.startswith('_catalog/et_') and texture.endswith('.png')
        categories = ['gutter', 'window', 'metal', 'shutter', 'stucco']
        rows = [{
            'category': category, 'brand': 'Installed Co',
            'product': category.title() + ' System', 'style': 'Standard',
            'color': 'Graphite', 'hex': '#334455', 'texture_ref': texture,
            'texture_scale': 72,
        } for category in categories]
        saved = client.put('/api/exterior-catalog', json={'entries': rows})
        assert saved.status_code == 200, saved.get_json()
        assert {row['category'] for row in saved.get_json()['entries']} == set(categories)
        assert all(row['texture_ref'] == texture and row['texture_scale'] == 72
                   for row in saved.get_json()['entries'])
    finally:
        assert client.put('/api/exterior-catalog', json={'entries': original}).status_code == 200


def test_design_share_is_price_free_and_approval_is_server_managed(client, anon):
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Ada Lovelace', 'address': '1 Design Way'},
        'status': 'draft',
    }).get_json()['estimate_id']
    try:
        client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'render', 'tier': 'better', 'elevation_id': 'front',
            'elevation_name': 'Front', 'ext': 'jpg', 'content_b64': _ONE_PX_JPEG_B64,
        })
        state = client.put(f'/api/estimates/{eid}/visualizer/state', json={
            'scope': ['roof', 'door'],
            'concept_names': {'good': 'Classic', 'better': 'Modern Farmhouse', 'best': 'Bold'},
            'selections': {
                'roofing': {'better': {'product_name': 'Landmark',
                                        'color_name': 'Moire Black'}},
                'siding': {'better': {'product_name': 'Hidden unscoped siding',
                                      'color_name': 'Should not display'}},
            },
            'provia_specs': {'better': {'access_code': 'PV-456', 'model': '460'}},
        })
        assert state.status_code == 200
        shared = client.post(f'/api/estimates/{eid}/visualizer/share')
        assert shared.status_code == 200, shared.get_json()
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['status'] == 'draft' and not fresh.get('sent_at')

        token = shared.get_json()['token']
        page = anon.get(f'/design/{token}')
        html = page.get_data(as_text=True)
        assert page.status_code == 200
        assert 'Modern Farmhouse' in html and 'PV-456' in html
        assert 'Hidden unscoped siding' not in html
        assert '$' not in html and 'Approve selected design' in html

        approved = anon.post(f'/design/{token}', data={
            'approved_tier': 'better', 'approver_name': 'Ada Lovelace', 'agree': 'yes'})
        assert approved.status_code == 200
        stored = client.get(f'/api/estimates/{eid}').get_json()
        assert stored['design_approval']['concept_name'] == 'Modern Farmhouse'
        assert stored['design_approval']['provia_spec']['access_code'] == 'PV-456'
        assert 'siding' not in stored['design_approval']['selections']
        assert stored['design_approval']['snapshot_hash']
        assert len(stored['design_approval_history']) == 1
        assert 'Current design approved' in anon.get(f'/design/{token}').get_data(as_text=True)

        changed = client.put(f'/api/estimates/{eid}/visualizer/state', json={
            'concept_names': {'good': 'Classic', 'better': 'Modern Farmhouse Revised',
                              'best': 'Bold'},
        })
        assert changed.status_code == 200
        changed_html = anon.get(f'/design/{token}').get_data(as_text=True)
        assert 'needs approval again' in changed_html
        assert 'value="better" checked' not in changed_html

        stale = copy.deepcopy(stored)
        stale.pop('design_approval', None)
        stale.pop('design_approval_history', None)
        stale.pop('design_share_token', None)
        assert client.put(f'/api/estimates/{eid}', json=stale).status_code == 200
        preserved = client.get(f'/api/estimates/{eid}').get_json()
        assert preserved['design_approval']['concept_name'] == 'Modern Farmhouse'
        assert preserved['design_share_token'] == token
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_extended_design_studio_frontend_wiring():
    source = open(os.path.join(os.path.dirname(__file__), '..', 'static', 'app.js'),
                  encoding='utf-8').read()
    for token in ('gutter', 'window', 'metal', 'shutter', 'stucco'):
        assert token in source
    assert 'function _vzAddElevation' in source
    assert 'function _vzShareDesign' in source
    assert 'function _vzCompositeTexture' in source
    assert 'Exact ProVia specification handoff' in source
    assert 'invalidate_other_renders' in source
    assert 'if (!scope.has(role)) continue;' in source


# ── PDF integration ────────────────────────────────────────────────────

def _minimal_signed_estimate(eid):
    """Just enough of an estimate to make build_signed_pdf produce output."""
    return {
        'estimate_id': eid,
        'estimate_type': 'retail',
        'customer': {'name': 'Ada Lovelace',
                     'address': {'street': '1 Analytical Engine',
                                 'city': 'Tyler', 'state': 'TX', 'zip': '75701'}},
        'salesperson': 'luke',
        'estimate_date': '2026-08-04',
        'trades': {'roofing': {'enabled': True, 'selected_tier': 'better',
                               'line_items': [{'name': 'Landmark shingle',
                                              'quantity': 30, 'unit': 'SQ',
                                              'tiers': {'better': {'included': True,
                                                                   'material_unit_cost': 142}}}]}},
        'pricing': {'mode': 'margin', 'margin_pct': 25},
        'selected_tier': 'better',
        'signature': {'name': 'Ada Lovelace',
                      'signed_at': '2026-08-04T12:00:00Z',
                      'ip_address': '127.0.0.1',
                      'document_hash': 'a' * 64,
                      'shingle_color': 'Charcoal Black'},
    }


def test_signed_pdf_embeds_the_render_when_present(client, A):
    """A tier render on the estimate must add a page to the signed PDF —
    the whole point of the feature is that it prints on the contract."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        est_no_vz = _minimal_signed_estimate(eid)
        base_bytes = A.build_signed_pdf(est_no_vz)

        # Write a real 1px JPEG into UPLOADS_DIR the way the asset
        # endpoint would, then reference it from the estimate.
        os.makedirs(os.path.join(TEST_DATA_DIR, 'uploads', eid), exist_ok=True)
        render_name = f'{eid}/vr_test_signed.jpg'
        with open(os.path.join(TEST_DATA_DIR, 'uploads', render_name), 'wb') as f:
            f.write(base64.b64decode(_ONE_PX_JPEG_B64))
        est_with_vz = _minimal_signed_estimate(eid)
        est_with_vz['visualizer'] = {
            'tier_renders': {'better': render_name},
            'selections': {'roofing': {'better': {'color_name': 'Charcoal Black'}}},
        }
        with_bytes = A.build_signed_pdf(est_with_vz)
        assert len(with_bytes) > len(base_bytes) + 200, (
            f'expected the visualizer page to add bytes; base={len(base_bytes)} '
            f'with={len(with_bytes)}')
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_signed_pdf_skips_visualizer_page_when_no_renders(A):
    """No renders saved = no visualizer page, no error. Otherwise a fresh
    estimate that never touched the tab would blow up on print."""
    est = _minimal_signed_estimate('e_no_vz')
    # No visualizer key at all — must still build.
    A.build_signed_pdf(est)
    # Empty tier_renders — same.
    est['visualizer'] = {'tier_renders': {}}
    A.build_signed_pdf(est)


# ── /sign page integration ─────────────────────────────────────────────

def test_sign_page_shows_visualizer_img_when_renders_are_saved(client, A):
    """When the estimate carries tier_renders, the customer's /sign page
    embeds an <img> tag pointing at /uploads/... so the customer sees the
    rendering before signing."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        # Fill out enough estimate data to render a customer view.
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['customer'] = {'name': 'Ada Lovelace',
                           'address': {'street': '1 Analytical Engine',
                                       'city': 'Tyler', 'state': 'TX'}}
        doc['estimate_type'] = 'retail'
        doc['salesperson'] = 'luke'
        doc['trades'] = {'roofing': {'enabled': True, 'selected_tier': 'better',
                                     'line_items': [{'name': 'x', 'quantity': 1, 'unit': 'SQ',
                                                     'tiers': {'better': {'included': True,
                                                                          'material_unit_cost': 100}}}]}}
        doc['selected_tier'] = 'better'
        # Save it and mint a share token by posting to the share endpoint.
        assert client.put(f'/api/estimates/{eid}', json=doc).status_code == 200

        # Write a render to disk + point est.visualizer at it.
        os.makedirs(os.path.join(TEST_DATA_DIR, 'uploads', eid), exist_ok=True)
        render_name = f'{eid}/vr_sign_page.jpg'
        with open(os.path.join(TEST_DATA_DIR, 'uploads', render_name), 'wb') as f:
            f.write(base64.b64decode(_ONE_PX_JPEG_B64))

        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['visualizer'] = {
            'tier_renders': {'better': render_name},
            'selections': {'roofing': {'better': {'color_name': 'Charcoal Black'}},
                           'siding':  {'better': {'color_name': 'Iron Gray',
                                                  'style_name': 'Lap Siding'}}},
        }
        client.put(f'/api/estimates/{eid}', json=doc)

        # Get share link via the send-preview endpoint or by peeking at
        # the share_token field. Simpler: use the /api/estimates/<id>/share
        # endpoint if it exists, else construct from the token in the doc.
        share = client.post(f'/api/estimates/{eid}/share')
        assert share.status_code in (200, 201)
        token = share.get_json().get('token') or share.get_json().get('share_token')
        assert token

        page = client.get(f'/sign/{token}')
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert render_name in html or f'/uploads/{render_name}' in html
        # And the tier label appears near the render, plus the color caption.
        assert 'See It on Your Home' in html
        assert 'Charcoal Black' in html
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_sign_page_omits_visualizer_when_no_renders(client, A):
    """Empty tier_renders must not emit a stub section — the customer
    shouldn't see a 'See It on Your Home' header pointing at nothing."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['customer'] = {'name': 'Ada', 'address': {'street': '1 St'}}
        doc['estimate_type'] = 'retail'
        doc['salesperson'] = 'luke'
        client.put(f'/api/estimates/{eid}', json=doc)
        share = client.post(f'/api/estimates/{eid}/share')
        token = share.get_json().get('token') or share.get_json().get('share_token')
        page = client.get(f'/sign/{token}')
        assert page.status_code == 200
        assert 'See It on Your Home' not in page.get_data(as_text=True)
    finally:
        client.delete(f'/api/estimates/{eid}')
