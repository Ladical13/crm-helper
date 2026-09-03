"""Regression coverage for the manufacturer texture catalog.

These tests deliberately exercise the migration helpers against in-memory
price books.  Production has a long-lived book on the Railway volume, so a
fresh-book assertion alone would miss the upgrade path used by real data.
"""

import copy
import hashlib
import re
from pathlib import Path

from PIL import Image


_TEXTURE_REF_RE = re.compile(r'^_catalog/(et_[0-9a-f]{32}\.png)$')


def _manifest(A):
    rows = A._official_exterior_texture_manifest()
    assert isinstance(rows, list)
    return rows


def _official_rows_for_manifest_entry(rows, entry):
    return [
        row for row in rows
        if row['category'] == entry['category']
        and row['price_book_bundle'] == entry['bundle_id']
        and row['color'].casefold() == entry['color'].casefold()
    ]


def test_fresh_lp_catalog_has_one_core_product_and_separate_naturals(A):
    pb = {}
    A._ensure_bundle_catalogs(pb)
    rows = [
        row for row in pb['exterior_catalog']
        if row['category'] == 'siding'
        and row['price_book_bundle'] == 'b_lp_expert'
    ]

    core = [row for row in rows
            if row['product'] == 'LP SmartSide ExpertFinish']
    naturals = [
        row for row in rows
        if row['product'] == 'LP SmartSide ExpertFinish Naturals Collection'
    ]

    assert len(core) == 16 * 8
    assert len({row['color'] for row in core}) == 16
    assert len({row['style'] for row in core}) == 8
    assert len(naturals) == 6 * 8
    assert {row['color'] for row in naturals} == {
        'Washed White', 'Smoky Slate', 'Saffron Cedar',
        'Weathered Walnut', 'Aged Amber', 'Bonsai Black',
    }
    assert {row['color']: row['hex'] for row in naturals} == {
        'Washed White': '#aea69b',
        'Smoky Slate': '#86837f',
        'Saffron Cedar': '#785131',
        'Weathered Walnut': '#564434',
        'Aged Amber': '#684229',
        'Bonsai Black': '#2f2a25',
    }
    assert len({row['style'] for row in naturals}) == 8
    assert not any(row['product'] == 'LP SmartSide Expert Finish'
                   and row['style'] in {
                       style['name'] for style in A._LP_EXPERTFINISH_STYLES
                   }
                   and row['color'] in {
                       color['name'] for color in A._LP_EXPERTFINISH_COLORS
                   }
                   for row in rows)
    versions = pb['exterior_catalog_seed_versions']
    assert A._LP_EXPERTFINISH_CANONICAL_MIGRATION in versions
    assert A._LP_EXPERTFINISH_NATURALS_MIGRATION in versions


def test_official_texture_manifest_matches_packaged_files(A):
    manifest = _manifest(A)
    assert len(manifest) == 54
    assert len({(row['category'], row['bundle_id'], row['color'].casefold())
                for row in manifest}) == len(manifest)
    assert {
        (row['category'], row['bundle_id']) for row in manifest
    } == {
        ('roof', 'b_iko_nordic'),
        ('roof', 'b_landmark'),
        ('siding', 'b_hardie_statement'),
        ('siding', 'b_lp_expert'),
    }

    asset_dir = Path(A.EXTERIOR_CATALOG_ASSET_DIR)
    total_bytes = 0
    for row in manifest:
        filename = row['file']
        assert re.fullmatch(r'et_[0-9a-f]{32}\.png', filename)
        path = asset_dir / filename
        assert path.is_file(), filename
        total_bytes += path.stat().st_size
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            assert image.format == 'PNG'
            assert image.width == row['width'] <= 512
            assert image.height == row['height'] <= 512
        assert 16 <= row['texture_scale'] <= 512
    assert total_bytes < 10 * 1024 * 1024


def test_lp_duplicate_cleanup_merges_manager_edits_before_renaming(A):
    pb = {}
    A._ensure_bundle_catalogs(pb)
    marker = A._LP_EXPERTFINISH_CANONICAL_MIGRATION
    pb['exterior_catalog_seed_versions'].remove(marker)

    canonical = next(
        row for row in pb['exterior_catalog']
        if row['product'] == 'LP SmartSide ExpertFinish'
        and row['style'] == 'Lap Joint Siding - 8 in.'
        and row['color'] == 'Abyss Black'
    )
    canonical['texture_ref'] = ''
    canonical['texture_scale'] = 96
    legacy = copy.deepcopy(canonical)
    legacy['product'] = 'LP SmartSide Expert Finish'
    legacy['hex'] = '#202122'
    legacy['texture_ref'] = '_catalog/et_' + ('e' * 32) + '.png'
    legacy['texture_scale'] = 173
    legacy['active'] = False
    legacy = A._normalize_exterior_entry(legacy)
    pb['exterior_catalog'].append(legacy)

    A._migrate_lp_expertfinish_canonical_product(pb)
    matches = [
        row for row in pb['exterior_catalog']
        if row['product'] == 'LP SmartSide ExpertFinish'
        and row['style'] == 'Lap Joint Siding - 8 in.'
        and row['color'] == 'Abyss Black'
    ]
    assert len(matches) == 1
    assert matches[0]['hex'] == '#202122'
    assert matches[0]['texture_ref'] == legacy['texture_ref']
    assert matches[0]['texture_scale'] == 173
    assert matches[0]['active'] is False
    assert not any(row['product'] == 'LP SmartSide Expert Finish'
                   for row in pb['exterior_catalog'])
    assert pb['exterior_catalog_seed_versions'].count(marker) == 1


