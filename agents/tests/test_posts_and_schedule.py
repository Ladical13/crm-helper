"""Social/GBP post bot, the weekly scheduler, and Bing Webmaster Tools.

No network anywhere: Perplexity and Bing are monkeypatched, and the scheduler
is driven by an injected clock rather than by waiting.
"""
import json
from datetime import datetime

import pytest


# ── The post bot ─────────────────────────────────────────────────────────────

def _fake_perplexity(monkeypatch, body='We re-roofed a home in Fort Collins '
                                        'this week after the last storm.',
                     cta='Book a roof inspection.'):
    from agents import perplexity
    monkeypatch.setattr(perplexity, 'search_json', lambda *a, **kw: {
        'data': {'body': body, 'hashtags': ['#FortCollins', '#Roofing'],
                 'call_to_action': cta, 'image_prompt': 'A finished roof at dusk'},
        'citations': [], 'cost_usd': 0.002, 'cached': False})


TOPIC = {'topic': 'Hail damage in Fort Collins', 'summary': 'Storm on the 23rd.',
         'city': 'Fort Collins', 'citations': ['https://coloradodaily.example/1'],
         'source': 'gdelt'}


def test_a_package_covers_every_requested_platform(monkeypatch):
    from agents.content import posts
    _fake_perplexity(monkeypatch)
    pkg = posts.build_package(TOPIC, dry_run=True)
    assert {p['platform'] for p in pkg['posts']} == set(posts.DEFAULT_PLATFORMS)
    assert not pkg['rejected']


def test_google_business_is_a_supported_platform():
    from agents.content import posts
    assert 'google_business' in posts.PLATFORMS
    assert posts.PLATFORMS['google_business']['hard_limit'] == 1500


def test_a_post_claiming_a_ranking_is_rejected_not_softened(monkeypatch):
    """Same guard as an SEO recommendation. A social post asserting "#1 roofer"
    is the same fabrication as a report asserting it."""
    from agents.content import posts
    _fake_perplexity(monkeypatch, body='We are ranked #1 for roofing in Denver.')
    pkg = posts.build_package(TOPIC, platforms=('facebook',), dry_run=True)
    assert pkg['posts'] == []
    assert 'unsupportable claim' in pkg['rejected'][0]['reason']


def test_a_post_using_a_banned_phrase_is_rejected(monkeypatch):
    from agents.content import posts
    _fake_perplexity(monkeypatch, body='Book your free roof inspection today.')
    pkg = posts.build_package(TOPIC, platforms=('facebook',), dry_run=True)
    assert pkg['posts'] == []
    assert 'banned phrase' in pkg['rejected'][0]['reason']


def test_a_post_over_the_platform_limit_is_rejected(monkeypatch):
    from agents.content import posts
    _fake_perplexity(monkeypatch, body='word ' * 2000)
    pkg = posts.build_package(TOPIC, platforms=('google_business',), dry_run=True)
    assert pkg['posts'] == []
    assert 'exceeds' in pkg['rejected'][0]['reason']


def test_google_business_posts_carry_no_hashtags(monkeypatch):
    """Hashtags do nothing on a Business Profile post."""
    from agents.content import posts
    _fake_perplexity(monkeypatch)
    pkg = posts.build_package(TOPIC, platforms=('google_business',), dry_run=True)
    assert '#' not in pkg['posts'][0]['draft_text']


def test_instagram_posts_ask_for_a_real_photograph(monkeypatch):
    from agents.content import posts
    _fake_perplexity(monkeypatch)
    pkg = posts.build_package(TOPIC, platforms=('instagram',), dry_run=True)
    assert 'Photo to shoot' not in pkg['posts'][0]['draft_text']
    assert pkg['posts'][0]['creative']['image_prompt'] == 'A finished roof at dusk'
    assert 'not stock' in pkg['posts'][0]['review_notes']


def test_a_reddit_sourced_post_warns_against_quoting(monkeypatch):
    from agents.content import posts
    _fake_perplexity(monkeypatch)
    topic = dict(TOPIC, citations=['https://www.reddit.com/r/Denver/comments/x/'])
    pkg = posts.build_package(topic, platforms=('facebook',), dry_run=True)
    notes = pkg['posts'][0]['review_notes']
    assert 'do NOT quote' in notes and 'identifiable' in notes


