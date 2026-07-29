"""Bulk prospect import, dedupe and suppression.

The invariants worth guarding here are the ones that bite silently: an import
that duplicates on retry, a dedupe rule that leaks into the cross-sell flow, and
an opt-out that a later batch quietly undoes.
"""
import app as appmod
from conftest import signup, login, new_lead


def _rows(*specs):
    """Prospect rows in the shape prospector/ emits."""
    out = []
    for s in specs:
        row = {'company': 'Acme HOA', 'city': 'Fort Collins', 'state': 'CO'}
        row.update(s)
        out.append(row)
    return out


def _import(client, rows, **kw):
    body = {'rows': rows, 'lead_type': 'hoa', 'source': 'dora'}
    body.update(kw)
    return client.post('/api/prospects/import', json=body)


# ── Normalization ────────────────────────────────────────────────────────────

def test_norm_phone_collapses_formatting():
    n = appmod._norm_phone
    assert n('(970) 555-1212') == '9705551212'
    assert n('970-555-1212') == '9705551212'
    assert n('+1 970 555 1212') == '9705551212'
    assert n('9705551212') == '9705551212'


def test_norm_phone_rejects_unusable():
    n = appmod._norm_phone
    assert n('555-1212') == ''        # no area code — unreachable
    assert n('') == ''
    assert n(None) == ''
    assert n('n/a') == ''


def test_norm_email_lowercases_and_strips():
    assert appmod._norm_email('  Jane@Acme.COM ') == 'jane@acme.com'
    assert appmod._norm_email(None) == ''


def test_host_of_strips_scheme_and_www():
    h = appmod._host_of
    assert h('https://www.acme.com/about?x=1') == 'acme.com'
    assert h('acme.com') == 'acme.com'
    assert h('') == ''


def test_create_lead_stores_normalized_forms(client):
    signup(client)
    lead = new_lead(client, phone='(970) 555-0100', email='  Jane@Acme.COM ')
    with appmod.get_db() as db:
        row = db.execute('SELECT phone_norm, email_norm FROM leads WHERE id=?',
                         (lead['id'],)).fetchone()
    assert row['phone_norm'] == '9705550100'
    assert row['email_norm'] == 'jane@acme.com'


def test_update_lead_keeps_normalized_forms_in_step(client):
    signup(client)
    lead = new_lead(client, phone='(970) 555-0100')
    client.put(f"/api/leads/{lead['id']}", json={'phone': '970-555-9999'})
    with appmod.get_db() as db:
        row = db.execute('SELECT phone_norm FROM leads WHERE id=?', (lead['id'],)).fetchone()
    assert row['phone_norm'] == '9705559999'


# ── Import ───────────────────────────────────────────────────────────────────

def test_import_creates_leads(client):
    signup(client)
    r = _import(client, _rows({'company': 'Ridge HOA', 'license_no': 'HOA-1'},
                              {'company': 'Vista HOA', 'license_no': 'HOA-2'}))
    assert r.status_code == 201
    body = r.get_json()
    assert body['counts']['inserted'] == 2
    leads = client.get('/api/leads').get_json()
    assert {l['company'] for l in leads} == {'Ridge HOA', 'Vista HOA'}
    assert all(l['lead_type'] == 'hoa' for l in leads)
    assert all(l['temperature'] == 'cold' and l['source'] == 'prospecting' for l in leads)


def test_import_is_idempotent(client):
    """Re-running a batch must insert nothing — a half-failed run is retryable."""
    signup(client)
    rows = _rows({'company': 'Ridge HOA', 'license_no': 'HOA-1'},
                 {'company': 'Vista HOA', 'license_no': 'HOA-2'})
    assert _import(client, rows).get_json()['counts']['inserted'] == 2
    second = _import(client, rows).get_json()['counts']
    assert second['inserted'] == 0
    assert second['duplicate'] == 2
    assert len(client.get('/api/leads').get_json()) == 2


def test_import_dedupes_within_one_batch(client):
    """The same brokerage routinely appears twice in one open-data pull."""
    signup(client)
    body = _import(client, _rows({'company': 'Ridge HOA', 'license_no': 'HOA-1'},
                                 {'company': 'Ridge HOA', 'license_no': 'HOA-1'})).get_json()
    assert body['counts'] == {'inserted': 1, 'duplicate': 1, 'suppressed': 0, 'invalid': 0}


def test_import_dedupes_on_phone_and_email(client):
    signup(client)
    _import(client, _rows({'company': 'A', 'phone': '(970) 555-0100'}))
    body = _import(client, _rows({'company': 'Different Name', 'phone': '970-555-0100'})).get_json()
    assert body['counts']['duplicate'] == 1


def test_import_dedupes_against_hand_entered_leads(client):
    """A partner the rep already added must not come back as a fresh prospect."""
    signup(client)
    new_lead(client, first_name='Jane', last_name='Doe', email='jane@acme.com')
    body = _import(client, _rows({'company': 'Acme', 'email': 'Jane@Acme.com'})).get_json()
    assert body['counts']['duplicate'] == 1


def test_import_rejects_rows_with_nothing_to_dedupe_on(client):
    """No stable key means every future re-import would duplicate the row."""
    signup(client)
    body = _import(client, _rows({'company': 'Anonymous HOA'})).get_json()
    assert body['counts']['invalid'] == 1
    assert 'dedupe' in body['details'][0]['reason']


