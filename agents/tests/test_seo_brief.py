"""Search intent, weekly-report structure, and the Content Brief bot.

Split from test_seo.py because these cover the layer *above* the crawler: what
the strategist concludes and what it hands to a writer.

No network. The fake site and profile fixtures are shared via conftest.
"""
import pytest


# ── Search intent and business value ────────────────────────────────────────

@pytest.mark.parametrize('text,expected', [
    ('roof replacement cost fort collins', 'transactional'),
    ('roofer near me', 'transactional'),
    ('best roofing material for colorado', 'commercial'),
    ('metal vs shingle roof', 'commercial'),
    ('how long does a roof last', 'informational'),
    ('does insurance cover hail damage', 'informational'),
])
def test_intent_classification_is_explainable(text, expected):
    from agents.seo import intent
    got, why = intent.classify(text)
    assert got == expected, f'{text!r} -> {got}'
    assert why, 'every classification must say why'


@pytest.mark.parametrize('text', [
    'Roofing in Fort Collins',
    'siding in Greeley',
    'windows in Colorado Springs',
])
def test_service_plus_city_is_transactional(text):
    """The highest-value local pattern there is, and it contains no verb —
    so it matched none of the keyword patterns and fell through to
    "informational". Caught on the first real run: the content plan wanted an
    FAQ where a city page belongs."""
    from agents.seo import intent
    got, why = intent.classify(text)
    assert got == 'transactional', f'{text!r} -> {got} ({why})'


def test_a_question_about_a_service_stays_informational():
    """The service+city rule must not swallow genuine questions."""
    from agents.seo import intent
    got, _ = intent.classify('how much does roofing cost in Fort Collins')
    assert got == 'transactional'      # "cost" is a buying signal
    got, _ = intent.classify('why does roofing in Fort Collins take so long')
    assert got == 'informational'


def test_buying_intent_outranks_reading_intent():
    from agents.seo import intent
    hire = intent.business_value('roof replacement cost in Fort Collins',
                                 city='Fort Collins', service='roofing')
    learn = intent.business_value('how long does a roof last')
    assert hire > learn


def test_a_priority_market_beats_an_unnamed_one():
    from agents.seo import intent
    here = intent.business_value('roof repair in Greeley', city='Greeley')
    away = intent.business_value('roof repair in Nowhere', city='Nowhere')
    assert here > away


def test_an_opportunity_states_it_has_no_volume_data():
    """A made-up search volume is the most tempting lie in SEO tooling."""
    from agents.seo import intent
    o = intent.opportunity('roof replacement cost', city='Greeley')
    assert 'no search volume' in o['basis'].lower()
    assert 'volume' not in [k.lower() for k in o]


def test_opportunities_rank_by_value_then_intent():
    from agents.seo import intent
    ranked = intent.rank([
        intent.opportunity('how long does a roof last'),
        intent.opportunity('roof replacement cost in Fort Collins',
                           city='Fort Collins', service='roofing'),
    ])
    assert ranked[0]['intent'] == 'transactional'


# ── Weekly report structure ──────────────────────────────────────────────────

def _report(monkeypatch):
    from agents.seo import run as seo
    monkeypatch.setattr('agents.seo.run._gather_research',
                        lambda dry: ({}, {}, 'skipped', 0.0))
    return seo.run(dry_run=True)


def test_the_report_has_every_required_section(fake_web, site_profile, monkeypatch):
    md = _report(monkeypatch)['report_markdown']
    for heading in ('Local topic opportunities',
                    'Search Console winners and decliners',
                    'Pages losing visibility',
                    'Technical website issues',
                    'Competitor and content gaps',
                    'Five recommended page improvements',
                    'Weekly content plan'):
        assert heading in md, f'missing section: {heading}'


def test_the_blocked_sections_say_why_rather_than_being_omitted(fake_web,
                                                                site_profile,
                                                                monkeypatch):
    """Dropping them reads as an oversight. Present-and-blocked tells the
    reader the gap is a constraint, and what would lift it."""
    md = _report(monkeypatch)['report_markdown']
    after = md.split('Search Console winners and decliners', 1)[1][:700]
    assert 'Not available' in after
    assert 'requires Google Search Console' in after
    assert 'would be invention' in after


def test_the_content_plan_does_not_claim_measured_demand(fake_web, site_profile,
                                                         monkeypatch):
    md = _report(monkeypatch)['report_markdown']
    plan = md.split('Weekly content plan', 1)[1][:900]
    assert 'not to measured search demand' in plan


def test_the_content_plan_is_one_week_not_a_backlog(fake_web, site_profile,
                                                    monkeypatch):
    assert len(_report(monkeypatch)['content_plan']) <= 5


def test_the_report_never_claims_a_ranking_anywhere(fake_web, site_profile,
                                                    monkeypatch):
    """Belt and braces over the whole rendered document, not just the recs."""
    from agents.seo import honesty
    md = _report(monkeypatch)['report_markdown']
    # The blocked sections legitimately use the words "position" and
    # "impressions" while explaining what is missing; check the queue body.
    body = md.split('Full recommendation queue', 1)[-1]
    assert not honesty.find_fabricated_metrics(body)


# ── Content briefs ───────────────────────────────────────────────────────────

def _a_recommendation(monkeypatch):
    """Run for real, return the id of a page-work recommendation."""
    from agents import config
    from agents.seo import run as seo
    monkeypatch.setattr('agents.seo.run._gather_research',
                        lambda dry: ({}, {}, 'skipped', 0.0))
    seo.run(dry_run=False)
    with config.get_cache_db() as db:
        row = db.execute("SELECT id FROM seo_recommendations WHERE category != "
                         "'technical_website_fix' LIMIT 1").fetchone()
    assert row, 'fixture produced no page-work recommendation'
    return row['id']


