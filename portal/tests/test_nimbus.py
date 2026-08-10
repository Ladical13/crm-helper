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
                 '/nimbus/api/seo/report', '/nimbus/api/seo/result'):
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


def test_anonymous_gets_401_not_403(client, tmp_path_factory, monkeypatch):
    """Portal's default-deny hook fires before Nimbus's admin gate."""
    _fresh_agents_dir(tmp_path_factory, monkeypatch)
    r = client.get('/nimbus/api/territories')
    assert r.status_code == 401
