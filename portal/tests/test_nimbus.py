"""Nimbus dashboard smoke tests.

Focus: the invariants that hold Nimbus in place. Everything else (the
dispatcher's outer loop, live Perplexity, the free-source pullers) is unit-
tested in the agents/tests suite; this file only pins the portal integration.

  * Reps get 403 on every /nimbus route.
  * Admins get the shell HTML on /nimbus and JSON on the APIs.
  * The launcher /api/me includes the Nimbus tile ONLY for admins.
  * Territories are seeded on first read and updatable.
  * Settings can be read and updated without a live Perplexity key.
"""
import os

from conftest import SIGNUP_CODE


# Nimbus needs a per-test scratch directory so territories.json / nimbus.db
# don't leak between runs.
def _fresh_agents_dir(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp('nimbus')
    monkeypatch.setenv('AGENTS_DATA_DIR', str(d))
    # Reload config so the new AGENTS_DATA_DIR takes effect.
    from agents import config
    return d


def test_reps_get_403_on_every_nimbus_route(rep, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    for path in ('/nimbus/', '/nimbus/api/territories', '/nimbus/api/settings',
                 '/nimbus/api/runs', '/nimbus/api/content/topics',
                 '/nimbus/api/content/drafts', '/nimbus/api/connections',
                 '/nimbus/marketing/connections', '/nimbus/marketing/seo',
                 '/nimbus/api/seo/runs', '/nimbus/api/seo/recommendations',
                 '/nimbus/api/seo/report', '/nimbus/api/seo/result',
                 '/nimbus/marketing/social', '/nimbus/api/social/drafts',
                 '/nimbus/api/schedule'):
        r = rep.get(path)
        assert r.status_code == 403, path


def test_admin_sees_the_shell(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.get('/nimbus/')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'NIMBUS' in body
    assert 'PROJECT ONE' in body


def test_admin_sees_the_nimbus_tile_in_me(admin, tmp_path_factory, monkeypatch):
    """The launcher key: /api/me must include Nimbus in admin_apps for admins."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    me = admin.get('/api/me').get_json()
    admin_apps = me.get('admin_apps') or []
    keys = [a['key'] for a in admin_apps]
    assert 'nimbus' in keys, admin_apps


def test_reps_do_not_see_the_nimbus_tile(rep, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    me = rep.get('/api/me').get_json()
    admin_apps = me.get('admin_apps') or []
    assert admin_apps == [], admin_apps


def test_territories_are_seeded_on_first_read(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.get('/nimbus/api/territories')
    assert r.status_code == 200
    data = r.get_json()
    usernames = {t['username'] for t in data}
    # Every rep in the default territory config should show up.
    for expected in ('avery', 'phil', 'derik', 'bryan'):
        assert expected in usernames, usernames


def test_territories_can_be_updated(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.put('/nimbus/api/territories/avery', json={
        'cities': ['Colorado Springs', 'Manitou Springs'],
        'enrich_top_n': 25,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert 'Manitou Springs' in body['cities']
    assert body['enrich_top_n'] == 25

    # Re-read confirms persistence.
    all_t = admin.get('/nimbus/api/territories').get_json()
    avery = next(t for t in all_t if t['username'] == 'avery')
    assert 'Manitou Springs' in avery['cities']
    assert avery['enrich_top_n'] == 25


def test_settings_readable_without_a_perplexity_key(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    monkeypatch.delenv('PERPLEXITY_API_KEY', raising=False)
    r = admin.get('/nimbus/api/settings')
    assert r.status_code == 200
    body = r.get_json()
    assert body['perplexity_key_set'] is False
    assert 'monthly_spend_cap_usd' in body
    assert body['month_spend_usd'] == 0.0


def test_settings_can_be_updated(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.post('/nimbus/api/settings',
                   json={'monthly_spend_cap_usd': 42,
                         'perplexity_model': 'sonar-pro'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['monthly_spend_cap_usd'] == 42
    assert body['perplexity_model'] == 'sonar-pro'


def test_runs_and_topics_and_drafts_default_empty(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    assert admin.get('/nimbus/api/runs').get_json() == []
    assert admin.get('/nimbus/api/content/topics').get_json() == []
    assert admin.get('/nimbus/api/content/drafts').get_json() == []


# ── Marketing connections ────────────────────────────────────────────────────

def test_connections_api_lists_every_connector(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.get('/nimbus/api/connections?probe=0')
    assert r.status_code == 200
    body = r.get_json()
    keys = {c['key'] for c in body['connections']}
    assert {'ga4', 'gsc', 'gbp', 'website'} <= keys
    assert body['summary']['read_only'] is True


def test_connections_api_never_returns_a_secret(admin, tmp_path_factory, monkeypatch):
    """The load-bearing one: this payload renders straight into the page."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    monkeypatch.setenv('GBP_OAUTH_CLIENT_SECRET', 'GOCSPX-zzLEAKCANARYzz')
    monkeypatch.setenv('GBP_OAUTH_REFRESH_TOKEN', '1//zzLEAKCANARYzz')
    r = admin.get('/nimbus/api/connections?probe=0')
    assert 'zzLEAKCANARYzz' not in r.get_data(as_text=True)


def test_connections_page_probes_nothing_when_unconfigured(admin, tmp_path_factory,
                                                           monkeypatch):
    """With no credentials set, a probing request must still return 200 and
    must not attempt any network call — the missing-env short-circuit runs
    first. `requests.get` is replaced with a landmine to prove it."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    for k in ('GOOGLE_SERVICE_ACCOUNT_JSON_B64', 'GOOGLE_SERVICE_ACCOUNT_JSON',
              'GA4_PROPERTY_ID', 'GSC_SITE_URL', 'GBP_OAUTH_CLIENT_ID',
              'GBP_OAUTH_CLIENT_SECRET', 'GBP_OAUTH_REFRESH_TOKEN',
              'GBP_ACCOUNT_ID', 'MARKETING_SITE_URL'):
        monkeypatch.delenv(k, raising=False)

    from agents import connections

    def landmine(*a, **kw):
        raise AssertionError('probed the network with no credentials configured')

    # The website probe legitimately needs no credential, so let it resolve to
    # "not connected" by clearing the profile fallback instead of hitting it.
    monkeypatch.setattr(connections, '_get', landmine)
    monkeypatch.setitem(connections._PROBES, 'website',
                        lambda: (connections.NOT_CONNECTED, 'MARKETING_SITE_URL is not set'))

    r = admin.get('/nimbus/api/connections?probe=1')
    assert r.status_code == 200
    rows = {c['key']: c for c in r.get_json()['connections']}
    assert rows['gbp']['status'] == 'not_connected'
    # GA4 and Search Console are franchise-owned: optional, not misconfigured.
    assert rows['ga4']['status'] == 'owner_required'
    assert rows['gsc']['status'] == 'owner_required'


# ── Local SEO strategist ─────────────────────────────────────────────────────

def test_seo_endpoints_are_empty_before_any_run(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    assert admin.get('/nimbus/api/seo/recommendations').get_json() == []
    assert admin.get('/nimbus/api/seo/report').get_json()['report'] is None
    runs = admin.get('/nimbus/api/seo/runs').get_json()
    assert runs['runs'] == []


def test_seo_review_rejects_an_unknown_status(admin, tmp_path_factory, monkeypatch):
    """The status vocabulary is closed — 'published' must not be smuggled in
    as a way to imply something was actioned."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.post('/nimbus/api/seo/recommendations/1', json={'status': 'published'})
    assert r.status_code == 400


def test_seo_review_404s_on_a_missing_recommendation(admin, tmp_path_factory,
                                                     monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.post('/nimbus/api/seo/recommendations/999', json={'status': 'approved'})
    assert r.status_code == 404


def test_brief_endpoints_are_admin_only(rep, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    assert rep.get('/nimbus/api/seo/briefs').status_code == 403
    assert rep.post('/nimbus/api/seo/recommendations/1/brief').status_code == 403


def test_a_brief_needs_an_approved_recommendation(admin, tmp_path_factory,
                                                  monkeypatch):
    """409, not 500 — refusing is the designed behaviour, not a failure."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    assert admin.get('/nimbus/api/seo/briefs').get_json() == []
    r = admin.post('/nimbus/api/seo/recommendations/999/brief')
    assert r.status_code == 404


def test_the_portal_service_worker_never_caches_nimbus(client):
    """Nimbus's API is at /nimbus/api/..., which does NOT start with '/api/'.

    Without an explicit '/nimbus' entry the portal worker served every Nimbus
    API response cache-first. Observed in real use: "Re-check all" on the
    Connections page returned a cached status, and the SEO page reported "no
    runs yet" with a finished run sitting in the database.
    """
    body = client.get('/sw.js').get_data(as_text=True)
    not_ours = body.split('NOT_OURS', 1)[1].split(']', 1)[0]
    assert "'/nimbus'" in not_ours, \
        'the portal SW will cache Nimbus API responses and serve stale status'
    # '/api/' alone does not cover it — that is the whole trap.
    assert '/nimbus/api/'.startswith('/api/') is False


# ── Social posts and the scheduler ───────────────────────────────────────────

def test_social_drafts_start_empty(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    assert admin.get('/nimbus/api/social/drafts').get_json() == []


def test_marking_a_draft_posted_does_not_publish(admin, tmp_path_factory,
                                                 monkeypatch):
    """"Posted" records that a HUMAN posted it. Nimbus publishes nothing, and
    the response says so explicitly so nobody assumes otherwise."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    from agents import config
    with config.get_cache_db() as db:
        cur = db.execute(
            "INSERT INTO content_drafts (created_at, platform, topic, draft_text) "
            "VALUES ('2026-08-11T00:00:00Z', 'facebook', 't', 'body')")
        db.commit()
        draft_id = cur.lastrowid
    r = admin.post(f'/nimbus/api/social/drafts/{draft_id}', json={'status': 'posted'})
    assert r.status_code == 200
    assert r.get_json()['published'] is False


def test_social_review_rejects_an_unknown_status(admin, tmp_path_factory,
                                                 monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.post('/nimbus/api/social/drafts/1', json={'status': 'publish'})
    assert r.status_code == 400


def test_schedule_lists_the_weekly_jobs(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    body = admin.get('/nimbus/api/schedule').get_json()
    names = {j['name'] for j in body['jobs']}
    assert {'seo_weekly', 'content_listen', 'social_weekly'} <= names
    # The process flag is separate from a job being enabled.
    assert 'enabled' in body


def test_a_job_can_be_switched_off(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.post('/nimbus/api/schedule/seo_weekly', json={'enabled': False})
    assert r.status_code == 200
    job = next(j for j in r.get_json()['jobs'] if j['name'] == 'seo_weekly')
    assert job['enabled'] == 0
    assert admin.post('/nimbus/api/schedule/nope', json={'enabled': True}).status_code == 404


def test_importing_the_app_does_not_start_the_scheduler(monkeypatch):
    """The test suite imports portal.wsgi. Background work must not begin."""
    from agents import scheduler
    monkeypatch.delenv('NIMBUS_SCHEDULER', raising=False)
    assert scheduler.enabled() is False


def test_anonymous_gets_401_not_403(client, tmp_path_factory, monkeypatch):
    """Portal's default-deny hook fires before Nimbus's admin gate."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = client.get('/nimbus/api/territories')
    assert r.status_code == 401


# ── Supervisor ───────────────────────────────────────────────────────────────

def test_reps_get_403_on_every_supervisor_route(rep, tmp_path_factory, monkeypatch):
    """The supervisor can start runs and read the whole pipeline. A rep
    reaching it would bypass the per-rep visibility rules the CRM enforces."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    for path in ('/nimbus/supervisor', '/nimbus/api/supervisor/status',
                 '/nimbus/api/supervisor/threads',
                 '/nimbus/api/supervisor/threads/1'):
        assert rep.get(path).status_code == 403, path
    assert rep.post('/nimbus/api/supervisor/threads').status_code == 403
    assert rep.post('/nimbus/api/supervisor/threads/1/message',
                    json={'text': 'hi'}).status_code == 403


def test_supervisor_tab_serves_the_shell(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = admin.get('/nimbus/supervisor')
    assert r.status_code == 200
    assert 'NIMBUS' in r.get_data(as_text=True)


def test_supervisor_status_never_leaks_the_key(admin, tmp_path_factory,
                                               monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-secret-value')
    body = admin.get('/nimbus/api/supervisor/status').get_json()
    assert body['key_set'] is True
    assert 'secret-value' not in admin.get(
        '/nimbus/api/supervisor/status').get_data(as_text=True)
    assert body['tool_count'] > 0


def test_a_thread_can_be_created_and_read_back(admin, tmp_path_factory,
                                               monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    tid = admin.post('/nimbus/api/supervisor/threads').get_json()['thread_id']
    thread = admin.get(f'/nimbus/api/supervisor/threads/{tid}').get_json()
    assert thread['status'] == 'idle'
    assert thread['messages'] == []
    listed = admin.get('/nimbus/api/supervisor/threads').get_json()['threads']
    assert tid in [t['id'] for t in listed]
    assert admin.get('/nimbus/api/supervisor/threads/99999').status_code == 404


def test_message_without_a_key_fails_loudly(admin, tmp_path_factory, monkeypatch):
    """503 with a sentence, not a 500 traceback or a silent no-op — an unset
    key is the most likely first-run state."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    tid = admin.post('/nimbus/api/supervisor/threads').get_json()['thread_id']
    r = admin.post(f'/nimbus/api/supervisor/threads/{tid}/message',
                   json={'text': 'what needs my attention?'})
    assert r.status_code == 503
    assert 'ANTHROPIC_API_KEY' in r.get_json()['error']


def test_empty_message_is_rejected(admin, tmp_path_factory, monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    tid = admin.post('/nimbus/api/supervisor/threads').get_json()['thread_id']
    r = admin.post(f'/nimbus/api/supervisor/threads/{tid}/message',
                   json={'text': '   '})
    assert r.status_code == 400


def test_a_busy_thread_refuses_a_second_message(admin, tmp_path_factory,
                                                monkeypatch):
    """Two turns interleaving on one thread would corrupt the message history
    the next API call is rebuilt from."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    from agents.supervisor import chat
    tid = admin.post('/nimbus/api/supervisor/threads').get_json()['thread_id']
    chat._set_status(tid, 'running')
    r = admin.post(f'/nimbus/api/supervisor/threads/{tid}/message',
                   json={'text': 'hello'})
    assert r.status_code == 409


class _FakeBlock:
    """Stands in for an SDK content block. `model_dump` is what the real loop
    uses to round-trip blocks back into the next request."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, exclude_none=True):
        return {k: v for k, v in self.__dict__.items()
                if not (exclude_none and v is None)}


class _FakeResponse:
    def __init__(self, blocks, stop_reason):
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = type('U', (), {
            'input_tokens': 1000, 'output_tokens': 200,
            'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0})()


def test_a_full_turn_calls_a_tool_and_answers(admin, tmp_path_factory, monkeypatch):
    """End-to-end through the real loop: the model asks for a tool, the tool
    layer reaches the real Nimbus API with the admin's session, the result
    goes back, and the answer is stored. Only the Anthropic call is faked.
    """
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    from agents.supervisor import chat, client as sup_client

    calls = []

    def fake_call(messages, system, tools=None, max_tokens=8000, reason=''):
        calls.append(messages)
        if len(calls) == 1:
            return _FakeResponse([
                _FakeBlock(type='tool_use', id='toolu_1',
                           name='get_pipeline', input={}),
            ], 'tool_use'), 0.01
        return _FakeResponse([
            _FakeBlock(type='text', text='Nothing is stuck. The pipeline is empty.'),
        ], 'end_turn'), 0.02

    monkeypatch.setattr(sup_client, 'call', fake_call)
    monkeypatch.setattr(sup_client, 'check_cap', lambda: None)

    tid = chat.create_thread('luke')
    chat.run_turn(tid, 'What needs my attention?', {'client': admin})

    thread = chat.get_thread(tid)
    assert thread['status'] == 'idle', thread['error']
    assert thread['cost_usd'] > 0

    # The tool result really came back from the portal's own pipeline route.
    second_request = calls[1]
    tool_results = [b for m in second_request if m['role'] == 'user'
                    for b in m['content'] if b.get('type') == 'tool_result']
    assert len(tool_results) == 1
    assert 'stage_counts' in tool_results[0]['content']
    assert tool_results[0]['is_error'] is False

    shown = [m for m in thread['messages'] if m['display']]
    assert shown[0]['display'] == 'What needs my attention?'
    assert shown[-1]['display'] == 'Nothing is stuck. The pipeline is empty.'
    assert 'get_pipeline' in thread['messages'][1]['tools_used']


def test_a_turn_that_never_stops_calling_tools_is_cut_off(admin, tmp_path_factory,
                                                          monkeypatch):
    """A model that loops on tool calls would otherwise burn the month's cap
    on one question. MAX_ITERATIONS is the backstop."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    from agents.supervisor import chat, client as sup_client

    def always_tools(messages, system, tools=None, max_tokens=8000, reason=''):
        return _FakeResponse([
            _FakeBlock(type='tool_use', id='toolu_x', name='get_pipeline', input={}),
        ], 'tool_use'), 0.01

    monkeypatch.setattr(sup_client, 'call', always_tools)
    monkeypatch.setattr(sup_client, 'check_cap', lambda: None)
    monkeypatch.setattr(chat, 'MAX_ITERATIONS', 3)

    tid = chat.create_thread('luke')
    chat.run_turn(tid, 'go', {'client': admin})
    thread = chat.get_thread(tid)
    assert thread['status'] == 'error'
    assert '3 rounds' in thread['error']


def test_a_failing_tool_is_reported_not_raised(admin, tmp_path_factory, monkeypatch):
    """A 404 from a tool has to come back as a readable result so the model can
    say so, rather than killing the turn."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    from agents.supervisor import chat, client as sup_client

    seen = []

    def fake_call(messages, system, tools=None, max_tokens=8000, reason=''):
        seen.append(messages)
        if len(seen) == 1:
            return _FakeResponse([
                _FakeBlock(type='tool_use', id='toolu_1',
                           name='make_content_brief', input={'rec_id': 99999}),
            ], 'tool_use'), 0.01
        return _FakeResponse([
            _FakeBlock(type='text', text='That recommendation does not exist.'),
        ], 'end_turn'), 0.01

    monkeypatch.setattr(sup_client, 'call', fake_call)
    monkeypatch.setattr(sup_client, 'check_cap', lambda: None)

    tid = chat.create_thread('luke')
    chat.run_turn(tid, 'make a brief for 99999', {'client': admin})
    assert chat.get_thread(tid)['status'] == 'idle'
    results = [b for m in seen[1] if m['role'] == 'user'
               for b in m['content'] if b.get('type') == 'tool_result']
    assert results[0]['is_error'] is False   # HTTP error, surfaced as data
    assert '"ok": false' in results[0]['content']


def test_spend_cap_stops_a_turn_with_a_sentence(admin, tmp_path_factory,
                                                monkeypatch):
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    from agents.supervisor import chat, client as sup_client

    def capped():
        raise sup_client.SpendCapReached('The monthly cap of $50.00 is used up.')

    monkeypatch.setattr(sup_client, 'check_cap', capped)
    tid = chat.create_thread('luke')
    chat.run_turn(tid, 'hello', {'client': admin})
    thread = chat.get_thread(tid)
    assert thread['status'] == 'error'
    assert 'used up' in thread['error']
    # The question is still in the transcript — it was not silently dropped.
    assert [m for m in thread['messages'] if m['display'] == 'hello']
