"""Local SEO strategist invariants.

Nothing here touches the network. `requests` is replaced wholesale in the
crawler and Perplexity is monkeypatched, so a run in CI is deterministic and
free.

The tests that matter most are the honesty ones. A strategist with no owned
data source is one careless sentence away from inventing a ranking, and the
whole design rests on that being impossible rather than merely discouraged.
"""
import json

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────

GOOD_HTML = '''<!doctype html><html><head>
<title>Roof Replacement in Fort Collins | Project One Roofing</title>
<meta name="description" content="Storm damage roof replacement across Northern Colorado, from inspection through the insurance claim and the final walkthrough of your new roof.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://example.com/roofing">
<script type="application/ld+json">{"@type":"RoofingContractor","name":"Project One"}</script>
</head><body>
<h1>Roof Replacement in Fort Collins</h1>
<h2>What to expect</h2>
<p>%s</p>
<a href="/siding">Siding services</a>
<img src="a.jpg" alt="A finished roof">
</body></html>''' % (' word' * 400)

BARE_HTML = '''<!doctype html><html><head></head><body>
<h1>One</h1><h1>Two</h1>
<img src="x.jpg">
<a href="/a">click here</a><a href="/b">read more</a><a href="/c">learn more</a>
<script type="application/ld+json">{ this is not json </script>
</body></html>'''


class FakeResponse:
    def __init__(self, text='', status=200, ctype='text/html'):
        self.text = text
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = {'Content-Type': ctype}

    def json(self):
        return json.loads(self.text)


@pytest.fixture
def fake_web(monkeypatch):
    """Serve a tiny fake site. Records every URL fetched."""
    from agents.seo import crawl

    pages = {
        'https://example.com/robots.txt': FakeResponse('User-agent: *\nDisallow: /private\n',
                                                       ctype='text/plain'),
        'https://example.com/sitemap.xml': FakeResponse(
            '<urlset><url><loc>https://example.com/roofing</loc></url>'
            '<url><loc>https://example.com/bare</loc></url>'
            '<url><loc>https://example.com/private/secret</loc></url></urlset>',
            ctype='application/xml'),
        'https://example.com/roofing': FakeResponse(GOOD_HTML),
        'https://example.com/bare': FakeResponse(BARE_HTML),
    }
    calls = []

    class FakeRequests:
        @staticmethod
        def get(url, **kw):
            calls.append(url)
            if url in pages:
                return pages[url]
            return FakeResponse('', status=404)

    monkeypatch.setattr(crawl, 'requests', FakeRequests)
    monkeypatch.setattr(crawl, 'CRAWL_DELAY', 0)     # keep the suite fast
    return calls


@pytest.fixture
def site_profile(monkeypatch):
    monkeypatch.setenv('MARKETING_SITE_URL', 'https://example.com')


# ── Honesty: the load-bearing rules ─────────────────────────────────────────

def _valid_rec(**over):
    rec = {
        'category': 'improve_existing_page',
        'action': 'Add a meta description to https://example.com/x',
        'rationale': 'The page has none.',
        'evidence': ['https://example.com/x'],
        'confidence': 'high',
        'evidence_basis': 'public_research',
        'review_notes': 'Check it describes this page only.',
    }
    rec.update(over)
    return rec


@pytest.mark.parametrize('claim', [
    'We rank #3 for roof repair in Fort Collins',
    'This keyword gets 2,400 monthly searches',
    'The page receives 1,200 visitors a month',
    'Our competitor outranks us on this term',
    'Search volume for this term is high and rising',
    'This page has a conversion rate of 4%',
    'Their domain authority of 52 beats ours',
    'We sit in position 7 on Google',
])
def test_unmeasurable_claims_are_rejected(claim):
    """Each of these needs a data source we do not have."""
    from agents.seo import honesty
    with pytest.raises(honesty.Rejected):
        honesty.check(_valid_rec(rationale=claim))


def test_a_clean_recommendation_passes_and_gets_labelled():
    from agents.seo import honesty
    out = honesty.check(_valid_rec())
    assert out['label'] == 'Observed on our site'


def test_research_findings_are_labelled_public_research_opportunity():
    from agents.seo import honesty
    out = honesty.check(_valid_rec(
        category='faq_or_content_brief',
        intent='Does insurance cover a hail-damaged roof?',
        evidence=['https://example.org/guide']))
    assert out['label'] == 'Public-research opportunity'


