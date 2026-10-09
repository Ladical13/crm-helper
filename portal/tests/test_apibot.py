"""The read-only executive-team principal.

This is the one place in the codebase where something other than a human
password gets you a session, so the tests are deliberately paranoid. The
properties that matter, in order:

  1. No token, wrong token, or no configured token → no session.
  2. The principal it issues can ONLY do GETs, on an allowlist.
  3. Neither the token nor a way to guess it ever appears in a response.

If any of those break, this stops being a reporting credential and starts being
a way into the business.
"""
import pytest

TOKEN = 'test-token-do-not-use-in-production'


@pytest.fixture
def bridge(monkeypatch):
    """Turn the bridge on with a known token.

    Deliberately does NOT touch the data dirs: conftest freezes those at import
    time (each sub-app bakes DATA_DIR into module constants), so re-pointing
    them here would leave the app reading one database and the test asserting
    against another.
    """
    monkeypatch.setenv('P1_READONLY_TOKEN', TOKEN)
    yield


def _exchange(client, token=TOKEN):
    return client.post('/api/apibot/session', headers={'X-P1-Token': token})


# ── Getting in ───────────────────────────────────────────────────────────────

def test_valid_token_returns_a_session(client, bridge):
    r = _exchange(client)
    assert r.status_code == 200
    body = r.get_json()
    assert body['username'] == 'apibot'
    assert body['read_only'] is True
    assert body['allowlist']


def test_wrong_token_is_rejected(client, bridge):
    assert _exchange(client, 'not-the-token').status_code == 401


def test_missing_header_is_rejected(client, bridge):
    assert client.post('/api/apibot/session').status_code == 401


def test_empty_token_header_is_rejected(client, bridge):
    """An empty header must not compare equal to an unset expectation."""
    assert _exchange(client, '').status_code == 401


def test_the_endpoint_hides_when_no_token_is_configured(client, monkeypatch):
    """404, not 401 — a prober should not learn the feature exists here."""
    monkeypatch.delenv('P1_READONLY_TOKEN', raising=False)
    assert _exchange(client).status_code == 404


def test_the_token_never_appears_in_a_response(client, bridge):
    body = _exchange(client).get_data(as_text=True)
    assert TOKEN not in body


def test_apibot_cannot_log_in_through_the_form(client, bridge):
    """The account exists but holds a random password nobody has. The token is
    the only route to it."""
    _exchange(client)          # creates the user
    for guess in ('apibot', 'password', '', 'apibot123'):
        r = client.post('/login', data={'username': 'apibot', 'password': guess})
        assert r.status_code != 302 or '/login' in r.headers.get('Location', '')


def test_repeated_exchange_is_idempotent(client, bridge):
    """Creating the principal twice must not error or duplicate the user."""
    assert _exchange(client).status_code == 200
    assert _exchange(client).status_code == 200
    from portal import users
    assert users.get('apibot') is not None


# ── What it can and cannot do once in ────────────────────────────────────────

def test_allowlisted_get_is_permitted(client, bridge):
    _exchange(client)
    # Reaches the endpoint rather than being turned away by the guard. The
    # endpoint's own status is not this test's business — 403 from `guard` is.
    r = client.get('/nimbus/api/settings')
    assert r.status_code != 403 or 'apibot' not in r.get_data(as_text=True)


def test_every_write_verb_is_refused(client, bridge):
    """The core promise. A read-only credential that can POST is not read-only."""
    _exchange(client)
    for verb, call in (
        ('POST',   client.post),
        ('PUT',    client.put),
        ('PATCH',  client.patch),
        ('DELETE', client.delete),
    ):
        r = call('/nimbus/api/settings')
        assert r.status_code == 403, f'{verb} was not refused'
        assert 'read-only' in r.get_json()['error']


def test_a_write_to_an_allowlisted_path_is_still_refused(client, bridge):
    """Being on the allowlist buys a GET, not a method."""
    _exchange(client)
    r = client.post('/crm/api/goals', json={'rep': 'luke', 'target': 1})
    assert r.status_code == 403


def test_non_allowlisted_get_is_refused(client, bridge):
    _exchange(client)
    for path in ('/api/users', '/nimbus/', '/api/me'):
        r = client.get(path)
        assert r.status_code == 403, path
        assert 'not available' in r.get_json()['error']