def test_import_rejects_nameless_rows(client):
    signup(client)
    body = _import(client, _rows({'company': '', 'license_no': 'X-1'})).get_json()
    assert body['counts']['invalid'] == 1


def test_dry_run_writes_nothing_but_classifies(client):
    signup(client)
    rows = _rows({'company': 'Ridge HOA', 'license_no': 'HOA-1'},
                 {'company': 'Ridge HOA', 'license_no': 'HOA-1'})
    r = _import(client, rows, dry_run=True)
    assert r.status_code == 200
    assert r.get_json()['counts'] == {'inserted': 1, 'duplicate': 1,
                                      'suppressed': 0, 'invalid': 0}
    assert client.get('/api/leads').get_json() == []


def test_import_requires_manager(client):
    signup(client, 'luke')          # first account bootstraps as admin
    signup(client, 'bryan')         # now signed in as a rep
    r = _import(client, _rows({'company': 'Ridge HOA', 'license_no': 'HOA-1'}))
    assert r.status_code == 403


def test_import_round_robins_across_reps(client):
    signup(client, 'luke')
    signup(client, 'bryan')
    signup(client, 'derik')
    login(client, 'luke')
    rows = _rows(*[{'company': f'HOA {i}', 'license_no': f'H-{i}'} for i in range(4)])
    body = _import(client, rows, assign='round_robin').get_json()
    assert body['counts']['inserted'] == 4
    assert sorted(body['assigned_to']) == ['bryan', 'derik']
    assert [d['rep'] for d in body['details']] == ['bryan', 'derik', 'bryan', 'derik']


def test_import_rejects_unknown_rep(client):
    signup(client)
    r = _import(client, _rows({'company': 'A', 'license_no': 'X'}), assign='nobody')
    assert r.status_code == 400


def test_batches_endpoint_summarizes_imports(client):
    signup(client)
    _import(client, _rows({'company': 'Ridge HOA', 'license_no': 'HOA-1'}), batch='dora-2026-07')
    rows = client.get('/api/prospects/batches').get_json()
    assert rows[0]['batch'] == 'dora-2026-07'
    assert rows[0]['leads'] == 1


# ── Suppression ──────────────────────────────────────────────────────────────

def test_suppression_blocks_import(client):
    signup(client)
    client.post('/api/suppressions', json={'kind': 'email', 'value': 'Jane@Acme.COM',
                                           'reason': 'asked to stop'})
    body = _import(client, _rows({'company': 'Acme', 'email': 'jane@acme.com'})).get_json()
    assert body['counts']['suppressed'] == 1
    assert client.get('/api/leads').get_json() == []


def test_suppression_blocks_by_phone_and_domain(client):
    signup(client)
    client.post('/api/suppressions', json={'kind': 'phone', 'value': '(970) 555-0100'})
    client.post('/api/suppressions', json={'kind': 'domain', 'value': 'https://www.blocked.com/'})
    body = _import(client, _rows({'company': 'A', 'phone': '970-555-0100'},
                                 {'company': 'B', 'email': 'x@blocked.com'},
                                 {'company': 'C', 'website': 'www.blocked.com',
                                  'license_no': 'L-3'})).get_json()
    assert body['counts']['suppressed'] == 3


def test_suppressing_flags_existing_leads_dnc(client):
    """An opt-out has to reach leads already in the pipeline, not just imports."""
    signup(client)
    lead = new_lead(client, email='jane@acme.com')
    client.post('/api/suppressions', json={'kind': 'email', 'value': 'jane@acme.com'})
    with appmod.get_db() as db:
        row = db.execute('SELECT dnc FROM leads WHERE id=?', (lead['id'],)).fetchone()
    assert row['dnc'] == 1


def test_duplicate_suppression_is_not_an_error(client):
    signup(client)
    first = client.post('/api/suppressions', json={'kind': 'email', 'value': 'a@b.com'})
    again = client.post('/api/suppressions', json={'kind': 'email', 'value': 'A@B.com'})
    assert first.status_code == 201 and again.status_code == 200
    assert len(client.get('/api/suppressions').get_json()) == 1


def test_suppression_rejects_bad_input(client):
    signup(client)
    assert client.post('/api/suppressions', json={'kind': 'fax', 'value': 'x'}).status_code == 400
    assert client.post('/api/suppressions',
                       json={'kind': 'phone', 'value': '555-1212'}).status_code == 400


def test_only_managers_remove_suppressions(client):
    signup(client, 'luke')
    sid = client.post('/api/suppressions', json={'kind': 'email', 'value': 'a@b.com'}
                      ).get_json()['id']
    signup(client, 'bryan')
    assert client.delete(f'/api/suppressions/{sid}').status_code == 403
    login(client, 'luke')
    assert client.delete(f'/api/suppressions/{sid}').status_code == 200


# ── The flow dedupe must NOT break ───────────────────────────────────────────

def test_cross_sell_still_creates_a_second_lead(client):
    """Pitching a second service to the same person is a separate deal, by design."""
    signup(client)
    new_lead(client, first_name='Jane', last_name='Doe',
             phone='970-555-0100', service='roofing')
    new_lead(client, first_name='Jane', last_name='Doe',
             phone='970-555-0100', service='window_cleaning')
    leads = client.get('/api/leads').get_json()
    assert len(leads) == 2
    assert {l['service'] for l in leads} == {'roofing', 'window_cleaning'}
