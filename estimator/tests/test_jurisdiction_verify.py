"""Verified per-jurisdiction code profile — the pipeline that fills the
adopted-IRC-year + local-amendments block on the customer sign page.

The rules under test:
  * Perplexity is called ONLY when direct fetch produces no adopted_code.
  * A Perplexity answer with no allowlisted citation is REJECTED (never
    reaches persistence).
  * The verify/approve/reject routes are manager-only.
  * An approved profile round-trips into the manifest's `code` block; an
    unapproved one (no reviewed_at) does not.
"""
import json
import os
from unittest.mock import patch

import pytest

import app as estimator_app
import jurisdiction_prompts as jp


TEST_JID = 'loveland'   # exists in the shipped jurisdictions.json


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def jx_snapshot():
    """Restore jurisdictions.json after any test that mutates it — approve
    writes to the shared TEST_DATA_DIR copy, and other tests read that same
    file. Snapshot from the seed committed beside the app rather than the
    live file (subsequent tests in the same session would otherwise inherit
    each other's edits)."""
    live_path = estimator_app.JURISDICTIONS_FILE
    original = None
    if os.path.exists(live_path):
        with open(live_path, encoding='utf-8') as f:
            original = f.read()
    else:
        seed = os.path.join(estimator_app.BASE_DIR, 'jurisdictions.json')
        with open(seed, encoding='utf-8') as f:
            original = f.read()
    yield
    with open(live_path, 'w', encoding='utf-8') as f:
        f.write(original)


@pytest.fixture
def rep_client(app):
    """A non-manager rep — for the 403 checks. Seeded via portal.users so
    role_of() returns 'rep' rather than 'admin' (which is what luke gets)."""
    from portal import users as portal_users
    if not portal_users.get('nomgr'):
        portal_users.create('nomgr', password='x', role='rep', full_name='No Manager')
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = 'nomgr'
    return c


# ── jurisdiction_prompts module ──────────────────────────────────────────

def test_prompt_citation_allowlist_accepts_authoritative_hosts():
    for good in [
        'https://library.municode.com/co/loveland/codes/code_of_ordinances',
        'https://codelibrary.amlegal.com/codes/denverco/latest/overview',
        'https://ecode360.com/12345/',
        'https://loveland.co.gov/permits',     # .gov catches state/city domains
        'https://codes.iccsafe.org/x',
    ]:
        assert jp.citation_is_allowed(good), good


def test_prompt_citation_allowlist_rejects_random_hosts():
    for bad in [
        'https://reddit.com/r/roofing',
        'https://someguy.blog/roofcodes',
        'javascript:alert(1)',
        '',
        None,
        'not a url at all',
    ]:
        assert not jp.citation_is_allowed(bad), bad


def test_filter_allowed_citations_preserves_order_and_dedupes():
    urls = ['https://example.com', 'https://library.municode.com/x',
            'https://library.municode.com/x', 'https://a.gov/y']
    assert jp.filter_allowed_citations(urls) == [
        'https://library.municode.com/x', 'https://a.gov/y',
    ]


# ── Direct-fetch tier (adopted_code parsing) ─────────────────────────────

def _fake_http_response(text, ok=True, ct='text/html'):
    class R:
        def __init__(self):
            self.ok = ok
            self.text = text
            self.headers = {'Content-Type': ct}
    return R()


def test_direct_fetch_extracts_adopted_code_from_municode_html(A):
    """Publisher-shaped HTML with '2021 IRC' anywhere in it must yield an
    IRC year + the URL it came from — no Perplexity call needed."""
    j = {'id': 'x', 'name': 'Test City', 'kind': 'city',
         'url': 'https://testcity.example.gov/building',
         'code_url': 'https://library.municode.com/co/testcity'}
    html = ('<html><body><h1>Chapter 5 — Building Code</h1>'
            '<p>The City has adopted the 2021 International Residential Code…</p>'
            '</body></html>')
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response(html)) as m:
        res = A._jx_direct_profile(j)
    assert m.called
    assert res is not None
    assert res['profile']['adopted_code'] == 'IRC 2021'
    assert res['profile']['adopted_code_source_url'] == j['code_url']
    assert res['source'] == 'municode'


def test_direct_fetch_returns_none_when_no_year_found(A):
    j = {'id': 'x', 'name': 'Test', 'kind': 'city',
         'url': 'https://test.example.gov'}
    html = '<html><body>Nothing about codes here.</body></html>'
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response(html)):
        assert A._jx_direct_profile(j) is None


def test_direct_fetch_ignores_non_html_content_types(A):
    j = {'id': 'x', 'name': 'Test', 'kind': 'city',
         'url': 'https://test.example.gov/codes.pdf'}
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response(
                          '2021 International Residential Code',
                          ct='application/pdf')):
        assert A._jx_direct_profile(j) is None


# ── Perplexity fallback wiring ───────────────────────────────────────────