def test_customer_data_paths_are_not_reachable(client, bridge):
    """The allowlist is aggregates only. A reporting credential that can also
    pull contact details and documents is a much bigger thing to lose."""
    _exchange(client)
    for path in ('/crm/api/documents', '/estimate/api/estimates',
                 '/api/users', '/crm/api/leads/1/activities'):
        # Refused, by whichever layer gets there first: 403 from the guard,
        # 404 if no such route, 405 if the route is not a GET. Werkzeug raises
        # 404/405 during URL matching, before any before_request hook runs, so
        # asserting a bare 403 would be asserting the order of two unrelated
        # mechanisms. What matters is that nothing is served.
        status = client.get(path).status_code
        assert status >= 400, f'{path} returned {status}'


# ── The guard does not affect anybody else ───────────────────────────────────

def test_a_normal_admin_is_untouched(admin, bridge):
    """The guard must be invisible to every other principal — it returns early
    on a session that isn't apibot."""
    assert admin.get('/api/me').status_code == 200
    r = admin.post('/nimbus/api/schedule/seo_weekly', json={'enabled': True})
    assert r.status_code != 403


def test_anonymous_still_gets_401_not_403(client, bridge):
    """The apibot guard must not shadow the portal's own default-deny."""
    assert client.get('/api/users').status_code == 401


# ── Path matching ────────────────────────────────────────────────────────────

def test_allowlist_matches_the_mounted_path_not_the_sub_app_path():
    """DispatcherMiddleware strips the mount prefix before the sub-app sees the
    request, so the guard reconstructs script_root + path. If that ever
    regresses, '/api/analytics' would match nothing and the bridge would look
    broken rather than insecure — but check it explicitly."""
    from portal import apibot
    assert apibot.path_allowed('/estimate/api/analytics')
    assert not apibot.path_allowed('/api/analytics')


def test_allowlist_does_not_match_by_substring():
    """A prefix check must not let '/estimate/api/analytics-export' through by
    accident — or rather, if it does, that must be a deliberate choice."""
    from portal import apibot
    assert not apibot.path_allowed('/evil/estimate/api/analytics')
    assert not apibot.path_allowed('/crm/api/leadsX/../../secret')


def test_leads_list_does_not_open_the_records_beneath_it():
    """'/crm/api/leads' is the pipeline list. A prefix match also opened a
    lead's full contact record and its documents (signed contracts)."""
    from portal import apibot
    assert apibot.path_allowed('/crm/api/leads')
    assert apibot.path_allowed('/crm/api/leads?stage=new')
    for path in ('/crm/api/leads/abc', '/crm/api/leads/abc/documents',
                 '/crm/api/leads/abc/messages', '/crm/api/leads/unplaced'):
        assert not apibot.path_allowed(path), path


# ── Jarvis: the outreach queue, Nimbus, and the one write ────────────────────

def _lead_for(admin, rep='bryan'):
    """A lead owned by `rep`, created the way a manager would."""
    from portal import users
    if not users.get(rep):
        users.create(rep, password='knockknock', role='rep')
    r = admin.post('/crm/api/leads', json={
        'first_name': 'Pat', 'last_name': 'Agent', 'lead_type': 'realtor',
        'phone': '970-555-1212', 'email': 'pat@example.com', 'rep': rep})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()['id']


def _crm_db():
    import sys
    return sys.modules['p1_crm_app'].get_db()


def test_nimbus_can_be_read_and_run_but_not_its_spend_cap(client, bridge):
    """Nimbus gates on is_admin; apibot is a manager, so it used to be shut out
    of a path the allowlist named. Jarvis may now run Nimbus — it drafts and
    researches, never publishes — but the settings holding the monthly spend
    cap stay with a person, by every method."""
    _exchange(client)
    assert client.get('/nimbus/api/settings').status_code == 200
    for call in (client.post, client.put, client.patch, client.delete):
        assert call('/nimbus/api/settings', json={'monthly_spend_cap_usd': 9999}).status_code == 403
    r = client.post('/nimbus/api/schedule/seo_weekly', json={'enabled': False})
    assert r.status_code == 200, r.get_data(as_text=True)


def test_prefix_writes_refuse_paths_that_could_resolve_elsewhere():
    from portal import apibot
    assert not apibot.write_allowed('POST', '/nimbus/api/../../api/users')
    assert not apibot.write_allowed('POST', '/nimbus/api//settings')


