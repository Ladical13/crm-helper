"""Marketing connection invariants.

The three that matter, in order: no secret ever reaches a response, every
data call is a read, and a broken connector degrades to a red row instead of
taking the page down.

No test here touches the network — probes are monkeypatched or the env is
left empty so the code short-circuits before any request.
"""
import os
import re

import pytest


# ── Secrets never leave the server ───────────────────────────────────────────

_SECRET_VALUES = {
    'GOOGLE_SERVICE_ACCOUNT_JSON_B64': 'eyJjbGllbnRfZW1haWwiOiAibm9wZSJ9',
    'GBP_OAUTH_CLIENT_ID':     'super-secret-client-id.apps.googleusercontent.com',
    'GBP_OAUTH_CLIENT_SECRET': 'GOCSPX-thismustneverappear',
    'GBP_OAUTH_REFRESH_TOKEN': '1//refresh-token-must-never-appear',
}


def test_status_never_contains_a_secret_value(monkeypatch):
    """Set every secret to a known sentinel, then assert none appears anywhere
    in the serialized status — the page renders this straight into HTML."""
    import json
    from agents import connections
    for k, v in _SECRET_VALUES.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv('GA4_PROPERTY_ID', '123456789')
    monkeypatch.setenv('GSC_SITE_URL', 'sc-domain:example.com')
    monkeypatch.setenv('GBP_ACCOUNT_ID', '1069')

    blob = json.dumps(connections.status_all(probe=False))
    for name, value in _SECRET_VALUES.items():
        assert value not in blob, f'{name} value leaked into the status payload'
        # The NAME is expected and useful; only the value must be absent.
    assert 'GBP_OAUTH_CLIENT_SECRET' in blob


def test_status_reports_env_var_names_and_set_flags_not_values(monkeypatch):
    from agents import connections
    monkeypatch.setenv('GBP_OAUTH_CLIENT_SECRET', 'GOCSPX-nope')
    rows = {r['key']: r for r in connections.status_all(probe=False)}
    secrets = rows['gbp']['secret_env']
    entry = next(s for s in secrets if s['name'] == 'GBP_OAUTH_CLIENT_SECRET')
    assert entry['set'] is True
    assert set(entry) == {'name', 'set'}, 'secret entries must carry no value field'


def test_non_secret_ids_are_echoed_because_they_are_not_secret(monkeypatch):
    """A GA4 property ID is an identifier, not a credential. Showing it is how
    an admin confirms they pasted the right one."""
    from agents import connections
    monkeypatch.setenv('GA4_PROPERTY_ID', '123456789')
    rows = {r['key']: r for r in connections.status_all(probe=False)}
    ga4_id = next(i for i in rows['ga4']['id_env'] if i['name'] == 'GA4_PROPERTY_ID')
    assert ga4_id['value'] == '123456789'


# ── Read-only ────────────────────────────────────────────────────────────────

def test_connections_module_makes_no_write_calls():
    """Greps the module. The only permitted POST is the OAuth token exchange,
    which mints a credential and touches no marketing data."""
    from agents import connections
    src = open(connections.__file__, encoding='utf-8').read()
    for verb in ('requests.put', 'requests.patch', 'requests.delete'):
        assert verb not in src, f'{verb} in a read-only module'
    posts = re.findall(r'requests\.post\([^)]*', src)
    assert len(posts) == 1, f'expected exactly one POST (token exchange), found {len(posts)}'
    assert '_OAUTH_TOKEN_URL' in posts[0], 'the only POST must be the token exchange'


def test_every_probe_url_is_a_google_read_endpoint():
    from agents import connections
    src = open(connections.__file__, encoding='utf-8').read()
    # No Business Profile write surfaces — these are the endpoints that would
    # publish a post or reply to a review.
    for banned in ('localPosts', 'mybusiness.googleapis.com/v4/.*reviews.*reply',
                   ':batchUpdate', ':write'):
        assert not re.search(banned, src), f'write-capable endpoint referenced: {banned}'


def test_readonly_scopes_are_declared_correctly():
    """GA4 and GSC use published read-only scopes. GBP has none, and must be
    flagged so nobody assumes Google is enforcing it."""
    from agents import connections
    by_key = {c['key']: c for c in connections.CONNECTORS}
    assert by_key['ga4']['scopes'] == [connections.GA4_SCOPE]
    assert by_key['gsc']['scopes'] == [connections.GSC_SCOPE]
    assert by_key['ga4']['scope_is_readonly'] is True
    assert by_key['gsc']['scope_is_readonly'] is True
    assert all(s.endswith('.readonly') for s in
               by_key['ga4']['scopes'] + by_key['gsc']['scopes'])
    # GBP: no read-only scope exists, so this must be declared false AND carry
    # a caveat, or the UI silently implies Google is enforcing read-only.
    assert by_key['gbp']['scope_is_readonly'] is False
    assert by_key['gbp']['caveat']