def test_perplexity_only_runs_when_direct_returns_nothing(A):
    """The pipeline must not spend money on Perplexity when the city's own
    page already produced an adopted_code."""
    j = {'id': 'x', 'name': 'Test', 'kind': 'city',
         'url': 'https://t.example.gov'}
    html = '<html>Adopted the International Residential Code, 2018 Edition.</html>'
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response(html)), \
         patch.object(A, '_jx_perplexity_profile') as pplx:
        res = A._verify_jurisdiction_profile(j)
    pplx.assert_not_called()
    assert res['ok'] is True
    assert res['profile']['adopted_code'] == 'IRC 2018'


def test_perplexity_result_rejected_without_allowlisted_citation(A):
    """A Perplexity answer whose only citations are a blog and a forum must
    fail closed — never reaches the manager preview as ok:true."""
    j = {'id': 'x', 'name': 'Test', 'kind': 'city',
         'url': 'https://t.example.gov'}
    fake_pplx_result = {
        'answer': 'ignored',
        'data': {
            'adopted_code': 'IRC 2021',
            'adopted_code_source_url': 'https://blog.example.com/roof',
            'amendments': [],
            'reroof_permit': {'submittal_method': 'unknown',
                              'portal_url': 'unknown', 'fee_basis': 'unknown'},
            'issues_permits_for_roofing': True, 'delegated_to': None,
        },
        'citations': ['https://blog.example.com/roof',
                      'https://forum.example.com/thread'],
        'cost_usd': 0.0, 'cached': False, 'model': 'sonar',
    }
    # Direct fetch produces nothing (empty HTML)
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response('<html></html>')), \
         patch('agents.perplexity.search_json',
               return_value=fake_pplx_result):
        res = A._verify_jurisdiction_profile(j)
    assert res['ok'] is False
    assert 'authoritative source' in res['error']


def test_perplexity_unknown_adopted_code_is_refused(A):
    """When Perplexity returns 'unknown' for adopted_code, we save nothing —
    the customer view sees the baseline instead of a garbage 'Enforces unknown'
    line. Same treatment for placeholder 'unknown' amendments."""
    j = {'id': 'x', 'name': 'Test', 'kind': 'city',
         'url': 'https://t.example.gov'}
    unknown_pplx = {
        'data': {
            'adopted_code': 'unknown', 'adopted_code_source_url': 'unknown',
            'amendments': [{'topic': 'unknown', 'text': 'unknown', 'source_url': 'unknown'}],
            'reroof_permit': {'submittal_method': 'unknown',
                              'portal_url': 'unknown', 'fee_basis': 'unknown'},
            'issues_permits_for_roofing': True, 'delegated_to': None,
        },
        'citations': ['https://library.municode.com/co/x'],
        'cost_usd': 0.0, 'cached': True, 'model': 'sonar',
    }
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response('<html></html>')), \
         patch('agents.perplexity.search_json', return_value=unknown_pplx):
        res = A._verify_jurisdiction_profile(j)
    assert res['ok'] is False
    assert 'adopted code' in res['error'].lower()


def test_perplexity_result_accepted_with_allowlisted_citation(A):
    j = {'id': 'x', 'name': 'Test', 'kind': 'city',
         'url': 'https://t.example.gov'}
    good_pplx = {
        'answer': '',
        'data': {
            'adopted_code': 'IRC 2018',
            'adopted_code_source_url': 'https://library.municode.com/co/testcity',
            'amendments': [
                {'topic': 'ice barrier', 'text': 'Ice barrier required to 36" past warm-wall.',
                 'source_url': 'https://library.municode.com/co/testcity/905'},
            ],
            'reroof_permit': {
                'submittal_method': 'Online via Citizen Portal',
                'portal_url': 'https://testcity.example.gov/portal',
                'fee_basis': 'per-square',
            },
            'issues_permits_for_roofing': True,
            'delegated_to': None,
        },
        'citations': ['https://library.municode.com/co/testcity',
                      'https://testcity.example.gov/building'],
        'cost_usd': 0.001, 'cached': False, 'model': 'sonar',
    }
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response('<html></html>')), \
         patch('agents.perplexity.search_json', return_value=good_pplx):
        res = A._verify_jurisdiction_profile(j)
    assert res['ok'] is True
    assert res['source'] == 'perplexity'
    assert res['profile']['adopted_code'] == 'IRC 2018'
    assert res['profile']['amendments'][0]['topic'] == 'ice barrier'
    # Only the allowlisted citations survive.
    for c in res['citations']:
        assert jp.citation_is_allowed(c)


# ── Auth on the new routes ───────────────────────────────────────────────