def test_a_brief_requires_an_approved_recommendation(fake_web, site_profile,
                                                     monkeypatch):
    """A brief for work nobody signed off on is just more output."""
    from agents.seo import brief
    rec_id = _a_recommendation(monkeypatch)
    with pytest.raises(brief.NotApproved):
        brief.create_for_recommendation(rec_id)


def test_a_brief_carries_every_required_field(fake_web, site_profile, monkeypatch):
    from agents.seo import brief, run as seo
    rec_id = _a_recommendation(monkeypatch)
    seo.set_status(rec_id, 'approved', 'luke')
    b = brief.create_for_recommendation(rec_id)
    for field in ('topic', 'search_intent', 'city', 'service', 'customer_question',
                  'page_type', 'outline', 'internal_links', 'required_assets',
                  'claims_needing_review', 'call_to_action', 'measurement'):
        assert field in b, f'brief is missing {field}'
    assert b['page_type'] in brief.PAGE_TYPES
    assert b['outline'] and b['required_assets'] and b['claims_needing_review']


def test_a_brief_forbids_superiority_claims_while_none_are_proven(fake_web,
                                                                  site_profile,
                                                                  monkeypatch):
    """provable_differentiators is empty. That means none are proven — not
    that a writer may use their judgement."""
    from agents.seo import brief, run as seo
    rec_id = _a_recommendation(monkeypatch)
    seo.set_status(rec_id, 'approved', 'luke')
    b = brief.create_for_recommendation(rec_id)
    joined = ' '.join(b['claims_needing_review']).lower()
    assert 'no comparative or superiority claims' in joined
    assert '"best"' in joined or 'best' in joined


def test_a_brief_is_honest_about_what_cannot_be_measured(fake_web, site_profile,
                                                         monkeypatch):
    from agents.seo import brief, run as seo
    rec_id = _a_recommendation(monkeypatch)
    seo.set_status(rec_id, 'approved', 'luke')
    b = brief.create_for_recommendation(rec_id)
    cannot = ' '.join(b['measurement']['not_measurable_today']).lower()
    assert 'search console' in cannot and 'analytics' in cannot
    assert b['measurement']['measurable_today']


def test_brief_internal_links_are_real_crawled_urls_not_invented(fake_web,
                                                                 site_profile,
                                                                 monkeypatch):
    from agents.seo import brief, run as seo
    rec_id = _a_recommendation(monkeypatch)
    seo.set_status(rec_id, 'approved', 'luke')
    b = brief.create_for_recommendation(rec_id)
    for link in b['internal_links']:
        assert link['url'].startswith('https://example.com'), \
            f'invented URL in brief: {link["url"]}'


def test_a_brief_renders_to_markdown_a_writer_can_use(fake_web, site_profile,
                                                      monkeypatch):
    from agents.seo import brief, run as seo
    rec_id = _a_recommendation(monkeypatch)
    seo.set_status(rec_id, 'approved', 'luke')
    md = brief.to_markdown(brief.create_for_recommendation(rec_id))
    for heading in ('Content brief', 'Customer question being answered', 'Outline',
                    'Internal links', 'Required proof and assets',
                    'Claims requiring review', 'Call to action',
                    'How success will be measured'):
        assert heading in md, f'brief markdown missing: {heading}'


def test_a_brief_never_uses_a_banned_phrase(fake_web, site_profile, monkeypatch):
    from agents import config
    from agents.seo import brief, run as seo
    rec_id = _a_recommendation(monkeypatch)
    seo.set_status(rec_id, 'approved', 'luke')
    b = brief.create_for_recommendation(rec_id)
    # The claims list names banned phrases in order to forbid them, which is
    # correct; the prose a writer follows must not use them.
    prose = ' '.join(b['outline']) + ' ' + b['call_to_action']
    for phrase in config.banned_phrases():
        assert phrase not in prose.lower(), f'brief prose uses banned {phrase!r}'


def test_brief_for_a_missing_recommendation_raises_lookup(fake_web, site_profile):
    from agents.seo import brief
    with pytest.raises(LookupError):
        brief.create_for_recommendation(99999)


# ── Interrupted runs ─────────────────────────────────────────────────────────

def test_a_run_killed_by_a_restart_is_reaped_not_left_running():
    """A run is a worker thread. A deploy, reload or crash kills it mid-flight
    and the row would otherwise say "running" forever, with the UI showing a
    run that never finishes and no reason why. Hit in real use."""
    from agents import config
    from agents.seo import run as seo
    with config.get_cache_db() as db:
        db.execute("INSERT INTO seo_runs (started_at, status, mode) "
                   "VALUES ('2020-01-01T00:00:00Z', 'running', 'live')")
        db.commit()

    assert seo.reap_stale_runs() == 1
    with config.get_cache_db() as db:
        row = db.execute('SELECT status, error FROM seo_runs').fetchone()
    assert row['status'] == 'interrupted'
    assert 'restarted' in row['error']


def test_reaping_leaves_a_genuinely_running_run_alone():
    from agents import config
    from agents.seo import run as seo
    with config.get_cache_db() as db:
        db.execute('INSERT INTO seo_runs (started_at, status, mode) '
                   "VALUES (?, 'running', 'live')", (config.now_iso(),))
        db.commit()
    assert seo.reap_stale_runs() == 0
