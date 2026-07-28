"""API surface: auth guard, estimate CRUD, and the customer-facing sign page."""
import json
import os

import pytest

from conftest import TEST_DATA_DIR


# ── auth guard (default-deny) ──────────────────────────────────────────

def test_api_requires_login(anon):
    assert anon.get('/api/estimates').status_code == 401


def test_pages_redirect_to_login(anon):
    r = anon.get('/')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_redirect_to_login_preserves_destination(anon):
    """The portal sends the rep back where they were after signing in."""
    r = anon.get('/?estimate=abc123')
    assert r.status_code == 302
    assert 'next=' in r.headers['Location']


def test_estimator_no_longer_serves_a_login_page(anon):
    """Login moved to the portal. This app must not answer /login itself —
    two sign-in forms on one origin is how sessions get written twice."""
    assert 'login' not in {r.endpoint for r in anon.application.url_map.iter_rules()}


def test_logged_in_user_reaches_api(client):
    assert client.get('/api/me').status_code == 200


# ── estimate lifecycle ─────────────────────────────────────────────────

def test_create_read_update_delete(client):
    r = client.post('/api/estimates', json={})
    assert r.status_code == 201
    eid = r.get_json()['estimate_id']

    assert client.get(f'/api/estimates/{eid}').status_code == 200

    doc = client.get(f'/api/estimates/{eid}').get_json()
    doc['customer'] = {'name': 'Ada Lovelace', 'address': {'city': 'Tyler'}}
    assert client.put(f'/api/estimates/{eid}', json=doc).status_code == 200
    assert client.get(f'/api/estimates/{eid}').get_json()['customer']['name'] == 'Ada Lovelace'

    assert client.delete(f'/api/estimates/{eid}').status_code == 200
    assert client.get(f'/api/estimates/{eid}').status_code == 404