def test_owned_data_basis_is_impossible_in_v1():
    """No owned analytics is connected, so a claim of one is wrong by
    construction — and would license every numeric claim we just banned."""
    from agents.seo import honesty
    with pytest.raises(honesty.Rejected, match='owned_data'):
        honesty.check(_valid_rec(evidence_basis='owned_data'))


def test_a_recommendation_with_no_evidence_is_dropped():
    from agents.seo import honesty
    with pytest.raises(honesty.Rejected, match='no evidence'):
        honesty.check(_valid_rec(evidence=[]))


def test_a_recommendation_with_no_review_requirement_is_dropped():
    """Every item must say what a human has to verify."""
    from agents.seo import honesty
    with pytest.raises(honesty.Rejected, match='human-review'):
        honesty.check(_valid_rec(review_notes=''))


def test_banned_marketing_phrases_apply_here_too():
    from agents.seo import honesty
    with pytest.raises(honesty.Rejected, match='banned phrase'):
        honesty.check(_valid_rec(action='Offer a free roof inspection page'))


def test_services_we_do_not_offer_are_rejected():
    from agents.seo import honesty
    with pytest.raises(honesty.Rejected, match='approved_services'):
        honesty.check(_valid_rec(service='swimming_pools'))


def test_every_required_category_exists():
    from agents.seo import honesty
    for cat in ('improve_existing_page', 'create_service_page',
                'create_city_or_service_area_page', 'faq_or_content_brief',
                'internal_linking_opportunity', 'technical_website_fix',
                'google_business_profile_opportunity'):
        assert cat in honesty.CATEGORIES


def test_filter_all_reports_what_it_dropped():
    """A run that drops half its output must not look like a quiet run."""
    from agents.seo import honesty
    kept, dropped = honesty.filter_all([
        _valid_rec(), _valid_rec(rationale='we rank #1 for everything')])
    assert len(kept) == 1 and len(dropped) == 1
    assert 'ranking' in dropped[0][1]


# ── Crawl safety ─────────────────────────────────────────────────────────────

def test_robots_disallow_is_respected(fake_web, site_profile):
    from agents.seo import crawl
    result = crawl.crawl_site('https://example.com', max_pages=10)
    assert any('/private' in s['url'] for s in result['skipped'])
    assert not any('/private' in u for u in fake_web), \
        'a disallowed URL was fetched anyway'


def test_the_crawler_identifies_itself(fake_web, site_profile):
    from agents.seo import crawl
    assert 'projectoneroofingcolorado.com' in crawl.USER_AGENT
    assert 'ProjectOneNimbus' in crawl.USER_AGENT


def test_crawl_respects_the_page_limit(fake_web, site_profile):
    from agents.seo import crawl
    result = crawl.crawl_site('https://example.com', max_pages=1)
    assert len(result['pages']) == 1


def test_the_seo_package_never_writes_to_the_web():
    """Read-only means read-only: no POST, PUT, PATCH or DELETE anywhere."""
    import os
    from agents import seo
    root = os.path.dirname(seo.__file__)
    for name in os.listdir(root):
        if not name.endswith('.py'):
            continue
        src = open(os.path.join(root, name), encoding='utf-8').read()
        for verb in ('requests.post', 'requests.put', 'requests.patch',
                     'requests.delete', '.submit('):
            assert verb not in src, f'{verb} found in agents/seo/{name}'


def test_a_dead_page_becomes_a_finding_not_a_crash(fake_web, site_profile):
    from agents.seo import crawl
    page = crawl.fetch_page('https://example.com/missing')
    assert page['status_code'] == 404
    assert page['error']


# ── Inspection ───────────────────────────────────────────────────────────────

def test_extract_reads_metadata_and_structured_data():
    from agents.seo import inspect
    page = inspect.extract(GOOD_HTML, 'https://example.com/roofing')
    assert page['title'].startswith('Roof Replacement')
    assert page['meta_description']
    assert page['h1_count'] == 1
    assert 'RoofingContractor' in page['schema_types']
    assert page['has_viewport'] is True
    assert page['images_missing_alt'] == 0


def test_extract_flags_malformed_structured_data():
    from agents.seo import inspect
    page = inspect.extract(BARE_HTML, 'https://example.com/bare')
    assert page['broken_jsonld_blocks'] == 1
    assert page['h1_count'] == 2
    assert page['images_missing_alt'] == 1
    assert page['has_viewport'] is False


SPA_HTML = ('<!doctype html><html><head><title>Project One</title></head>'
            '<body><div id="root"></div>'
            '<script>var a=1;/* lots of minified js words here to inflate */</script>'
            '</body></html>')