def test_the_system_prompt_forbids_superiority_while_none_are_proven():
    from agents import config
    from agents.content import posts
    prompt = posts._system_prompt(config.load_marketing_profile())
    assert 'NO comparative or superiority claims' in prompt
    assert 'top-rated' in prompt
    # And it names only services we actually sell.
    assert 'Roofing' in prompt and 'Siding' in prompt


def test_a_dry_run_saves_no_drafts(monkeypatch):
    from agents import config
    from agents.content import posts
    _fake_perplexity(monkeypatch)
    posts.build_package(TOPIC, dry_run=True)
    with config.get_cache_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM content_drafts').fetchone()['c'] == 0


def test_a_live_package_is_saved_and_grouped(monkeypatch):
    from agents import config
    from agents.content import posts
    _fake_perplexity(monkeypatch)
    pkg = posts.build_package(TOPIC, dry_run=False)
    with config.get_cache_db() as db:
        rows = db.execute('SELECT package_id, platform FROM content_drafts').fetchall()
    assert len(rows) == 4
    assert {r['package_id'] for r in rows} == {pkg['package_id']}


def test_the_spend_cap_costs_a_platform_not_the_package(monkeypatch):
    from agents import perplexity
    from agents.content import posts
    calls = {'n': 0}

    def flaky(*a, **kw):
        calls['n'] += 1
        if calls['n'] == 1:
            raise perplexity.SpendCapReached('cap reached')
        return {'data': {'body': 'A short honest update about a Fort Collins roof.',
                         'hashtags': [], 'call_to_action': 'Call us.'},
                'citations': [], 'cost_usd': 0.0}

    monkeypatch.setattr(perplexity, 'search_json', flaky)
    pkg = posts.build_package(TOPIC, dry_run=True)
    assert pkg['posts'], 'the remaining platforms should still be written'
    assert any('cap' in r['reason'] for r in pkg['rejected'])


def test_weekly_run_prefers_an_approved_recommendation(monkeypatch):
    """An approved recommendation has a human behind it; a trending topic is
    merely interesting."""
    from agents import config
    from agents.content import posts
    _fake_perplexity(monkeypatch)
    with config.get_cache_db() as db:
        db.execute(
            "INSERT INTO seo_recommendations (run_id, created_at, category, city, "
            "service, intent, action, evidence, score, status) VALUES "
            "(1, '2026-08-11T00:00:00Z', 'create_city_or_service_area_page', "
            "'Greeley', 'roofing', 'Do I need a new roof after hail?', "
            "'Consider a Greeley page.', '[]', 9.0, 'approved')")
        db.execute("INSERT INTO trending_topics (captured_at, topic, score) "
                   "VALUES ('2026-08-11T00:00:00Z', 'something trending', 1.0)")
        db.commit()
    topics = posts.candidate_topics(limit=1)
    assert topics[0]['source'] == 'seo_recommendation'


def test_weekly_run_says_so_when_there_is_nothing_worth_posting(monkeypatch):
    from agents.content import posts
    from agents.content.sources import news_gdelt
    monkeypatch.setattr(news_gdelt, 'storm_events',
                        lambda **k: {'articles': [], 'note': 'none'})
    out = posts.weekly_run(dry_run=True)
    assert out['packages'] == []
    assert 'nothing worth posting' in out['note']


# ── Scheduler ────────────────────────────────────────────────────────────────

MONDAY_6AM = datetime(2026, 8, 10, 6, 30)     # a Monday
MONDAY_5AM = datetime(2026, 8, 10, 5, 30)
TUESDAY    = datetime(2026, 8, 11, 6, 30)


def test_scheduler_is_off_unless_explicitly_enabled(monkeypatch):
    """Importing the app must never start background work by surprise."""
    from agents import scheduler
    monkeypatch.delenv('NIMBUS_SCHEDULER', raising=False)
    assert scheduler.enabled() is False
    assert scheduler.start() is False
    monkeypatch.setenv('NIMBUS_SCHEDULER', '1')
    assert scheduler.enabled() is True