def test_new_estimate_appears_in_list(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        ids = [e['estimate_id'] for e in client.get('/api/estimates').get_json()]
        assert eid in ids
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_duplicate_creates_a_distinct_estimate(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    r = client.post(f'/api/estimates/{eid}/duplicate')
    assert r.status_code in (200, 201)
    dup = r.get_json()['estimate_id']
    try:
        assert dup != eid
        assert client.get(f'/api/estimates/{dup}').status_code == 200
    finally:
        client.delete(f'/api/estimates/{eid}')
        client.delete(f'/api/estimates/{dup}')


# ── path traversal ─────────────────────────────────────────────────────

@pytest.mark.parametrize('bad', ['../../etc/passwd', '', '..', 'a/../../b'])
def test_safe_path_id_rejects_traversal(A, bad):
    assert not A._safe_path_id(bad)


def test_safe_path_id_accepts_real_ids(A):
    assert A._safe_path_id('9e42-45d7-b052-mq5p065g')


def test_traversal_id_is_not_served(client):
    assert client.get('/api/estimates/..%2f..%2fapp').status_code != 200


# ── malformed estimates must not disappear ─────────────────────────────

def _write_raw(name, payload):
    p = os.path.join(TEST_DATA_DIR, 'estimates', f'{name}.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(payload, f)
    return p


def test_malformed_estimate_is_surfaced_not_silently_dropped(client):
    """A rep must never watch a job vanish from the dashboard with no trace."""
    p = _write_raw('broken-doc', {
        'estimate_id': 'broken-doc',
        'customer': 'a string where a dict belongs',  # blows up the summarizer
        'status': 'sent',
    })
    try:
        rows = client.get('/api/estimates').get_json()
        row = next((r for r in rows if r['estimate_id'] == 'broken-doc'), None)
        assert row is not None, 'malformed estimate was silently dropped from the list'
        assert 'load_error' in row
    finally:
        os.remove(p)


def _rep_client(app, name='aaron'):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = name  # not in users.json -> resolves to 'rep'
    return c


def test_rep_does_not_see_another_reps_estimate(app):
    """Positive control: the ownership filter is actually on for this user."""
    p = _write_raw('owned-by-someone-else', {
        'estimate_id': 'owned-by-someone-else',
        'salesperson': 'someone.else',
        'customer': {'name': 'Not Yours'},
    })
    try:
        rows = _rep_client(app).get('/api/estimates').get_json()
        assert not any(r['estimate_id'] == 'owned-by-someone-else' for r in rows)
    finally:
        os.remove(p)


def test_unreadable_salesperson_fails_closed_for_reps(app):
    """A salesperson field that isn't a string must not raise (500ing the whole
    dashboard) nor leak the estimate to a rep who doesn't own it."""
    p = _write_raw('weird-salesperson', {
        'estimate_id': 'weird-salesperson',
        'salesperson': 12345,               # breaks a naive .strip()
        'customer': {'name': 'Mystery'},
    })
    try:
        r = _rep_client(app).get('/api/estimates')
        assert r.status_code == 200, 'an unreadable salesperson took down the list'
        assert not any(x['estimate_id'] == 'weird-salesperson' for x in r.get_json()), \
            'estimate with an unreadable owner leaked to a rep'
    finally:
        os.remove(p)


def test_manager_still_sees_estimate_with_unreadable_salesperson(client):
    """Failing closed applies to reps; a manager/admin must still see the doc
    so the bad data is discoverable rather than invisible to everyone."""
    p = _write_raw('weird-salesperson-mgr', {
        'estimate_id': 'weird-salesperson-mgr',
        'salesperson': 12345,
        'customer': {'name': 'Mystery'},
    })
    try:
        rows = client.get('/api/estimates').get_json()  # client == luke == admin
        assert any(r['estimate_id'] == 'weird-salesperson-mgr' for r in rows)
    finally:
        os.remove(p)


def test_non_object_estimate_document_is_skipped(client):
    """A JSON file holding a list/string must not 500 the whole dashboard."""
    p = _write_raw('not-an-object', ['this', 'is', 'a', 'list'])
    try:
        assert client.get('/api/estimates').status_code == 200
    finally:
        os.remove(p)


# ── bundle catalogs (roofing + siding) ─────────────────────────────────
# Both trades are built ONLY from their catalog + bundles, so a bundle that
# references a missing product id, or a tier default pointing at a deleted
# bundle, silently ships an empty tier to a rep. Guard the shipped seeds.

@pytest.mark.parametrize('trade', ['roofing', 'siding', 'commercial'])
def test_pricebook_serves_bundle_catalog(client, trade):
    pb = client.get('/api/pricebook').get_json()
    assert pb[f'{trade}_catalog'], f'{trade} catalog must be seeded'
    assert pb[f'{trade}_bundles'], f'{trade} bundles must be seeded'
    assert set(pb[f'{trade}_tier_defaults']) == {'good', 'better', 'best'}


@pytest.mark.parametrize('trade', ['roofing', 'siding', 'commercial'])
def test_bundles_reference_real_products(client, trade):
    pb = client.get('/api/pricebook').get_json()
    ids = {p['id'] for p in pb[f'{trade}_catalog']}
    for b in pb[f'{trade}_bundles']:
        missing = [pid for pid in b.get('product_ids', []) if pid not in ids]
        assert not missing, f"{trade} bundle {b['name']} references unknown {missing}"
        assert b.get('product_ids'), f"{trade} bundle {b['name']} has no products"


@pytest.mark.parametrize('trade', ['roofing', 'siding'])
def test_tier_defaults_point_at_real_bundles(client, trade):
    pb = client.get('/api/pricebook').get_json()
    ids = {b['id'] for b in pb[f'{trade}_bundles']}
    for tier, bid in pb[f'{trade}_tier_defaults'].items():
        assert bid in ids, f'{trade} {tier} default bundle {bid!r} does not exist'


@pytest.mark.parametrize('trade', ['roofing', 'siding', 'commercial'])
def test_every_seeded_bundle_ships_customer_copy(client, trade):
    """A bundle with no copy silently leaves the PREVIOUS product's tagline and
    bullets on the card when a rep swaps to it — how a metal roof goes out
    described as a shingle. The tagline is the bundle's own; the bullets come
    from its products, so every bundle must own a product that speaks."""
    pb = client.get('/api/pricebook').get_json()
    by_id = {p['id']: p for p in pb[f'{trade}_catalog']}
    for b in pb[f'{trade}_bundles']:
        assert b.get('description', '').strip(), f"{trade} bundle {b['name']} has no tagline"
        speaks = [pid for pid in b['product_ids']
                  if by_id.get(pid, {}).get('bullets') != []
                  and by_id.get(pid, {}).get('customer_visible') is not False]
        assert speaks, f"{trade} bundle {b['name']} has no product that says anything"


@pytest.mark.parametrize('trade', ['roofing', 'siding', 'commercial'])
def test_seeded_product_bullets_are_never_blank(client, trade):
    """A blank bullet prints as an empty dot on the customer's card."""
    for p in client.get('/api/pricebook').get_json()[f'{trade}_catalog']:
        for bullet in (p.get('bullets') or []):
            assert str(bullet).strip(), f"{trade} product {p['name']} has a blank bullet"


def test_copy_backfills_onto_a_book_that_already_has_a_catalog(A):
    """The live book predates these fields; without this, new seed copy never
    lands — the bundle's closing bullets or a product's customer wording."""
    seed = A.ROOFING_BUNDLES_SEED[0]
    pb = A._ensure_bundle_catalogs({
        'roofing_catalog': [{'id': 'm_landmark', 'name': 'X', 'unit': 'SQ', 'cost': 1}],
        'roofing_bundles': [{'id': seed['id'], 'name': seed['name'], 'product_ids': ['m_landmark']}],
    })
    got = next(b for b in pb['roofing_bundles'] if b['id'] == seed['id'])
    assert got['extra_features'] == seed['extra_features']
    assert got['description'] == seed['description']
    assert got['product_ids'] == ['m_landmark']    # the manager's product list, untouched
    landmark = next(p for p in pb['roofing_catalog'] if p['id'] == 'm_landmark')
    seed_p = next(p for p in A.ROOFING_CATALOG_SEED if p['id'] == 'm_landmark')
    assert landmark['bullets'] == seed_p['bullets']
    assert landmark['name'] == 'X'                 # the manager's rename, untouched


def test_cleared_product_bullets_are_not_refilled(A):
    """A manager who silences a product saves `bullets: []`. Absence is the test
    for backfill, never falsiness — same contract as manual measures."""
    pb = A._ensure_bundle_catalogs({
        'roofing_catalog': [{'id': 'm_landmark', 'name': 'X', 'unit': 'SQ', 'cost': 1,
                             'bullets': []}],
    })
    assert next(p for p in pb['roofing_catalog'] if p['id'] == 'm_landmark')['bullets'] == []


def test_backfill_respects_deliberately_emptied_copy(A):
    """Key ABSENCE means 'server may fill it in'; [] means the manager cleared it."""
    seed = A.ROOFING_BUNDLES_SEED[0]
    pb = A._ensure_bundle_catalogs({
        'roofing_catalog': [{'id': 'm_x', 'name': 'X', 'unit': 'SQ', 'cost': 1}],
        'roofing_bundles': [{'id': seed['id'], 'name': seed['name'], 'product_ids': ['m_x'],
                             'extra_features': [], 'description': ''}],
    })
    got = next(b for b in pb['roofing_bundles'] if b['id'] == seed['id'])
    assert got['extra_features'] == []
    assert got['description'] == ''


def test_backfill_leaves_manager_bundles_and_deletions_alone(A):
    pb = A._ensure_bundle_catalogs({
        'roofing_catalog': [{'id': 'm_x', 'name': 'X', 'unit': 'SQ', 'cost': 1}],
        'roofing_bundles': [{'id': 'b_custom', 'name': 'Mine', 'product_ids': ['m_x']}],
    })
    assert [b['id'] for b in pb['roofing_bundles']] == ['b_custom']   # no seeds re-added
    assert 'extra_features' not in pb['roofing_bundles'][0]


def test_backfill_never_mutates_the_seed_constants(A):
    """The response dict is edited downstream; aliasing a seed list would poison
    every later request in the process."""
    before = json.dumps(A.ROOFING_BUNDLES_SEED, sort_keys=True)
    cat_before = json.dumps(A.ROOFING_CATALOG_SEED, sort_keys=True)
    pb = A._ensure_bundle_catalogs({})
    pb['roofing_bundles'][0]['extra_features'].append('injected')
    pb['roofing_bundles'][0]['product_ids'].append('injected')
    pb['roofing_catalog'][0]['bullets'].append('injected')
    assert json.dumps(A.ROOFING_BUNDLES_SEED, sort_keys=True) == before
    assert json.dumps(A.ROOFING_CATALOG_SEED, sort_keys=True) == cat_before


def test_seeded_bundle_measures_are_known_keys(A):
    """A catalog product's Auto-Qty key must exist client-side (MEASURE_DEFS)."""
    import re
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'static', 'app.js'), encoding='utf-8').read()
    block = js[js.index('const MEASURE_DEFS'):]
    block = block[:block.index('\n};')]
    known = set(re.findall(r'^\s{2}(\w+):', block, re.M))
    for trade, (catalog, _b, _d) in A.BUNDLE_SEEDS.items():
        for p in catalog:
            if p.get('measure'):
                assert p['measure'] in known, (
                    f"unknown measure {p['measure']} on {trade} product {p['name']}")