def test_crm_writes_are_named_routes_only(client, bridge):
    from portal import apibot
    assert apibot.write_allowed('POST', '/crm/api/queue/log')
    assert apibot.write_allowed('PATCH', '/crm/api/leads/abc/outreach-status')
    assert not apibot.write_allowed('POST', '/crm/api/leads/abc/outreach-status')
    assert not apibot.write_allowed('PATCH', '/crm/api/leads/abc')
    assert not apibot.write_allowed('PATCH', '/crm/api/leads/abc/stage')
    assert not apibot.write_allowed('GET', '/crm/api/queue/log')
    assert not apibot.write_allowed('POST', '/crm/api/queue/assign')
    assert not apibot.write_allowed('POST', '/crm/api/leads')
    assert not apibot.write_allowed('POST', '/crm/api/queue/log/x')
    _exchange(client)
    for path in ('/crm/api/leads', '/crm/api/queue/assign', '/crm/api/goals'):
        assert client.post(path, json={}).status_code == 403, path


def test_a_logged_touch_is_credited_to_the_lead_owner(admin, bridge):
    lead_id = _lead_for(admin)
    _exchange(admin)
    r = admin.post('/crm/api/queue/log', json={
        'lead_id': lead_id, 'kind': 'email', 'ref': 'gmail-123'})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body['rep'] == 'bryan' and body['logged'] is True
    with _crm_db() as db:
        acts = db.execute("SELECT * FROM activities WHERE lead_id=? AND kind='email'",
                          (lead_id,)).fetchall()
        assert len(acts) == 1
        assert acts[0]['rep'] == 'bryan'
        assert 'via Jarvis' in acts[0]['body']
        # The cooldown keys off this; without it tomorrow's queue repeats today.
        assert db.execute('SELECT last_activity_at FROM leads WHERE id=?',
                          (lead_id,)).fetchone()[0]


def test_the_same_ref_logs_once(admin, bridge):
    """Jarvis re-scans Sent; a second pass must not double the day's count."""
    lead_id = _lead_for(admin)
    _exchange(admin)
    payload = {'lead_id': lead_id, 'kind': 'email', 'ref': 'gmail-dup'}
    assert admin.post('/crm/api/queue/log', json=payload).status_code == 201
    second = admin.post('/crm/api/queue/log', json=payload)
    assert second.status_code == 200 and second.get_json()['duplicate'] is True
    with _crm_db() as db:
        n = db.execute("SELECT COUNT(*) FROM activities WHERE lead_id=? AND kind='email'",
                       (lead_id,)).fetchone()[0]
    assert n == 1


def test_the_log_accepts_outreach_kinds_only(admin, bridge):
    lead_id = _lead_for(admin)
    _exchange(admin)
    for kind in ('note', 'system', '', None):
        r = admin.post('/crm/api/queue/log', json={'lead_id': lead_id, 'kind': kind})
        assert r.status_code == 400, kind


def test_bulk_lead_list_hides_contact_details_from_apibot(admin, bridge):
    lead_id = _lead_for(admin)
    _exchange(admin)
    leads = admin.get('/crm/api/leads').get_json()
    mine = next(l for l in leads if l['id'] == lead_id)
    assert mine['phone'] == '' and mine['email'] == ''
    assert mine['first_name'] == 'Pat'           # names stay: "the Smith deal"


def test_queue_needs_a_rep_and_carries_contacts(admin, bridge):
    """The day's work list is where contact details legitimately arrive."""
    _lead_for(admin)
    _exchange(admin)
    assert admin.get('/crm/api/queue/today').status_code == 400
    q = admin.get('/crm/api/queue/today?rep=bryan&target=100').get_json()
    assert q['rep'] == 'bryan' and q['target'] == 100
    cards = q['due'] + q['new']
    assert any(c.get('email') == 'pat@example.com' for c in cards)


def test_a_human_is_unaffected_by_the_redaction(admin, bridge):
    lead_id = _lead_for(admin)
    leads = admin.get('/crm/api/leads').get_json()
    assert next(l for l in leads if l['id'] == lead_id)['phone'] == '970-555-1212'