def test_jobs_are_seeded_once_and_not_overwritten():
    from agents import scheduler
    scheduler.ensure_jobs()
    scheduler.set_enabled('seo_weekly', False)
    scheduler.ensure_jobs()
    job = next(j for j in scheduler.list_jobs() if j['name'] == 'seo_weekly')
    assert job['enabled'] == 0, 'ensure_jobs clobbered a changed schedule'


def test_a_job_is_due_only_on_its_day_and_hour():
    from agents import scheduler
    scheduler.ensure_jobs()
    job = next(j for j in scheduler.list_jobs() if j['name'] == 'seo_weekly')
    assert scheduler._due(job, MONDAY_6AM) is True
    assert scheduler._due(job, MONDAY_5AM) is False, 'ran before its hour'
    assert scheduler._due(job, TUESDAY) is False, 'ran on the wrong day'


def test_a_disabled_job_is_never_due():
    from agents import scheduler
    scheduler.ensure_jobs()
    scheduler.set_enabled('seo_weekly', False)
    job = next(j for j in scheduler.list_jobs() if j['name'] == 'seo_weekly')
    assert scheduler._due(job, MONDAY_6AM) is False


def test_only_one_worker_can_claim_a_job():
    """Gunicorn runs two workers and each starts a scheduler. Without an
    atomic claim the weekly report would run twice."""
    from agents import scheduler
    scheduler.ensure_jobs()
    assert scheduler._claim('seo_weekly', MONDAY_6AM) is True
    assert scheduler._claim('seo_weekly', MONDAY_6AM) is False


def test_a_job_does_not_rerun_the_same_day():
    from agents import scheduler
    scheduler.ensure_jobs()
    scheduler._claim('seo_weekly', MONDAY_6AM)
    job = next(j for j in scheduler.list_jobs() if j['name'] == 'seo_weekly')
    assert scheduler._due(job, datetime(2026, 8, 10, 23, 0)) is False


def test_run_due_executes_the_job_and_records_the_result(monkeypatch):
    from agents import scheduler
    scheduler.ensure_jobs()
    for name in ('content_listen', 'social_weekly'):
        scheduler.set_enabled(name, False)
    monkeypatch.setitem(scheduler.JOBS, 'seo_weekly', lambda: '7 recommendations')
    ran = scheduler.run_due(MONDAY_6AM)
    assert ran == ['seo_weekly']
    job = next(j for j in scheduler.list_jobs() if j['name'] == 'seo_weekly')
    assert job['last_status'] == 'ok'
    assert '7 recommendations' in job['last_summary']


def test_a_failing_job_is_recorded_not_raised(monkeypatch):
    """One bad week must not take the scheduler down until the next deploy."""
    from agents import scheduler
    scheduler.ensure_jobs()
    for name in ('content_listen', 'social_weekly'):
        scheduler.set_enabled(name, False)

    def boom():
        raise RuntimeError('crawl exploded')
    monkeypatch.setitem(scheduler.JOBS, 'seo_weekly', boom)

    assert scheduler.run_due(MONDAY_6AM) == []
    job = next(j for j in scheduler.list_jobs() if j['name'] == 'seo_weekly')
    assert job['last_status'] == 'error'
    assert 'crawl exploded' in job['last_summary']


def test_the_listen_job_runs_before_the_seo_job():
    """The SEO run consumes the topics the listen pass produces."""
    from agents import scheduler
    jobs = dict((n, (d, h)) for n, d, h in scheduler.DEFAULT_JOBS)
    assert jobs['content_listen'] < jobs['seo_weekly']


# ── Bing ─────────────────────────────────────────────────────────────────────

_BING_ROWS = [
    {'Query': 'roof repair fort collins', 'Impressions': 120, 'Clicks': 9,
     'AvgImpressionPosition': 4.2},
    {'Query': 'hail damage roof', 'Impressions': 300, 'Clicks': 0,
     'AvgImpressionPosition': 12.5},
]


