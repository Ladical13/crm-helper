"""The Den link on an estimate — `customer.crm_contact_id`.

This one field is what makes bid-versus-actual possible: it joins an estimate
to its job in Base44, and therefore to the job's real costs. Without it the
only way to match a bid to what the work actually cost is by customer name,
which mismatches quietly and produces confident, wrong margins.

It was declared on the estimate document from the start but never populated —
the CRM has always sent `?contact=<id>` on the handoff URL and the estimator
dropped it, and the in-app job picker hardcoded null even though the project it
had just fetched carried one. These tests exist so it cannot silently go back
to null.
"""


def test_the_link_survives_a_round_trip(client):
    """Saved on the estimate, returned when it is read back."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    est = client.get(f'/api/estimates/{eid}').get_json()
    est.setdefault('customer', {})['crm_contact_id'] = 'contact_abc123'
    assert client.put(f'/api/estimates/{eid}', json=est).status_code == 200

    back = client.get(f'/api/estimates/{eid}').get_json()
    assert back['customer']['crm_contact_id'] == 'contact_abc123'


def test_the_list_exposes_the_link(client):
    """The CFO's margin work reads the list, not each estimate in turn. If the
    key is not in the list projection the join is impossible without N calls."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    est = client.get(f'/api/estimates/{eid}').get_json()
    est.setdefault('customer', {})['crm_contact_id'] = 'contact_xyz789'
    client.put(f'/api/estimates/{eid}', json=est)

    row = next(e for e in client.get('/api/estimates').get_json()
               if e['estimate_id'] == eid)
    assert 'crm_contact_id' in row, 'the join key must be in the list projection'
    assert row['crm_contact_id'] == 'contact_xyz789'


def test_an_unlinked_estimate_reports_empty_not_missing(client):
    """Estimates made before this shipped, or started outside the CRM, have no
    link. That must read as an explicit empty string — "not matchable" — rather
    than an absent key that a consumer might paper over."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    row = next(e for e in client.get('/api/estimates').get_json()
               if e['estimate_id'] == eid)
    assert row['crm_contact_id'] == ''


def test_the_project_picker_exposes_contact_id(client, monkeypatch):
    """The in-app CRM job picker sets the link from the project it fetched, so
    the API that feeds it must carry contact_id through. It used to be dropped
    by the slim projection, which is why the picker hardcoded null."""
    import app as estimator_app

    monkeypatch.setattr(estimator_app, 'fetch_all_projects', lambda: [{
        'id': 'proj_1', 'contact_id': 'contact_from_project',
        'name': 'Test Job', 'job_number': 'J-1', 'client_name': 'Testy',
        'client_phone': '', 'client_email': '', 'address': '1 Main St',
        'status': 'contracted', 'assigned_salesperson': '',
        'created_date': '2026-01-01',
    }])

    rows = client.get('/api/crm/jobs?q=test').get_json()
    assert rows, 'expected the stubbed project back'
    assert rows[0]['contact_id'] == 'contact_from_project'