def test_verify_route_requires_manager(anon, rep_client, client):
    assert anon.post(f'/api/jurisdictions/{TEST_JID}/verify').status_code == 401
    assert rep_client.post(f'/api/jurisdictions/{TEST_JID}/verify').status_code == 403
    # Manager path: prevent any real Perplexity/http call.
    with patch.object(estimator_app, '_verify_jurisdiction_profile',
                      return_value={'ok': True, 'profile': {'adopted_code': 'IRC 2021'},
                                    'source': 'city-page', 'citations': []}):
        assert client.post(f'/api/jurisdictions/{TEST_JID}/verify').status_code == 200


def test_approve_and_reject_are_manager_only(anon, rep_client):
    body = {'profile': {'adopted_code': 'IRC 2021'}, 'source': 'test',
            'citations': ['https://x.gov']}
    assert anon.post(f'/api/jurisdictions/{TEST_JID}/approve',
                     json=body).status_code == 401
    assert rep_client.post(f'/api/jurisdictions/{TEST_JID}/approve',
                           json=body).status_code == 403
    assert anon.post(f'/api/jurisdictions/{TEST_JID}/reject').status_code == 401
    assert rep_client.post(f'/api/jurisdictions/{TEST_JID}/reject').status_code == 403


def test_approve_rejects_missing_adopted_code(client, jx_snapshot):
    r = client.post(f'/api/jurisdictions/{TEST_JID}/approve',
                    json={'profile': {'adopted_code': ''}, 'citations': []})
    assert r.status_code == 400


# ── End-to-end: approve → manifest exposure ──────────────────────────────

def _estimate_in_loveland(A):
    return {
        'estimate_id': 'e-1-xxxxxxxx',
        'customer': {'name': 'Sig Reader',
                     'address': {'street': '1 Main', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
                               'tier_bundles': {'good': 'b_landmark',
                                                'better': 'b_northgate',
                                                'best':  'b_standing_seam'},
                               'line_items': [
                                   {'name': 'Shingles', 'quantity': 20,
                                    'unit': 'SQ', 'measure': 'squares_waste',
                                    'tiers': {'good':   {'material_unit_cost': 142},
                                              'better': {'material_unit_cost': 175},
                                              'best':   {'material_unit_cost': 400}}},
                               ]}},
        'measurements': {'attic_sqft': 1800},
        'permit_jurisdiction': {'selected_id': TEST_JID, 'auto_id': TEST_JID,
                                'confirmed': True},
    }


def test_approved_profile_appears_in_manifest(client, A, jx_snapshot):
    body = {
        'profile': {
            'adopted_code': 'IRC 2021',
            'adopted_code_source_url': 'https://library.municode.com/co/loveland',
            'amendments': [
                {'topic': 'fasteners', 'text': '6 nails per shingle required.',
                 'source_url': 'https://library.municode.com/co/loveland/9052'},
            ],
            'reroof_permit': {'submittal_method': 'Citizen Access Portal',
                              'portal_url': 'https://lovgov.org/portal',
                              'fee_basis': 'per-square'},
            'issues_permits_for_roofing': True, 'delegated_to': None,
        },
        'source': 'municode',
        'citations': ['https://library.municode.com/co/loveland'],
    }
    r = client.post(f'/api/jurisdictions/{TEST_JID}/approve', json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    m = A._build_estimate_manifest(_estimate_in_loveland(A))
    vp = (m.get('code') or {}).get('verified_profile')
    assert vp, 'approved profile should appear in the manifest code block'
    assert vp['adopted_code'] == 'IRC 2021'
    assert vp['amendments'][0]['topic'] == 'fasteners'
    assert vp['verified_at']
    assert vp['reroof_permit']['portal_url'] == 'https://lovgov.org/portal'


def test_unapproved_profile_hidden_from_manifest(A, jx_snapshot):
    """A verified_profile without reviewed_at is treated as an unreviewed
    preview and must NOT reach the customer manifest."""
    data = A._load_jurisdictions()
    j = A._jx_find_by_id(data, TEST_JID)
    j['verified_profile'] = {
        'adopted_code': 'IRC 2018',
        'amendments': [],
        # No reviewed_at, no reviewed_by — this is a pending preview.
    }
    A._jx_save_atomic(data)
    m = A._build_estimate_manifest(_estimate_in_loveland(A))
    assert (m.get('code') or {}).get('verified_profile') is None


def test_reject_clears_saved_profile(client, A, jx_snapshot):
    # First approve, then reject and confirm it's gone.
    client.post(f'/api/jurisdictions/{TEST_JID}/approve', json={
        'profile': {'adopted_code': 'IRC 2021'},
        'source': 'test', 'citations': ['https://x.gov'],
    })
    data = A._load_jurisdictions()
    assert A._jx_find_by_id(data, TEST_JID).get('verified_profile')
    assert client.post(f'/api/jurisdictions/{TEST_JID}/reject').status_code == 200
    data = A._load_jurisdictions()
    assert 'verified_profile' not in A._jx_find_by_id(data, TEST_JID)