def test_bing_is_unavailable_without_a_key(monkeypatch):
    from agents.seo import bing
    monkeypatch.delenv('BING_WEBMASTER_API_KEY', raising=False)
    monkeypatch.delenv('BING_SITE_URL', raising=False)
    out = bing.summary()
    assert out['available'] is False and 'not set' in out['note']


def test_bing_query_stats_are_labelled_as_bing(monkeypatch):
    """A reader taking a Bing impression count for a Google one has been
    misled just as surely as by an invented number."""
    from agents.seo import bing
    monkeypatch.setenv('BING_WEBMASTER_API_KEY', 'k')
    monkeypatch.setenv('BING_SITE_URL', 'https://example.com')
    monkeypatch.setattr(bing, '_call', lambda m, **k: _BING_ROWS)
    rows = bing.query_stats()
    assert all(r['source'] == bing.SOURCE_LABEL for r in rows)
    assert rows[0]['impressions'] == 300, 'sorted by impressions'


def test_bing_splits_earning_from_seen_but_ignored():
    from agents.seo import bing
    split = bing.winners_and_decliners([
        {'query': 'a', 'impressions': 120, 'clicks': 9, 'avg_position': 4.2},
        {'query': 'b', 'impressions': 300, 'clicks': 0, 'avg_position': 12.5},
    ])
    assert [q['query'] for q in split['earning_clicks']] == ['a']
    assert [q['query'] for q in split['seen_not_clicked']] == ['b']


def test_a_broken_bing_costs_one_section_not_the_run(monkeypatch):
    from agents.seo import bing
    monkeypatch.setenv('BING_WEBMASTER_API_KEY', 'k')
    monkeypatch.setenv('BING_SITE_URL', 'https://example.com')

    def boom(*a, **k):
        raise bing.BingUnavailable('HTTP 403 — the API key was rejected')
    monkeypatch.setattr(bing, '_call', boom)
    out = bing.summary()
    assert out['available'] is False
    assert '403' in out['note']


def test_the_report_labels_bing_data_as_bing_not_search(fake_web, site_profile,
                                                        monkeypatch):
    from agents.seo import bing, run as seo
    monkeypatch.setattr('agents.seo.run._gather_research',
                        lambda dry: ({}, {}, 'skipped', 0.0))
    monkeypatch.setattr(bing, 'summary', lambda: {
        'available': True, 'source': bing.SOURCE_LABEL, 'note': '2 rows',
        'queries': [{'query': 'roof repair fort collins', 'impressions': 120,
                     'clicks': 9, 'avg_position': 4.2},
                    {'query': 'hail damage roof', 'impressions': 300,
                     'clicks': 0, 'avg_position': 12.5}],
        'pages': []})
    md = seo.run(dry_run=True)['report_markdown']
    assert 'Bing only' in md
    assert 'They are Bing, not' in md
    assert 'Seen but not clicked' in md
    # And it must not pretend to be a trend.
    assert 'not a period-over-period delta' in md


def test_without_bing_the_sections_stay_blocked_and_point_at_it(fake_web,
                                                                site_profile,
                                                                monkeypatch):
    from agents.seo import bing, run as seo
    monkeypatch.setattr('agents.seo.run._gather_research',
                        lambda dry: ({}, {}, 'skipped', 0.0))
    monkeypatch.setattr(bing, 'summary', lambda: {
        'available': False, 'queries': [], 'pages': [], 'note': 'not set'})
    md = seo.run(dry_run=True)['report_markdown']
    assert 'Search Console winners and decliners' in md
    assert 'Bing Webmaster Tools would fill part of this' in md


# ── the nightly storm ingest ───────────────────────────────────────────

class _FakeSwath(list):
    """A swath is truthy when it holds cells and falsy when the day was quiet.
    Both are recorded — that is what tells "no hail" from "never ingested"."""
    max_size = 2.0