def test_a_javascript_shell_is_detected_as_client_rendered():
    """projectoneroofingcolorado.com is a React SPA: 4.9KB of HTML, no H1, no content.
    Before this check the strategist reported 'add an H1' on all 40 pages."""
    from agents.seo import inspect
    page = inspect.extract(SPA_HTML, 'https://example.com/')
    assert page['client_rendered'] is True
    assert page['root_container'] == 'root'


def test_script_bodies_do_not_count_as_page_content():
    from agents.seo import inspect
    page = inspect.extract(SPA_HTML, 'https://example.com/')
    assert page['word_count'] < 10, 'minified JS is being counted as content'


def test_a_real_page_is_not_flagged_as_client_rendered():
    from agents.seo import inspect
    assert inspect.extract(GOOD_HTML, 'u')['client_rendered'] is False


def test_content_findings_are_suppressed_on_client_rendered_pages():
    """The whole point: never grade markup that is not the page anyone sees."""
    from agents.seo import recommend
    result = {'pages': [{**__import__('agents.seo.inspect', fromlist=['x'])
                         .extract(SPA_HTML, f'https://example.com/p{i}'),
                         'url': f'https://example.com/p{i}', 'depth': 1}
                        for i in range(5)],
              'base_url': 'https://example.com'}
    recs = recommend.from_crawl(result)
    kinds = {r.get('kind') for r in recs}
    assert 'client_rendered' in kinds, 'the shell itself should be reported'
    for wrong in ('no_h1', 'no_meta_desc', 'thin_content', 'missing_alt',
                  'bad_title_length', 'orphan_page'):
        assert wrong not in kinds, f'{wrong} was judged on an unreadable shell'


def test_the_client_rendered_finding_makes_no_ranking_claim():
    from agents.seo import honesty, recommend
    result = {'pages': [{**__import__('agents.seo.inspect', fromlist=['x'])
                         .extract(SPA_HTML, f'https://example.com/p{i}'),
                         'url': f'https://example.com/p{i}', 'depth': 1}
                        for i in range(3)],
              'base_url': 'https://example.com'}
    kept, dropped = honesty.filter_all(recommend.from_crawl(result))
    assert not dropped
    csr = next(r for r in kept if r.get('kind') == 'client_rendered')
    assert 'not a claim about how the site currently performs' in csr['rationale']


def test_extract_never_raises_on_garbage():
    from agents.seo import inspect
    for junk in ('', '<<<>>>', '<html><head><title>unclosed'):
        assert isinstance(inspect.extract(junk, 'u'), dict)


# ── Recommendations ──────────────────────────────────────────────────────────

def test_crawl_findings_produce_technical_and_page_recommendations(fake_web, site_profile):
    from agents.seo import crawl, recommend
    result = crawl.crawl_site('https://example.com', max_pages=10)
    recs = recommend.from_crawl(result)
    cats = {r['category'] for r in recs}
    assert 'technical_website_fix' in cats
    assert 'improve_existing_page' in cats
    # The bare page is missing a title, description and viewport.
    actions = ' '.join(r['action'] for r in recs)
    assert 'title' in actions.lower()
    assert 'viewport' in actions.lower()


def test_every_generated_recommendation_survives_the_honesty_check(fake_web, site_profile):
    """The generator must not produce output its own guardrails reject."""
    from agents.seo import crawl, honesty, recommend
    result = crawl.crawl_site('https://example.com', max_pages=10)
    recs = recommend.build_all(result)
    kept, dropped = honesty.filter_all(recs)
    assert not dropped, f'generator produced rejectable output: {dropped[:3]}'
    assert kept


def test_a_site_wide_fault_becomes_one_recommendation_not_thirty():
    """Against the real site this collapsed 239 items to a reviewable queue.
    Dozens of copies of the same finding is technically correct output and a
    completely unusable one."""
    from agents.seo import recommend
    singles = [{'category': 'technical_website_fix', 'kind': 'missing_alt',
                'action': f'Add alt text on /page{i}', 'rationale': 'No alt.',
                'evidence': [f'https://example.com/page{i}'], 'confidence': 'high',
                'review_notes': 'Describe the image.'} for i in range(30)]
    grouped = recommend.group_repeated(singles)
    assert len(grouped) == 1
    assert '30 pages' in grouped[0]['action']
    assert grouped[0]['affected_pages'] == 30
    assert len(grouped[0]['evidence']) <= 8
    assert 'one template change' in grouped[0]['review_notes']


