"""Permit-jurisdiction reference API (statewide Colorado).

The Scope-page "Permit Jurisdiction & Code" panel is fed by /api/jurisdictions,
seeded from scripts/build_jurisdictions.py (all 64 CO counties + 273
municipalities + a shared colorado_baseline). These guard the seed shape and the
manager-only write gate.
"""


def test_jurisdictions_seed_served(client):
    r = client.get('/api/jurisdictions')
    assert r.status_code == 200
    d = r.get_json()
    assert d['colorado_baseline']['code_points'], 'baseline code points missing'
    assert d['colorado_baseline']['verify_note']
    js = d['jurisdictions']
    ids = {j['id'] for j in js}
    assert len(ids) == len(js), 'jurisdiction ids must be unique'
    # Loveland is wired to the existing permit filler.
    lov = next(j for j in js if j['id'] == 'loveland')
    assert lov['permit_template'] == 'loveland'
    assert lov['county'] == 'Larimer'
    # Statewide coverage: all 64 counties + all 273 municipalities.
    assert len([j for j in js if j['kind'] == 'county']) == 64
    assert len([j for j in js if j['kind'] == 'city']) == 273


def test_jurisdictions_multicounty(client):
    """A muni spanning two counties keeps both so the panel can offer each as a
    candidate authority (the Fort Collins / Larimer County nuance)."""
    js = client.get('/api/jurisdictions').get_json()['jurisdictions']
    windsor = next(j for j in js if j['id'] == 'windsor')
    assert set(windsor['counties']) == {'Weld', 'Larimer'}


def test_jurisdictions_put_requires_manager(anon, app):
    # Anonymous → global login guard (401).
    assert anon.put('/api/jurisdictions', json={}).status_code == 401
    # Logged-in rep (not a manager) → forbidden (403).
    rep = app.test_client()
    with rep.session_transaction() as s:
        s['user'] = 'not-a-manager'
    assert rep.put('/api/jurisdictions', json={'jurisdictions': []}).status_code == 403


def test_jurisdictions_put_persists_for_manager(client):
    doc = client.get('/api/jurisdictions').get_json()
    doc['colorado_baseline']['verify_note'] = 'EDITED-BY-TEST'
    assert client.put('/api/jurisdictions', json=doc).status_code == 200
    again = client.get('/api/jurisdictions').get_json()
    assert again['colorado_baseline']['verify_note'] == 'EDITED-BY-TEST'
    # Round-trip must not lose the statewide dataset.
    assert len(again['jurisdictions']) == len(doc['jurisdictions'])


# ── Scope-gap checklist (code_items) ────────────────────────────────────────
# The Insurance tab matches carrier line descriptions against these, so the
# shape is a contract with app.js (insCodeGaps / _jxCodeItems).

def test_code_items_shape(client):
    items = client.get('/api/jurisdictions').get_json()['colorado_baseline']['code_items']
    assert items, 'baseline scope-gap checklist missing'
    keys = [it['key'] for it in items]
    assert len(set(keys)) == len(keys), 'code_item keys must be unique'
    # app.js merges a jurisdiction's items over the baseline BY LABEL (the
    # Settings editor re-slugs keys from the label on save, so the label is the
    # only stable identity) — duplicate labels would silently swallow an item.
    labels = [it['label'].strip().lower() for it in items]
    assert len(set(labels)) == len(labels), 'code_item labels must be unique'
    # The pipe-delimited Settings format can't round-trip a literal pipe.
    assert not any('|' in str(it.get(f, '')) for it in items
                   for f in ('label', 'basis', 'note')), 'no | in editable fields'
    for it in items:
        assert it['class'] in ('code', 'common', 'conditional'), it['key']
        assert it['label'] and it['basis'] and it['note'], it['key']
        assert it['match'], f"{it['key']} has no keywords to match on"
        # Matching is done lowercased against the carrier description.
        assert all(m == m.lower() and m.strip() == m for m in it['match']), it['key']
    # The items the carrier short-pays most often must all be present.
    for k in ('drip_edge', 'ice_water', 'permit', 'ventilation', 'starter'):
        assert k in keys, f'{k} missing from the scope-gap checklist'
    # Code-mandated items are the point of the check — there must be several.
    assert len([it for it in items if it['class'] == 'code']) >= 5