def _fake_hail(monkeypatch, *, held=(), fails=()):
    """Stand in for hail.ingest / hail.storms. No network, no database."""
    import datetime as dt

    from hail import ingest, storms
    recorded = {}

    def swath_for(d, **kw):
        if d.isoformat() in fails:
            raise RuntimeError('NOAA said no')
        return _FakeSwath(['cell']) if d.day % 2 else _FakeSwath()

    monkeypatch.setattr(ingest, 'swath_for', swath_for)
    monkeypatch.setattr(ingest, 'EARLIEST', dt.date(2020, 10, 14))
    monkeypatch.setattr(storms, 'ingested_dates', lambda *a, **k: set(held))
    monkeypatch.setattr(storms, 'record',
                        lambda date, swath, **k: recorded.__setitem__(date, swath))
    return recorded


def test_the_nightly_ingest_fills_a_gap_a_missed_night_left(monkeypatch):
    """A deploy, a restart or a NOAA hiccup must not leave a permanent hole.

    Once a gap is old it is indistinguishable from a quiet day — which is
    exactly why `storms.record` writes days with no hail at all — so the
    window looks back rather than fetching only yesterday.
    """
    from agents import scheduler
    recorded = _fake_hail(monkeypatch, held=())
    note = scheduler._job_hail_daily()
    assert len(recorded) == scheduler.HAIL_WINDOW_DAYS, note


def test_a_day_already_held_is_not_refetched(monkeypatch):
    """Skipping held days is what makes the job cheap enough to run nightly."""
    import datetime as dt

    from agents import scheduler
    every_day = {(dt.date.today() - dt.timedelta(days=i)).isoformat()
                 for i in range(scheduler.HAIL_WINDOW_DAYS)}
    recorded = _fake_hail(monkeypatch, held=every_day)
    scheduler._job_hail_daily()
    assert len(recorded) == scheduler.HAIL_REFETCH_DAYS, (
        'only the days still settling should be pulled again')


def test_the_most_recent_days_are_refetched_even_when_held(monkeypatch):
    """`MESH_Max_1440min` is a ROLLING 24-hour maximum, so a file read early
    is still moving. `storms.record` REPLACES a date's cells rather than
    merging them, which is what makes the re-fetch safe rather than additive."""
    import datetime as dt

    from agents import scheduler
    today = dt.date.today()
    recorded = _fake_hail(monkeypatch, held={today.isoformat()})
    scheduler._job_hail_daily()
    assert today.isoformat() in recorded


def test_one_bad_day_does_not_cost_the_others(monkeypatch):
    import datetime as dt

    from agents import scheduler
    bad = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    recorded = _fake_hail(monkeypatch, fails={bad})
    note = scheduler._job_hail_daily()
    assert bad not in recorded, 'a failed day must stay unrecorded and be retried'
    assert len(recorded) == scheduler.HAIL_WINDOW_DAYS - 1
    assert 'will retry' in note


def test_a_run_that_fetched_nothing_is_not_reported_as_success(monkeypatch):
    """A job that quietly succeeds while ingesting nothing is how an archive
    goes stale with a green status beside it."""
    import datetime as dt

    from agents import scheduler
    everything = {(dt.date.today() - dt.timedelta(days=i)).isoformat()
                  for i in range(scheduler.HAIL_WINDOW_DAYS)}
    _fake_hail(monkeypatch, fails=everything)
    with pytest.raises(RuntimeError):
        scheduler._job_hail_daily()


def test_a_daily_job_is_due_every_day_not_one_weekday():
    """The scheduler was weekly-only. MESH is published daily, so a weekly
    pull would leave six days of roofs unrecorded between runs."""
    from agents import scheduler
    scheduler.ensure_jobs()
    job = next(j for j in scheduler.list_jobs() if j['name'] == 'hail_daily')
    assert int(job['weekday']) == scheduler.DAILY
    for day in range(1, 8):                      # a whole week, at its hour
        when = datetime(2026, 6, day, int(job['hour_utc']))
        assert scheduler._due(job, when) is True, when.strftime('%A')
    assert scheduler._due(job, datetime(2026, 6, 1, 0)) is False, 'ran before its hour'


def test_every_scheduled_job_has_a_body():
    """A job seeded with no entry in JOBS is claimed, runs nothing, and stamps
    itself successful — a silent no-op with a green status."""
    from agents import scheduler
    assert sorted(scheduler.JOBS) == sorted(n for n, _, _ in scheduler.DEFAULT_JOBS)