def test_jarvis_records_a_reply_but_cannot_book_an_appointment(admin, bridge):
    lead_id = _lead_for(admin)
    _exchange(admin)
    url = f'/crm/api/leads/{lead_id}/outreach-status'
    r = admin.patch(url, json={'status': 'interested'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()['outreach_status'] == 'interested'
    assert admin.patch(url, json={'status': 'dnc'}).status_code == 200   # opt-outs must land
    assert admin.patch(url, json={'status': 'appt_set'}).status_code == 403


def test_nimbus_prospecting_can_land_leads_but_only_with_a_named_rep(admin, bridge):
    """A Nimbus b2b run forwards the caller's session into the CRM importer, so
    apibot needs that one route — and must never become the assignee."""
    from portal import users
    users.create('bryan', password='knockknock', role='rep')
    _exchange(admin)
    row = {'company': 'Front Range HOA Mgmt', 'license_no': 'L-1', 'city': 'Loveland',
           'source_ref': 'test:1'}
    body = {'rows': [row], 'lead_type': 'hoa', 'source': 'nimbus'}
    assert admin.post('/crm/api/prospects/import', json=body).status_code == 400
    assert admin.post('/crm/api/prospects/import',
                      json=dict(body, assign='apibot')).status_code == 400
    r = admin.post('/crm/api/prospects/import', json=dict(body, assign='bryan'))
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    assert admin.get('/crm/api/partners/counts').status_code == 200


# ── The draft ledger and the plan ────────────────────────────────────────────

def test_jarvis_reserves_a_draft_and_closes_it_by_logging_the_send(admin, bridge):
    """Reserve, write it in Gmail, come back with the id, log it once it is in
    Sent. Each step is a named route; nothing else under /queue/drafts opens."""
    from portal import apibot
    lead_id = _lead_for(admin)
    _exchange(admin)
    d = admin.post('/crm/api/queue/drafts', json={'lead_id': lead_id, 'subject': 'Roof answers'})
    assert d.status_code == 201, d.get_data(as_text=True)
    draft = d.get_json()
    assert draft['rep'] == 'bryan' and draft['recipient'] == 'pat@example.com'
    url = f"/crm/api/queue/drafts/{draft['id']}"
    assert admin.patch(url, json={'draft_ref': 'r-9'}).status_code == 200
    assert admin.patch(url, json={'status': 'sent'}).status_code == 400     # only the log sends
    pending = admin.get('/crm/api/queue/drafts?rep=bryan&status=pending').get_json()
    assert [p['draft_ref'] for p in pending] == ['r-9']
    assert admin.get('/crm/api/queue/drafts').status_code == 400            # must name the rep
    r = admin.post('/crm/api/queue/log', json={
        'lead_id': lead_id, 'kind': 'email', 'outcome': 'emailed',
        'draft_id': draft['id'], 'ref': 'gmail-9'})
    assert r.status_code == 201 and r.get_json()['rep'] == 'bryan'
    sent = admin.get('/crm/api/queue/drafts?rep=bryan&status=sent').get_json()
    assert [s['message_ref'] for s in sent] == ['gmail-9']
    assert admin.patch(url, json={'reply': 'interested'}).status_code == 200
    assert admin.patch(url, json={'reply': 'appt_set'}).status_code == 400  # booked by a person

    assert apibot.write_allowed('PATCH', '/crm/api/queue/drafts/abc')
    assert not apibot.write_allowed('PATCH', '/crm/api/queue/drafts')
    assert not apibot.write_allowed('DELETE', '/crm/api/queue/drafts/abc')
    assert not apibot.write_allowed('PATCH', '/crm/api/queue/drafts/abc/x')
    assert not apibot.path_allowed('/crm/api/queue/drafts/abc')


def test_jarvis_reads_the_plan_and_the_scorecard_but_cannot_move_the_ramp(admin, bridge):
    """The plan holds how many emails a day leave a rep's inbox. Jarvis
    recommending a higher number must not be the same act as setting one."""
    from portal import apibot, users
    users.create('bryan', password='knockknock', role='rep')
    r = admin.put('/crm/api/outreach/plan/bryan', json={'daily_target': 100, 'email_share': 15})
    assert r.status_code == 200, r.get_data(as_text=True)                   # the manager may
    _exchange(admin)
    assert admin.get('/crm/api/outreach/plan?rep=bryan').get_json()['email_share'] == 15
    card = admin.get('/crm/api/outreach/scorecard?rep=bryan')
    assert card.status_code == 200 and card.get_json()['today']['target'] == 100
    assert admin.get('/crm/api/outreach/scorecard').status_code == 400      # must name the rep
    assert admin.put('/crm/api/outreach/plan/bryan', json={'email_share': 40}).status_code == 403
    assert not apibot.write_allowed('PUT', '/crm/api/outreach/plan/bryan')
    assert not apibot.path_allowed('/crm/api/outreach/plan/bryan')
    assert admin.get('/crm/api/outreach/plan?rep=bryan').get_json()['email_share'] == 15