def test_official_texture_migration_fills_all_matching_rows(A):
    pb = {}
    A._ensure_bundle_catalogs(pb)
    rows = pb['exterior_catalog']
    manifest = _manifest(A)

    for entry in manifest:
        expected_ref = f"_catalog/{entry['file']}"
        matches = _official_rows_for_manifest_entry(rows, entry)
        assert matches, (entry['bundle_id'], entry['color'])
        assert {row['texture_ref'] for row in matches} == {expected_ref}
        assert {row['texture_scale'] for row in matches} == {
            entry['texture_scale']}
    assert A._OFFICIAL_EXTERIOR_TEXTURES_MIGRATION in (
        pb['exterior_catalog_seed_versions'])
    textured = [row for row in rows if row.get('texture_ref')]
    assert len(textured) == 404
    assert len({row['texture_ref'] for row in textured}) == 54
    assert {row['category'] for row in textured} == {
        'roof', 'siding', 'trim', 'soffit'}


def test_official_texture_migration_preserves_manager_texture_and_is_sticky(A):
    pb = {}
    A._ensure_bundle_catalogs(pb)
    marker = A._OFFICIAL_EXTERIOR_TEXTURES_MIGRATION
    pb['exterior_catalog_seed_versions'].remove(marker)

    target = next(
        row for row in pb['exterior_catalog']
        if row['category'] == 'roof'
        and row['price_book_bundle'] == 'b_landmark'
        and row['color'] == 'Black Walnut'
    )
    manager_ref = '_catalog/et_ffffffffffffffffffffffffffffffff.png'
    target['texture_ref'] = manager_ref
    target['texture_scale'] = 211

    A._ensure_bundle_catalogs(pb)
    target = next(row for row in pb['exterior_catalog']
                  if row['id'] == target['id'])
    assert target['texture_ref'] == manager_ref
    assert target['texture_scale'] == 211
    assert pb['exterior_catalog_seed_versions'].count(marker) == 1

    snapshot = copy.deepcopy(pb['exterior_catalog'])
    A._ensure_bundle_catalogs(pb)
    assert pb['exterior_catalog'] == snapshot
    assert pb['exterior_catalog_seed_versions'].count(marker) == 1

    removed_id = next(
        row['id'] for row in pb['exterior_catalog']
        if row['category'] == 'roof'
        and row['price_book_bundle'] == 'b_iko_nordic'
        and row['color'] == 'Glacier'
    )
    pb['exterior_catalog'] = [
        row for row in pb['exterior_catalog'] if row['id'] != removed_id
    ]
    A._ensure_bundle_catalogs(pb)
    assert not any(row['id'] == removed_id for row in pb['exterior_catalog'])


def test_packaged_texture_assets_are_seeded_and_served(client, A):
    """A deployed DATA_DIR receives the immutable, content-addressed assets."""
    manifest = _manifest(A)
    upload_dir = Path(A.UPLOADS_DIR) / '_catalog'
    assert upload_dir.is_dir()

    # One asset from each manufacturer family is enough for the route check;
    # the manifest integrity test above validates every packaged file.
    seen = set()
    for entry in manifest:
        family = (entry['category'], entry['bundle_id'])
        if family in seen:
            continue
        seen.add(family)
        filename = entry['file']
        copied = upload_dir / filename
        assert copied.is_file(), filename
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == entry['sha256']
        response = client.get(f'/_catalog-does-not-exist/{filename}')
        assert response.status_code == 404
        response = client.get(f'/uploads/_catalog/{filename}')
        assert response.status_code == 200
        assert response.mimetype == 'image/png'


def test_packaged_texture_seeder_repairs_a_corrupt_volume_copy(A):
    entry = _manifest(A)[0]
    target = Path(A.UPLOADS_DIR) / '_catalog' / entry['file']
    target.write_bytes(b'not a png')

    A._seed_exterior_texture_assets()

    assert hashlib.sha256(target.read_bytes()).hexdigest() == entry['sha256']
    repaired = target.read_bytes()
    A._seed_exterior_texture_assets()
    assert target.read_bytes() == repaired