# ── Degradation ──────────────────────────────────────────────────────────────

def test_unconfigured_connectors_report_not_connected_with_the_missing_names(monkeypatch):
    from agents import connections
    for k in list(_SECRET_VALUES) + ['GA4_PROPERTY_ID', 'GSC_SITE_URL',
                                     'GBP_ACCOUNT_ID', 'MARKETING_SITE_URL']:
        monkeypatch.delenv(k, raising=False)
    rows = {r['key']: r for r in connections.status_all(probe=False)}
    assert rows['ga4']['status'] == connections.NOT_CONNECTED
    assert 'GA4_PROPERTY_ID' in rows['ga4']['detail']
    assert 'GOOGLE_SERVICE_ACCOUNT_JSON_B64' in rows['ga4']['detail']


def test_configured_but_unprobed_is_not_reported_as_connected(monkeypatch):
    """A set env var is not evidence Google will accept it."""
    from agents import connections
    monkeypatch.setenv('GBP_OAUTH_CLIENT_ID', 'x')
    monkeypatch.setenv('GBP_OAUTH_CLIENT_SECRET', 'x')
    monkeypatch.setenv('GBP_OAUTH_REFRESH_TOKEN', 'x')
    monkeypatch.setenv('GBP_ACCOUNT_ID', '1069')
    rows = {r['key']: r for r in connections.status_all(probe=False)}
    assert rows['gbp']['status'] == connections.UNCHECKED
    assert rows['gbp']['status'] != connections.CONNECTED


def test_a_raising_probe_becomes_an_error_row_not_an_exception(monkeypatch):
    from agents import connections
    monkeypatch.setenv('GA4_PROPERTY_ID', '123')
    monkeypatch.setenv('GOOGLE_SERVICE_ACCOUNT_JSON_B64',
                       'eyJjbGllbnRfZW1haWwiOiJhQGIuY29tIn0=')

    def boom():
        raise Exception('network on fire')
    monkeypatch.setitem(connections._PROBES, 'ga4', boom)

    rows = {r['key']: r for r in connections.status_all(probe=True)}
    assert rows['ga4']['status'] == connections.ERROR
    assert 'network on fire' in rows['ga4']['detail']


def test_malformed_service_account_reports_a_precise_error(monkeypatch):
    from agents import connections
    monkeypatch.setenv('GOOGLE_SERVICE_ACCOUNT_JSON_B64', 'not-base64!!!')
    with pytest.raises(ValueError, match='not valid base64'):
        connections._service_account_info()
    # And the email helper swallows it rather than exploding the page header.
    assert connections.service_account_email() == ''


def test_service_account_email_is_exposed_but_the_key_is_not(monkeypatch):
    """The client_email is the address an admin pastes into Google's sharing
    dialog — useless to an attacker, essential to setup."""
    import base64, json
    from agents import connections
    # A sentinel that cannot collide with an env var name like
    # GBP_OAUTH_CLIENT_SECRET, which legitimately appears in the payload.
    sentinel = 'zzPRIVATEKEYMATERIALzz'
    info = {'client_email': 'nimbus-reader@p1.iam.gserviceaccount.com',
            'private_key': f'-----BEGIN PRIVATE KEY-----{sentinel}-----'}
    monkeypatch.setenv('GOOGLE_SERVICE_ACCOUNT_JSON_B64',
                       base64.b64encode(json.dumps(info).encode()).decode())
    assert connections.service_account_email() == 'nimbus-reader@p1.iam.gserviceaccount.com'
    assert sentinel not in json.dumps(connections.summary())
    assert sentinel not in json.dumps(connections.status_all(probe=False))


def test_undefined_connector_is_surfaced_rather_than_hidden():
    """The Colorado marketing data source has not been specified. It shows as
    an open item so it doesn't get quietly dropped."""
    from agents import connections
    rows = {r['key']: r for r in connections.status_all(probe=False)}
    assert 'co_marketing_data' in rows
    assert rows['co_marketing_data']['status'] == connections.NOT_CONNECTED