# ── Boundary verification ───────────────────────────────────────────────────
# The Census geocoder answers "is this parcel inside city limits?" against TIGER
# polygons. A missing 'Incorporated Places' entry means unincorporated — the
# county is the AHJ — so that absence is parsed as an answer, not an error.

def test_verify_needs_a_street_address(client):
    r = client.get('/api/jurisdictions/verify?city=Fort+Collins&state=CO')
    assert r.status_code == 200          # never errors — the panel degrades
    assert r.get_json()['ok'] is False


def test_verify_incorporated(client, A, monkeypatch):
    monkeypatch.setattr(A, '_jx_census_by_address', lambda one_line: {
        'place': 'Fort Collins city', 'county': 'Larimer County',
        'source': 'census-address', 'matched_address': '300 LAPORTE AVE, FORT COLLINS, CO, 80521',
        'lat': 40.59, 'lon': -105.08})
    d = client.get('/api/jurisdictions/verify?street=300+Laporte+Ave&city=Fort+Collins'
                   '&state=CO&zip=80521').get_json()
    assert d['ok'] is True
    assert d['incorporated'] is True
    assert d['place_clean'] == 'Fort Collins'      # ' city' suffix stripped
    assert d['county_clean'] == 'Larimer'          # ' County' suffix stripped


def test_verify_unincorporated(client, A, monkeypatch):
    """No Incorporated Places geography = outside every city limit."""
    monkeypatch.setattr(A, '_jx_census_by_address', lambda one_line: {
        'place': None, 'county': 'Larimer County', 'source': 'census-address',
        'matched_address': '', 'lat': 40.61, 'lon': -105.18})
    d = client.get('/api/jurisdictions/verify?street=1+Rist+Canyon+Rd&city=Bellvue'
                   '&state=CO&zip=80512').get_json()
    assert d['ok'] is True
    assert d['incorporated'] is False
    assert d['county_clean'] == 'Larimer'


def test_verify_falls_back_to_osm_point(client, A, monkeypatch):
    """Census can't match rural addresses OSM has — geocode, then test the point."""
    monkeypatch.setattr(A, '_jx_census_by_address', lambda one_line: None)
    monkeypatch.setattr(A, '_jx_nominatim_point', lambda one_line: (40.61, -105.18))
    monkeypatch.setattr(A, '_jx_census_by_coords', lambda lat, lon: {
        'place': None, 'county': 'Larimer County', 'source': 'osm+census-point',
        'matched_address': '', 'lat': lat, 'lon': lon})
    d = client.get('/api/jurisdictions/verify?street=9999+Rural+Rd&city=Bellvue'
                   '&state=CO&zip=80512').get_json()
    assert d['ok'] is True and d['source'] == 'osm+census-point'


def test_verify_survives_a_dead_geocoder(client, A, monkeypatch):
    """Both lookups down → ok:false, not a 500. The rep picks manually."""
    def boom(*a, **k):
        raise RuntimeError('network down')
    monkeypatch.setattr(A, '_jx_census_by_address', boom)
    monkeypatch.setattr(A, '_jx_nominatim_point', boom)
    r = client.get('/api/jurisdictions/verify?street=300+Laporte+Ave&city=Fort+Collins&state=CO')
    assert r.status_code == 200
    assert r.get_json()['ok'] is False


def test_verify_place_suffix_stripping(A):
    assert A._jx_clean_place('Fort Collins city') == 'Fort Collins'
    assert A._jx_clean_place('Windsor town') == 'Windsor'
    assert A._jx_clean_place('Denver city') == 'Denver'
    assert A._jx_clean_place('') == ''
    assert A._jx_clean_county('Larimer County') == 'Larimer'


def test_verified_place_names_resolve_to_jurisdictions(client, A):
    """The verified place name is matched against each entry's match.cities
    aliases (app.js _jxFromVerified) — NOT j.name, which is the formal
    'City of Fort Collins'. The cleaned Census names must line up with those."""
    js = client.get('/api/jurisdictions').get_json()['jurisdictions']
    aliases = {c.lower() for j in js if j['kind'] == 'city'
               for c in (j.get('match', {}).get('cities') or [])}
    for census_name in ('Fort Collins city', 'Loveland city', 'Windsor town',
                        'Greeley city', 'Longmont city', 'Denver city'):
        assert A._jx_clean_place(census_name).lower() in aliases, census_name
    counties = {j['county'].lower() for j in js if j['kind'] == 'county'}
    assert A._jx_clean_county('Larimer County').lower() in counties