def test_a_finding_below_the_threshold_stays_per_page():
    """Two instances are not a pattern — the per-page detail is more useful."""
    from agents.seo import recommend
    singles = [{'category': 'technical_website_fix', 'kind': 'missing_alt',
                'action': f'Add alt text on /page{i}', 'rationale': 'No alt.',
                'evidence': [f'https://example.com/page{i}'], 'confidence': 'high',
                'review_notes': 'Describe the image.'} for i in range(2)]
    assert len(recommend.group_repeated(singles)) == 2


def test_grouped_recommendations_still_pass_the_honesty_check():
    from agents.seo import honesty, recommend
    singles = [{'category': 'technical_website_fix', 'kind': 'missing_alt',
                'action': f'Add alt text on /page{i}', 'rationale': 'No alt.',
                'evidence': [f'https://example.com/page{i}'], 'confidence': 'high',
                'review_notes': 'Describe the image.'} for i in range(5)]
    kept, dropped = honesty.filter_all(recommend.group_repeated(singles))
    assert kept and not dropped


def test_ranking_puts_technical_faults_above_content_ideas():
    from agents.seo import recommend
    tech = {'category': 'technical_website_fix', 'confidence': 'high',
            'evidence': ['u'], 'city': ''}
    brief = {'category': 'faq_or_content_brief', 'confidence': 'low',
             'evidence': ['u'], 'city': ''}
    assert recommend.score(tech) > recommend.score(brief)


def test_priority_markets_get_a_ranking_boost():
    from agents.seo import recommend
    base = {'category': 'create_city_or_service_area_page', 'confidence': 'medium',
            'evidence': ['u']}
    assert (recommend.score({**base, 'city': 'Fort Collins'})
            > recommend.score({**base, 'city': 'Nowhere'}))


# ── Research (mocked Perplexity) ─────────────────────────────────────────────

def test_research_returns_questions_with_citations(monkeypatch):
    from agents import perplexity
    from agents.seo import research
    monkeypatch.setattr(perplexity, 'search_json', lambda *a, **kw: {
        'data': {'questions': [{'question': 'Does insurance cover hail damage?',
                                'why_it_matters': 'Most CO claims start here.'}],
                 'sources': ['https://example.org/hail']},
        'citations': ['https://example.org/hail'], 'cost_usd': 0.01, 'cached': False})
    out = research.customer_questions('Roofing', 'Fort Collins')
    assert out['questions'][0]['question'].startswith('Does insurance')
    assert 'https://example.org/hail' in out['citations']


def test_the_texas_franchise_is_never_reported_as_a_competitor(monkeypatch):
    """projectoneroofing.com is the same brand, different franchise. Treating
    it as a rival benchmarks us against ourselves; treating it as ours writes
    copy for a market we do not serve."""
    from agents import perplexity
    from agents.seo import research
    monkeypatch.setattr(perplexity, 'search_json', lambda *a, **kw: {
        'data': {'competitors': [
            {'name': 'Project One Roofing (Texas)',
             'url': 'https://projectoneroofing.com', 'topics_covered': ['roofing']},
            {'name': 'A Real Rival', 'url': 'https://rival.example',
             'topics_covered': ['siding']}],
            'content_gaps': ['metal roofing guide'],
            'sources': ['https://projectoneroofing.com/x', 'https://rival.example/y']},
        'citations': [], 'cost_usd': 0.0, 'cached': True})

    out = research.competitor_landscape('Fort Collins', 'Roofing')
    names = [c['name'] for c in out['competitors']]
    assert names == ['A Real Rival']
    assert out['excluded_siblings'] == 1
    assert not any('projectoneroofing.com' in u for u in out['citations'])


def test_sibling_domains_come_from_the_profile():
    from agents.seo import research
    assert 'projectoneroofing.com' in research.sibling_domains()
    # The Colorado site must never be listed as its own sibling.
    assert 'projectoneroofingcolorado.com' not in research.sibling_domains()


def test_research_degrades_when_the_spend_cap_is_hit(monkeypatch):
    """A blown cap must cost us the research, not the whole report."""
    from agents import perplexity
    from agents.seo import research

    def boom(*a, **kw):
        raise perplexity.SpendCapReached('cap reached')
    monkeypatch.setattr(perplexity, 'search_json', boom)
    with pytest.raises(research.ResearchUnavailable):
        research.customer_questions('Roofing', 'Greeley')


