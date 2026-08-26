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
    assert res['profile']['adopted_code'] == '2021 IRC'
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
    assert res['profile']['adopted_code'] == '2018 IRC'


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
    assert res['profile']['adopted_code'] == '2018 IRC'
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
    assert vp['adopted_code'] == '2021 IRC'
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


# ── The allowlist trusts each jurisdiction's own domain ──────────────────
#
# The original allowlist assumed a Colorado municipality publishes on `.gov`.
# Measured against the 273 cities in jurisdictions.json the split is .org 90,
# .gov 84, .com 74, .us 18 — so a bare `.gov` rule threw out 69% of cities'
# OWN official sites. Aurora's real building-code page on auroragov.org was
# rejected as untrustworthy, which failed the whole verify.

def test_a_city_on_a_dot_org_domain_is_authoritative_for_its_own_code():
    aurora = {'id': 'aurora', 'name': 'City of Aurora',
              'url': 'https://www.auroragov.org/'}
    hosts = jp.jurisdiction_hosts(aurora)
    assert 'auroragov.org' in hosts
    real_page = ('https://www.auroragov.org/business_services/building_division'
                 '/adopted_building_codes')
    assert not jp.citation_is_allowed(real_page), 'precondition: fails the static list'
    assert jp.citation_is_allowed(real_page, hosts)


def test_widening_the_allowlist_does_not_admit_contractor_blogs():
    """The fix trusts ONE extra domain per jurisdiction, not `.org` at large."""
    hosts = jp.jurisdiction_hosts({'url': 'https://www.auroragov.org/'})
    for junk in ('https://hailreadycolorado.com/colorado-roofing-codes-by-city/',
                 'https://roofsbycooper.com/building-codes/',
                 'https://someroofer.org/aurora-codes'):
        assert not jp.citation_is_allowed(junk, hosts), junk


def test_a_lookalike_domain_does_not_satisfy_the_jurisdiction_host():
    """Suffix matching must happen at a label boundary, or 'auroragov.org'
    would be satisfied by 'notauroragov.org'."""
    hosts = jp.jurisdiction_hosts({'url': 'https://www.auroragov.org/'})
    assert not jp.citation_is_allowed('https://notauroragov.org/codes', hosts)
    # A genuine subdomain still counts.
    assert jp.citation_is_allowed('https://permits.auroragov.org/x', hosts)


def test_jurisdiction_hosts_looks_through_a_wayback_snapshot():
    """30 entries list a 2020 Wayback capture as the town's official site.
    web.archive.org says nothing about who the town is; the archived URL does."""
    j = {'url': 'https://web.archive.org/web/20200522064708/'
                'https://townofbayfield.colorado.gov/'}
    assert jp.jurisdiction_hosts(j) == ['townofbayfield.colorado.gov']


def test_jurisdiction_hosts_refuses_a_bare_public_suffix():
    """A malformed url must not turn into a wildcard that trusts half the
    internet."""
    for bad in ('http://co.us/', 'https://org/', 'https://com/', ''):
        assert jp.jurisdiction_hosts({'url': bad}) == [], bad


def test_a_curated_code_url_also_counts_as_an_authoritative_host():
    """Both recorded URLs contribute a host. Only a leading `www.` is dropped:
    a `code_url` on codes.lovgov.net trusts that host and its subdomains, not
    the whole of lovgov.net, which nobody has vouched for."""
    j = {'url': 'https://www.lovgov.org/', 'code_url': 'https://codes.lovgov.net/ch5'}
    hosts = jp.jurisdiction_hosts(j)
    assert hosts == ['lovgov.org', 'codes.lovgov.net']
    assert jp.citation_is_allowed('https://codes.lovgov.net/ch5/905', hosts)
    assert not jp.citation_is_allowed('https://lovgov.net/unrelated', hosts)


# ── Delegating jurisdictions ─────────────────────────────────────────────

def _pplx(data, citations, cached=False):
    return {'answer': '', 'data': data, 'citations': citations,
            'cost_usd': 0.0 if cached else 0.001, 'cached': cached,
            'model': 'sonar-pro'}


