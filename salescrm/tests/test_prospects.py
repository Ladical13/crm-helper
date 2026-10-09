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
             phone='970-555-0100', service='gutter_cleaning')
    leads = client.get('/api/leads').get_json()
    assert len(leads) == 2
    assert {l['service'] for l in leads} == {'roofing', 'gutter_cleaning'}


# ── Warm imports: people who already know us ─────────────────────────────────

def _warm(client, rows, **kw):
    body = {'lead_type': 'homeowner', 'source': 'den:customers',
            'lead_source': 'existing_customer'}
    body.update(kw)
    return _import(client, rows, **body)


def test_a_warm_import_lands_as_a_past_customer(client):
    """A homeowner whose roof we replaced must not arrive as a cold prospect
    and be offered a free hail inspection."""
    signup(client)
    r = _warm(client, [{'first_name': 'Pat', 'last_name': 'Ng', 'phone': '970-555-0101',
                        'email': 'pat@example.com', 'stage': 'won',
                        'won_at': '2026-03-09', 'created_at': '2026-02-01T15:00:00.000000',
                        'source_ref': 'den:contact:1'}])
    assert r.status_code == 201, r.get_json()
    lead = client.get('/api/leads').get_json()[0]
    assert (lead['source'], lead['stage'], lead['temperature']) == ('existing_customer', 'won', 'warm')
    assert lead['won_at'] == '2026-03-09T00:00:00Z'
    # Dated when we met them, so a spring customer is not in October's cohort.
    assert lead['created_at'] == '2026-02-01T15:00:00Z'
    m = client.get(f"/api/leads/{lead['id']}/messages").get_json()
    assert m['audience'] == 'past_customer'
    assert any('review' in t['name'].lower() for t in m['text']['templates'])


def test_a_cold_import_ignores_the_warm_fields(client):
    """`stage` on an open-data row must not let a pull invent a customer."""
    signup(client)
    _import(client, _rows({'license_no': 'HOA-9', 'stage': 'won', 'won_at': '2026-03-09'}))
    lead = client.get('/api/leads').get_json()[0]
    assert (lead['source'], lead['stage'], lead['won_at']) == ('prospecting', 'new', '')


def test_an_open_job_gets_its_own_script_not_a_review_ask(client):
    signup(client)
    _warm(client, [{'first_name': 'Sam', 'last_name': 'Lee', 'phone': '970-555-0102',
                    'stage': 'follow_up', 'source_ref': 'den:project:2'}])
    lead = client.get('/api/leads').get_json()[0]
    names = [t['name'] for t in
             client.get(f"/api/leads/{lead['id']}/messages").get_json()['text']['templates']]
    assert names and all('review' not in n.lower() for n in names), names


def test_a_warm_import_spreads_its_first_touches_over_days(client):
    """Fifty customers imported on Monday must not all be due on Monday."""
    signup(client)
    rows = [{'first_name': f'C{i}', 'last_name': 'X', 'phone': f'970-555-01{i:02d}',
             'stage': 'won', 'source_ref': f'den:contact:{i}'} for i in range(5)]
    r = _warm(client, rows, cadence='past_customer_winter', stagger_per_day=2)
    assert r.status_code == 201, r.get_json()
    with appmod.get_db() as db:
        due = sorted(t['due_at'][:10] for t in db.execute('SELECT due_at FROM tasks WHERE done=0'))
    assert len(due) == 5 and len(set(due)) == 3
    assert [due.count(d) for d in sorted(set(due))] == [2, 2, 1]
    assert len(client.get('/api/queue/today').get_json()['due']) == 2


def test_a_dry_run_starts_no_cadence(client):
    signup(client)
    _warm(client, [{'first_name': 'A', 'last_name': 'B', 'phone': '970-555-0199', 'stage': 'won'}],
          cadence='past_customer_winter', dry_run=True)
    with appmod.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM leads').fetchone()[0] == 0


def test_a_cold_import_cannot_start_a_cadence(client):
    """36k open-data rows must not each grow a task."""
    signup(client)
    r = _import(client, _rows({'license_no': 'HOA-1'}), cadence='partner_nurture')
    assert r.status_code == 400


def test_an_unknown_lead_source_is_refused(client):
    signup(client)
    assert _import(client, _rows({'license_no': 'HOA-1'}), lead_source='website').status_code == 400
    assert _warm(client, [{'company': 'X', 'phone': '970-555-0100'}],
                 cadence='no_such_cadence').status_code == 400


def test_a_customer_stays_with_the_rep_who_sold_the_job_only_when_named(client):
    """The Den names a salesperson on every job, and most of those names are
    another market's reps or people who have left. Honour every one and a
    customer lands in a queue nobody opens."""
    signup(client)
    signup(client, 'derik')
    signup(client, 'bryan')
    login(client, 'luke')
    rows = [{'first_name': 'Dee', 'phone': '970-555-0181', 'stage': 'won', 'owner': 'Derik'},
            {'first_name': 'Bea', 'phone': '970-555-0182', 'stage': 'won', 'owner': 'bryan'},
            {'first_name': 'Tex', 'phone': '970-555-0183', 'stage': 'won', 'owner': 'ted'},
            {'first_name': 'Una', 'phone': '970-555-0184', 'stage': 'won'}]
    body = _warm(client, rows, assign='luke', owners=['derik'],
                 cadence='past_customer_winter').get_json()
    assert [d['rep'] for d in body['details']] == ['derik', 'luke', 'luke', 'luke']
    # The cadence a warm batch starts belongs to the same rep as the lead.
    with appmod.get_db() as db:
        mismatched = db.execute('SELECT COUNT(*) FROM tasks t JOIN leads l ON l.id = t.lead_id '
                                'WHERE t.rep != l.rep').fetchone()[0]
    assert mismatched == 0


def test_keeping_an_owner_is_for_warm_imports_and_real_reps(client):
    signup(client)
    assert _import(client, _rows({'license_no': 'HOA-1'}), owners=['luke']).status_code == 400
    assert _warm(client, [{'first_name': 'A', 'phone': '970-555-0185'}],
                 owners=['nobody']).status_code == 400


def test_parse_stamp_reads_what_the_den_writes():
    assert appmod._parse_stamp('2026-03-09') == '2026-03-09T00:00:00Z'
    assert appmod._parse_stamp('2026-03-09T17:04:05.123000') == '2026-03-09T17:04:05Z'
    assert appmod._parse_stamp('2026-03-09T17:04:05Z') == '2026-03-09T17:04:05Z'
    assert appmod._parse_stamp('2026-03-09T10:04:05-07:00') == '2026-03-09T17:04:05Z'
    assert appmod._parse_stamp('') == '' and appmod._parse_stamp('last spring') == ''