def test_a_run_survives_research_being_unavailable(fake_web, site_profile, monkeypatch):
    from agents import perplexity
    from agents.seo import run as seo

    def boom(*a, **kw):
        raise perplexity.SpendCapReached('cap reached')
    monkeypatch.setattr(perplexity, 'search_json', boom)

    # Live, because a dry run skips research entirely and so would never
    # exercise the degradation path this test exists to prove.
    manifest = seo.run(dry_run=False)
    assert manifest['ok'] is True
    assert manifest['recommendations'], 'crawl findings should survive on their own'
    assert 'cap reached' in manifest['research_note']


# ── Runs, dry run, persistence ───────────────────────────────────────────────

def test_dry_run_writes_absolutely_nothing(fake_web, site_profile, monkeypatch):
    from agents import config
    from agents.seo import run as seo
    monkeypatch.setenv('PERPLEXITY_API_KEY', '')

    manifest = seo.run(dry_run=True)
    assert manifest['ok'] and manifest['recommendations']

    with config.get_cache_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM seo_runs').fetchone()['c'] == 0
        assert db.execute('SELECT COUNT(*) c FROM seo_recommendations').fetchone()['c'] == 0
        assert db.execute('SELECT COUNT(*) c FROM seo_reports').fetchone()['c'] == 0
        # Not even the crawl cache — a dry run leaves no trace at all.
        assert db.execute('SELECT COUNT(*) c FROM seo_page_cache').fetchone()['c'] == 0


def test_a_live_run_persists_a_report_and_a_queue(fake_web, site_profile, monkeypatch):
    from agents import config
    from agents.seo import run as seo
    monkeypatch.setattr('agents.seo.run._gather_research',
                        lambda dry: ({}, {}, 'skipped in test', 0.0))

    manifest = seo.run(dry_run=False)
    assert manifest['ok']
    with config.get_cache_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM seo_runs').fetchone()['c'] == 1
        assert db.execute('SELECT COUNT(*) c FROM seo_recommendations').fetchone()['c'] > 0
        report = db.execute('SELECT markdown FROM seo_reports').fetchone()
    assert 'Local SEO strategy' in report['markdown']


def test_the_report_states_what_it_could_not_see(fake_web, site_profile, monkeypatch):
    """A reader who doesn't know GA4/GSC are missing will misread the report."""
    from agents.seo import run as seo
    monkeypatch.setattr('agents.seo.run._gather_research',
                        lambda dry: ({}, {}, 'skipped', 0.0))
    md = seo.run(dry_run=True)['report_markdown']
    assert 'Search Console' in md and 'owner access required' in md.lower()
    assert 'nothing here is a measured result' in md.lower()
    assert 'public-research opportunities' in md.lower()


def test_a_failed_crawl_is_an_error_run_not_an_exception(monkeypatch, site_profile):
    from agents.seo import crawl, run as seo

    class DeadWeb:
        @staticmethod
        def get(url, **kw):
            return FakeResponse('', status=500)
    monkeypatch.setattr(crawl, 'requests', DeadWeb)
    monkeypatch.setattr(crawl, 'CRAWL_DELAY', 0)

    manifest = seo.run(dry_run=True)
    assert manifest['ok'] is False
    assert 'no readable pages' in manifest['error']


def test_approving_a_recommendation_records_but_does_not_act(fake_web, site_profile,
                                                             monkeypatch):
    from agents import config
    from agents.seo import run as seo
    monkeypatch.setattr('agents.seo.run._gather_research',
                        lambda dry: ({}, {}, 'skipped', 0.0))
    seo.run(dry_run=False)
    with config.get_cache_db() as db:
        rec_id = db.execute('SELECT id FROM seo_recommendations LIMIT 1').fetchone()['id']

    assert seo.set_status(rec_id, 'approved', 'luke') is True
    with config.get_cache_db() as db:
        row = db.execute('SELECT status, reviewed_by FROM seo_recommendations '
                         'WHERE id = ?', (rec_id,)).fetchone()
    assert row['status'] == 'approved'
    assert row['reviewed_by'] == 'luke'


def test_unknown_review_status_is_refused():
    from agents.seo import run as seo
    with pytest.raises(ValueError):
        seo.set_status(1, 'published', 'luke')


def test_the_crawl_cache_prevents_a_second_round_of_requests(fake_web, site_profile):
    from agents.seo import crawl
    crawl.crawl_site('https://example.com', max_pages=5)
    first = len(fake_web)
    crawl.crawl_site('https://example.com', max_pages=5)
    # robots.txt + sitemap are re-read; the pages themselves come from cache.
    assert len(fake_web) - first <= 2
