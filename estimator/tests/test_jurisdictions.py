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