_DELEGATED = {
    'adopted_code': 'unknown', 'adopted_code_source_url': 'unknown',
    'amendments': [],
    'reroof_permit': {'submittal_method': 'unknown',
                      'portal_url': 'unknown', 'fee_basis': 'unknown'},
    'issues_permits_for_roofing': True,
    'delegated_to': 'Pikes Peak Regional Building Department',
}


def test_a_jurisdiction_that_delegates_permits_verifies_instead_of_failing(A):
    """Colorado Springs sets no code of its own — it contracts to the Pikes
    Peak Regional Building Department. That answer is correct and is exactly
    what the office needs, but the old rule failed on adopted_code=='unknown'
    and threw it away."""
    j = {'id': 'colorado-springs', 'name': 'City of Colorado Springs',
         'kind': 'city', 'url': 'https://coloradosprings.gov/'}
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response('<html></html>')), \
         patch('agents.perplexity.search_json',
               return_value=_pplx(_DELEGATED, ['https://coloradosprings.gov/building'])):
        res = A._verify_jurisdiction_profile(j)
    assert res['ok'] is True
    assert res['profile']['delegated_to'] == 'Pikes Peak Regional Building Department'
    assert res['profile']['adopted_code'] == ''


def test_unknown_code_with_no_delegation_still_fails_closed(A):
    """The delegation carve-out must not become a general escape hatch: with
    nothing to say, we still save nothing."""
    nothing = dict(_DELEGATED, delegated_to=None)
    j = {'id': 'x', 'name': 'Test', 'kind': 'city', 'url': 'https://t.example.gov'}
    with patch.object(A.http, 'get',
                      return_value=_fake_http_response('<html></html>')), \
         patch('agents.perplexity.search_json',
               return_value=_pplx(nothing, ['https://t.example.gov/b'])):
        res = A._verify_jurisdiction_profile(j)
    assert res['ok'] is False


