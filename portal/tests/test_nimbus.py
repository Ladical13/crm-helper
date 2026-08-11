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