def test_a_delegated_profile_can_be_approved_and_reaches_the_office(client, A, jx_snapshot):
    """Approve used to require adopted_code, so a delegation-only profile was
    unapprovable — and 'pull this permit somewhere else' never reached the
    packet the office works from."""
    body = {'profile': dict(_DELEGATED, adopted_code=''),
            'citations': ['https://coloradosprings.gov/building'],
            'source': 'perplexity'}
    r = client.post(f'/api/jurisdictions/{TEST_JID}/approve', json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    m = A._build_estimate_manifest(_estimate_in_loveland(A))
    vp = (m.get('code') or {}).get('verified_profile')
    assert vp and vp['delegated_to'] == 'Pikes Peak Regional Building Department'


# ── A cached rejection must not be a permanent one ───────────────────────

def test_a_cached_bad_answer_is_retried_once_for_real(A):
    """The 30-day cache stores the model's ANSWER, but these rejections are
    decided downstream of it — so 'Re-verify' replayed the same cached answer
    into the same error for 30 days. A cached rejection now spends one call on
    a genuine retry."""
    j = {'id': 'x', 'name': 'Test', 'kind': 'city', 'url': 'https://t.example.gov'}
    good = dict(_DELEGATED, adopted_code='IRC 2021', delegated_to=None)
    calls = []

    def fake(prompt, **kw):
        calls.append(kw.get('force_refresh'))
        if len(calls) == 1:      # the stale cached answer, rejected
            return _pplx(dict(_DELEGATED, delegated_to=None),
                         ['https://t.example.gov/b'], cached=True)
        return _pplx(good, ['https://t.example.gov/b'], cached=False)

    with patch.object(A.http, 'get',
                      return_value=_fake_http_response('<html></html>')), \
         patch('agents.perplexity.search_json', side_effect=fake):
        res = A._verify_jurisdiction_profile(j)
    assert calls == [False, True], 'the retry must bypass the cache'
    assert res['ok'] is True and res['retried'] is True
    assert res['profile']['adopted_code'] == '2021 IRC'


def test_a_fresh_bad_answer_is_not_retried(A):
    """A live call that just failed will not do better a millisecond later —
    retrying it only doubles the spend."""
    j = {'id': 'x', 'name': 'Test', 'kind': 'city', 'url': 'https://t.example.gov'}
    calls = []

    def fake(prompt, **kw):
        calls.append(kw.get('force_refresh'))
        return _pplx(dict(_DELEGATED, delegated_to=None),
                     ['https://t.example.gov/b'], cached=False)

    with patch.object(A.http, 'get',
                      return_value=_fake_http_response('<html></html>')), \
         patch('agents.perplexity.search_json', side_effect=fake):
        res = A._verify_jurisdiction_profile(j)
    assert calls == [False], 'a fresh failure must cost exactly one call'
    assert res['ok'] is False


# ── adopted_code is tidied, never rewritten ──────────────────────────────

def test_adopted_code_canonicalises_the_two_ways_of_writing_one_year(A):
    for raw in ('IRC 2021', '2021 IRC', 'irc  2021', 'IRC, 2021',
                'International Residential Code, 2021 Edition'):
        assert A._jx_normalize_code(raw) == '2021 IRC', raw


def test_adopted_code_leaves_a_code_that_is_not_the_irc_alone(A):
    """El Paso County really is on the Pikes Peak Regional Building Code.
    Flattening that to an IRC year would state something false."""
    for raw in ('Pikes Peak Regional Building Code 2023',
                '2024 I-Codes',
                '2024 International Codes (local amendments)'):
        assert A._jx_normalize_code(raw) == raw, raw


def test_adopted_code_keeps_a_multi_clause_answer_whole(A):
    """Larimer County's answer names the wildfire code AND the IRC year that
    governs roofing. Truncating to the first clause would drop the half that
    matters for a re-roof."""
    raw = ('2025 Colorado Wildfire Resiliency Code; 2021 IRC amendments in '
           'effect for residential roofing')
    assert A._jx_normalize_code(raw) == raw


def test_adopted_code_is_capped_so_one_runaway_sentence_cannot_break_the_pdf(A):
    # 160 characters plus the ellipsis that marks the cut.
    assert len(A._jx_normalize_code('2021 IRC ' + 'x' * 500)) <= 161


# ── The direct tier no longer guesses publisher URLs ─────────────────────

def test_direct_fetch_only_tries_urls_that_are_about_this_jurisdiction(A):
    """The Municode/amlegal slug guesses hit 0 times across a 16-jurisdiction
    sample and cost a 10s timeout each: Municode serves a JS app shell with no
    code year in the HTML, and amlegal 403s us. Only the two URLs actually
    recorded for this jurisdiction are tried now."""
    j = {'id': 'fort-collins', 'name': 'City of Fort Collins', 'kind': 'city',
         'url': 'https://www.fcgov.com/', 'code_url': 'https://fcgov.com/building/codes'}
    urls = A._jx_direct_urls(j)
    assert urls == ['https://fcgov.com/building/codes', 'https://www.fcgov.com/']
    for u in urls:
        assert 'municode.com' not in u and 'amlegal.com' not in u


def test_direct_fetch_unwraps_a_wayback_url_before_fetching_it(A):
    """Fetching web.archive.org returns the 2020 copy of a site — not what we
    want to quote a customer as current code."""
    j = {'id': 'x', 'name': 'Town of Bayfield', 'kind': 'city',
         'url': 'https://web.archive.org/web/20200522064708/https://tob.colorado.gov/'}
    assert A._jx_direct_urls(j) == ['https://tob.colorado.gov/']


def test_a_county_gets_its_colorado_locality_domain():
    """All 64 counties in jurisdictions.json have no `url`, so they had no
    domain of their own — Douglas County failed while Perplexity was citing
    apps.douglas.co.us."""
    hosts = jp.jurisdiction_hosts({'name': 'Douglas County', 'kind': 'county'})
    assert hosts == ['douglas.co.us']
    assert jp.citation_is_allowed(
        'https://apps.douglas.co.us/building/services/Default.aspx', hosts)
    # And it is still not a licence to cite anybody.
    assert not jp.citation_is_allowed(
        'https://upstreamroof.com/blog/douglas-county-roof', hosts)


def test_the_locality_guess_is_name_matched_not_a_wildcard():
    """`<name>.co.us` must belong to the jurisdiction being verified — Weld
    County must not accept a citation on douglas.co.us."""
    weld = jp.jurisdiction_hosts({'name': 'Weld County', 'kind': 'county'})
    assert not jp.citation_is_allowed('https://apps.douglas.co.us/x', weld)
    assert jp.citation_is_allowed('https://weld.co.us/building', weld)


def test_adopted_code_truncation_lands_on_a_word_boundary(A):
    """A hard slice ended one real answer mid-word at '...as part of t',
    which reads as corruption on a customer's estimate."""
    long = ('2024 International Residential Code (for one- and two-family '
            'dwellings and townhouses) and 2024 International Building Code '
            '(for other structures), as part of the 2024 I-Codes')
    out = A._jx_normalize_code(long)
    assert len(out) <= 161            # 160 + the ellipsis
    assert out.endswith('…')
    assert not out[:-1].endswith(' ')
    assert long.startswith(out[:-1])  # a prefix, never reworded


def test_the_prompt_asks_for_one_short_code_not_an_essay():
    """sonar-pro answered the looser prompt with 160-character sentences
    naming the IBC, effective dates and transition plans — all of which land
    on the 'Enforces' line of a customer's estimate."""
    text = jp.build_prompt({'name': 'City of Greeley', 'kind': 'city',
                            'county': 'Weld', 'url': 'https://greeleygov.com/'})
    assert 'RESIDENTIAL RE-ROOF' in text
    assert 'Name ONE code' in text
    assert 'City of Greeley' in text


# ── County URLs reach a volume that already has jurisdictions.json ───────

def test_service_area_counties_have_a_domain_of_their_own(A):
    """El Paso County is elpasoco.com — not .gov and not elpaso.co.us, so
    neither the static list nor the locality guess reaches it. Without a `url`
    the county had no authoritative host at all and verify failed."""
    data = A._load_jurisdictions()
    by_id = {j['id']: j for j in data['jurisdictions']}
    el_paso = by_id['el-paso-county']
    assert el_paso['url'] == 'https://elpasoco.com/'
    hosts = jp.jurisdiction_hosts(el_paso)
    assert jp.citation_is_allowed('https://elpasoco.com/regional-building-department', hosts)


def test_county_urls_are_backfilled_on_read_not_only_in_the_seed(A):
    """_seed_data_dir() copies jurisdictions.json to the volume only when it
    is ABSENT, so on a long-lived volume the repo copy is inert. A seed-only
    edit would never reach production — the backfill has to run on read."""
    stale = {'jurisdictions': [
        {'id': 'el-paso-county', 'name': 'El Paso County', 'kind': 'county', 'url': ''},
    ]}
    out = A._jx_backfill_urls(stale)
    assert out['jurisdictions'][0]['url'] == 'https://elpasoco.com/'


def test_backfill_never_overwrites_a_manager_edited_url(A):
    """A manager who points a county at its building-department page in
    Settings must not have it reverted to the seed on the next read."""
    edited = {'jurisdictions': [
        {'id': 'weld-county', 'name': 'Weld County', 'kind': 'county',
         'url': 'https://www.weld.gov/Government/Departments/Building-Department'},
    ]}
    out = A._jx_backfill_urls(edited)
    assert out['jurisdictions'][0]['url'].endswith('/Building-Department')


def test_backfill_leaves_jurisdictions_it_has_no_seed_for_alone(A):
    untouched = {'jurisdictions': [
        {'id': 'gilpin-county', 'name': 'Gilpin County', 'kind': 'county', 'url': ''},
    ]}
    out = A._jx_backfill_urls(untouched)
    assert out['jurisdictions'][0]['url'] == ''
