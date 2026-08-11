"""Free listening sources: GDELT, Reddit, and cross-source scoring.

No network. Both sources are exercised against recorded response shapes, so
the suite is deterministic and costs nothing.

The scoring tests matter most. The cross-source bonus was written long before
anything could trigger it — with only Perplexity reporting, every topic had
exactly one source and the bonus was dead code. These pin the behaviour now
that it can actually fire.
"""
import json

import pytest


class FakeResp:
    def __init__(self, payload='', status=200, is_json=True):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


# ── GDELT ────────────────────────────────────────────────────────────────────

_GDELT_BODY = {'articles': [
    {'url': 'https://denverpost.example/hail-1', 'title': 'Hail pounds Fort Collins',
     'domain': 'denverpost.example', 'seendate': '20260810T143000Z'},
    # The same wire story, six outlets — exactly what the live API returned.
    {'url': 'https://a.example/x', 'title': 'Roofing Firm Publishes Windsor Guide',
     'domain': 'a.example', 'seendate': '20260810T090000Z'},
    {'url': 'https://b.example/x', 'title': 'Roofing Firm Publishes Windsor Guide',
     'domain': 'b.example', 'seendate': '20260810T090000Z'},
    {'url': 'https://c.example/x', 'title': 'Roofing Firm Publishes Windsor Guide!',
     'domain': 'c.example', 'seendate': '20260810T090000Z'},
]}


def test_gdelt_needs_no_credential():
    from agents.content.sources import news_gdelt
    assert news_gdelt.available() is True


def test_gdelt_collapses_syndicated_copies(monkeypatch):
    """One press release across six outlets is one signal, not six. Anyone
    with a wire budget could otherwise set our content agenda."""
    from agents.content.sources import news_gdelt
    monkeypatch.setattr(news_gdelt, 'requests',
                        type('R', (), {'get': staticmethod(lambda *a, **k: FakeResp(_GDELT_BODY))}))
    out = news_gdelt.pull()
    titles = [a['title'] for a in out['articles']]
    assert len(titles) == 2, titles
    wire = next(a for a in out['articles'] if 'Windsor' in a['title'])
    assert wire['syndicated_copies'] == 3


def test_gdelt_survives_a_rate_limit(monkeypatch):
    from agents.content.sources import news_gdelt
    monkeypatch.setattr(news_gdelt, 'RETRY_BACKOFF_S', 0)
    monkeypatch.setattr(news_gdelt, 'requests',
                        type('R', (), {'get': staticmethod(lambda *a, **k: FakeResp('', 429))}))
    out = news_gdelt.pull()
    assert out['articles'] == []
    assert 'rate-limited' in out['note']


def test_gdelt_retries_once_then_succeeds(monkeypatch):
    """429 then 200 is the normal pattern on this endpoint."""
    from agents.content.sources import news_gdelt
    calls = {'n': 0}

    def get(*a, **k):
        calls['n'] += 1
        return FakeResp('', 429) if calls['n'] == 1 else FakeResp(_GDELT_BODY)

    monkeypatch.setattr(news_gdelt, 'RETRY_BACKOFF_S', 0)
    monkeypatch.setattr(news_gdelt, 'requests', type('R', (), {'get': staticmethod(get)}))
    assert news_gdelt.pull()['articles']
    assert calls['n'] == 2


def test_gdelt_html_error_page_is_reported_as_a_query_error(monkeypatch):
    """GDELT answers a bad query with HTML and HTTP 200."""
    from agents.content.sources import news_gdelt
    monkeypatch.setattr(news_gdelt, 'requests', type('R', (), {
        'get': staticmethod(lambda *a, **k: FakeResp('<html>bad query</html>'))}))
    out = news_gdelt.pull()
    assert out['articles'] == []
    assert 'query error' in out['note']


def test_gdelt_query_is_scoped_to_colorado():
    """The Texas franchise shares the brand. Texas hail in a Colorado report
    would be worse than no report."""
    from agents.content.sources import news_gdelt
    q = news_gdelt._build_query()
    assert 'Colorado' in q and 'Fort Collins' in q


def test_gdelt_query_excludes_colorado_sports():
    """The first live run returned Broncos previews and a Chicago baseball
    column — "Denver"/"Colorado" hits sports coverage, and bare "roof" hits
    stadium roofs."""
    from agents.content.sources import news_gdelt
    q = news_gdelt._build_query()
    for team in ('-Broncos', '-Rockies', '-Nuggets'):
        assert team in q
    # Two-word domain phrases, not bare words.
    assert '"hail damage"' in q
    assert ' roof OR' not in q and '(roof OR' not in q


def test_gdelt_query_stays_inside_the_length_limit():
    """GDELT rejects a long query outright — "Your query was too short or too
    long" — and the week loses the source. Hit for real when the keyword list
    and the full city list were both long."""
    from agents.content.sources import news_gdelt
    assert len(news_gdelt._build_query()) <= news_gdelt.MAX_QUERY_CHARS
    assert len(news_gdelt._build_query(
        keywords=news_gdelt.KEYWORDS + ['a very long extra phrase'] * 20
    )) <= news_gdelt.MAX_QUERY_CHARS


def test_gdelt_trims_keywords_rather_than_dropping_the_query():
    from agents.content.sources import news_gdelt
    q = news_gdelt._build_query(keywords=['hail damage'] + [f'filler {i}' for i in range(30)])
    assert '"hail damage"' in q, 'the most valuable keyword must survive trimming'


def test_gdelt_drops_out_of_state_mentions(monkeypatch):
    """GDELT matches the article body, so a national roundup listing every
    state arrives looking local. A live run returned Arkansas homeowners,
    Kansas City, Georgia, and a Writer's Digest piece on fiction writing."""
    from agents.content.sources import news_gdelt
    body = {'articles': [
        {'url': 'https://a/1', 'title': 'Hail damage hits Fort Collins homes',
         'domain': 'random.example', 'seendate': '20260810T120000Z'},
        {'url': 'https://a/2', 'title': 'Power outages follow 5.6 inch hail',
         'domain': 'coloradodaily.com', 'seendate': '20260810T120000Z'},
        {'url': 'https://a/3', 'title': 'Arkansas homeowners insurance costs rise',
         'domain': 'liveinsurancenews.com', 'seendate': '20260810T120000Z'},
        {'url': 'https://a/4', 'title': 'How to write morally grey characters',
         'domain': 'writersdigest.com', 'seendate': '20260810T120000Z'},
    ]}
    monkeypatch.setattr(news_gdelt, 'requests', type('R', (), {
        'get': staticmethod(lambda *a, **k: FakeResp(body))}))
    out = news_gdelt.pull()
    titles = [a['title'] for a in out['articles']]
    assert len(titles) == 2, titles
    assert any('Fort Collins' in t for t in titles), 'place in the headline'
    assert any('5.6 inch hail' in t for t in titles), 'Colorado outlet'
    assert out['filtered_out'] == 2
    assert 'filtered' in out['note'], 'a silent filter is undebuggable'


def test_colorado_city_texas_is_not_our_colorado():
    """The live feed returned "Storm damage leaves Colorado City residents"
    from a station in Abilene. Colorado City is in TEXAS — the sibling
    franchise's patch, and the exact confusion the profile exists to prevent."""
    from agents.content.sources import news_gdelt
    assert news_gdelt.looks_colorado(
        {'title': 'Storm damage leaves Colorado City residents without power',
         'domain': 'ktxs.com'}) is False


def test_western_colorado_is_outside_the_service_area():
    """Real Colorado, wrong side of the mountains. The profile says east."""
    from agents.content.sources import news_gdelt
    assert news_gdelt.looks_colorado(
        {'title': 'I-70 closed in Western Colorado after mudslide',
         'domain': 'travelerstoday.com'}) is False
    assert news_gdelt.looks_colorado(
        {'title': 'Hail damage across Fort Collins', 'domain': 'x.example'}) is True


def test_gdelt_keeps_a_local_story_whose_headline_omits_the_state():
    """The single most relevant live result was a Colorado hail story whose
    headline never said "Colorado" — the outlet is what identified it."""
    from agents.content.sources import news_gdelt
    assert news_gdelt.looks_colorado(
        {'title': 'Power outages, downed trees follow 5.6 inch hail',
         'domain': 'coloradodaily.com'}) is True


def test_gdelt_topics_carry_their_source_and_citation(monkeypatch):
    from agents.content.sources import news_gdelt
    monkeypatch.setattr(news_gdelt, 'requests', type('R', (), {
        'get': staticmethod(lambda *a, **k: FakeResp(_GDELT_BODY))}))
    topics = news_gdelt.as_topics(news_gdelt.pull())
    assert topics and all(t['source_names'] == ['gdelt'] for t in topics)
    assert all(t['citations'] for t in topics)


# ── Reddit ───────────────────────────────────────────────────────────────────

def _reddit_listing(*posts):
    return {'data': {'children': [{'data': p} for p in posts]}}


_POST_LOCAL_Q = {
    'title': 'Does insurance cover a hail damaged roof in Denver?',
    'selftext': 'Adjuster says partial. Roof is 8 years old.',
    'permalink': '/r/Denver/comments/abc/hail/', 'subreddit': 'Denver',
    'num_comments': 42, 'over_18': False,
}
_POST_NATIONAL = {
    'title': 'Roof leak after storm', 'selftext': 'shingle missing',
    'permalink': '/r/Roofing/comments/def/leak/', 'subreddit': 'Roofing',
    'num_comments': 3, 'over_18': False,
}
_POST_IRRELEVANT = {
    'title': 'Best paint colour for a nursery', 'selftext': 'no idea',
    'permalink': '/r/HomeImprovement/comments/g/x/', 'subreddit': 'HomeImprovement',
    'num_comments': 90, 'over_18': False,
}
_POST_NSFW = {
    'title': 'roof', 'selftext': '', 'permalink': '/r/x/comments/h/y/',
    'subreddit': 'Roofing', 'num_comments': 1, 'over_18': True,
}


@pytest.fixture
def reddit_wired(monkeypatch):
    from agents.content.sources import reddit
    monkeypatch.setenv('REDDIT_CLIENT_ID', 'id')
    monkeypatch.setenv('REDDIT_CLIENT_SECRET', 'secret')
    monkeypatch.setattr(reddit, 'PER_REQUEST_DELAY', 0)
    reddit._token_cache.update({'token': '', 'expires_at': 0})

    class R:
        @staticmethod
        def post(*a, **k):
            return FakeResp({'access_token': 't', 'expires_in': 3600})

        @staticmethod
        def get(url, **k):
            return FakeResp(_reddit_listing(_POST_LOCAL_Q, _POST_NATIONAL,
                                            _POST_IRRELEVANT, _POST_NSFW))

    monkeypatch.setattr(reddit, 'requests', R)
    return reddit


def test_reddit_is_unavailable_without_credentials(monkeypatch):
    from agents.content.sources import reddit
    monkeypatch.delenv('REDDIT_CLIENT_ID', raising=False)
    monkeypatch.delenv('REDDIT_CLIENT_SECRET', raising=False)
    out = reddit.pull()
    assert out['available'] is False and out['posts'] == []
    assert 'REDDIT_CLIENT_ID' in out['note']


def test_reddit_keeps_only_keyword_matches(reddit_wired):
    out = reddit_wired.pull(subs=['Denver'])
    titles = [p['title'] for p in out['posts']]
    assert any('hail' in t.lower() for t in titles)
    assert not any('nursery' in t.lower() for t in titles)


def test_reddit_skips_nsfw_posts(reddit_wired):
    out = reddit_wired.pull(subs=['Roofing'])
    assert all(p['title'] != 'roof' for p in out['posts'])


def test_reddit_flags_colorado_local_subreddits(reddit_wired):
    out = reddit_wired.pull(subs=['Denver'])
    local = [p for p in out['posts'] if p['is_local']]
    assert local and local[0]['subreddit'] == 'Denver'


def test_reddit_questions_put_local_threads_first(reddit_wired):
    out = reddit_wired.pull(subs=['Denver'])
    qs = reddit_wired.questions(out)
    assert qs[0]['is_local'] is True
    assert qs[0]['is_question'] is True


def test_reddit_posts_carry_a_citable_permalink(reddit_wired):
    out = reddit_wired.pull(subs=['Denver'])
    assert all(p['url'].startswith('https://www.reddit.com/r/')
               for p in out['posts'] if p['url'])


def test_reddit_bad_credentials_surface_clearly(monkeypatch):
    from agents.content.sources import reddit
    monkeypatch.setenv('REDDIT_CLIENT_ID', 'id')
    monkeypatch.setenv('REDDIT_CLIENT_SECRET', 'wrong')
    reddit._token_cache.update({'token': '', 'expires_at': 0})
    monkeypatch.setattr(reddit, 'requests', type('R', (), {
        'post': staticmethod(lambda *a, **k: FakeResp('', 401))}))
    out = reddit.pull()
    assert out['posts'] == []
    assert '401' in out['note']


def test_reddit_user_agent_is_descriptive():
    """Reddit throttles generic user-agents."""
    from agents.content.sources import reddit
    ua = reddit.user_agent()
    assert 'project-one' in ua.lower() and 'contact' in ua.lower()


# ── Cross-source scoring ─────────────────────────────────────────────────────

def test_the_same_topic_from_two_sources_merges_into_one():
    from agents.content import score
    merged = score.merge_sources(
        [{'topic': 'Does insurance cover hail damage on a roof',
          'citations': ['https://reddit.example/1'], 'source_names': ['reddit']}],
        [{'topic': 'insurance cover hail damage roof claims',
          'citations': ['https://news.example/2'], 'source_names': ['gdelt']}])
    assert len(merged) == 1
    assert set(merged[0]['source_names']) == {'reddit', 'gdelt'}
    assert len(merged[0]['citations']) == 2


def test_unrelated_topics_do_not_merge():
    """Wrongly merging loses a question silently, which is worse than showing
    a near-duplicate a human can spot."""
    from agents.content import score
    merged = score.merge_sources(
        [{'topic': 'Does insurance cover hail damage', 'source_names': ['reddit']}],
        [{'topic': 'Best gutter guards for pine needles', 'source_names': ['gdelt']}])
    assert len(merged) == 2


def test_corroboration_across_sources_raises_the_score():
    """The payoff for wiring free sources: one topic in three sources beats
    three topics in one. This bonus was dead code until now."""
    from agents.content import score
    one = score._score({'topic': 'hail roof insurance claim',
                        'source_names': ['reddit']})
    three = score._score({'topic': 'hail roof insurance claim',
                          'source_names': ['reddit', 'gdelt', 'perplexity']})
    assert three > one


def test_source_count_alone_cannot_dominate():
    """Three thin mentions is not a strong signal."""
    from agents.content import score
    thin = score._score({'topic': 'weather',
                         'source_names': ['a', 'b', 'c', 'd', 'e', 'f']})
    rich = score._score({'topic': 'hail storm roof shingle insurance claim '
                                  'adjuster deductible estimate inspection',
                         'source_names': ['reddit']})
    assert rich > thin


def test_a_colorado_local_topic_outranks_a_national_one():
    from agents.content import score
    local = score._score({'topic': 'roof hail claim', 'is_local': True,
                          'source_names': ['reddit']})
    national = score._score({'topic': 'roof hail claim', 'is_local': False,
                             'source_names': ['reddit']})
    assert local > national


def test_rank_merged_is_sorted_and_deduplicated():
    from agents.content import score
    ranked = score.rank_merged(
        [{'topic': 'kitchen worktops', 'source_names': ['reddit']}],
        [{'topic': 'hail damage roof insurance claim adjuster',
          'source_names': ['reddit'], 'is_local': True}],
        [{'topic': 'hail damage roof insurance claim adjuster deductible',
          'source_names': ['gdelt']}])
    assert ranked[0]['score'] > ranked[-1]['score']
    assert 'hail' in ranked[0]['topic']
    assert set(ranked[0]['source_names']) == {'reddit', 'gdelt'}


# ── The listen pass ──────────────────────────────────────────────────────────

def test_listen_reports_which_sources_were_available(monkeypatch):
    from agents.content import listen
    status = listen.sources_status()
    assert set(status) == {'perplexity', 'reddit', 'gdelt'}
    assert status['gdelt']['cost'] == 'free'


def test_listen_survives_every_source_failing(monkeypatch):
    """A thin week must be distinguishable from a broken one, and neither
    should raise."""
    from agents.content import listen
    from agents.content.sources import news_gdelt, reddit
    monkeypatch.setattr(reddit, 'pull', lambda **k: {'posts': [], 'available': False,
                                                     'note': 'not configured'})
    monkeypatch.setattr(news_gdelt, 'pull', lambda **k: {'articles': [], 'available': True,
                                                         'note': 'rate limited'})
    out = listen.run(use_paid=False)
    assert out['topics'] == []
    assert 'reddit' in out['note'] and 'gdelt' in out['note']


def test_listen_free_only_makes_no_paid_call(monkeypatch):
    from agents.content import listen
    from agents.content.sources import news_gdelt, perplexity_synth, reddit

    def boom(*a, **k):
        raise AssertionError('paid source called during a free-only pass')

    monkeypatch.setattr(perplexity_synth, 'pull', boom)
    monkeypatch.setattr(reddit, 'pull', lambda **k: {'posts': [], 'available': False,
                                                     'note': 'off'})
    monkeypatch.setattr(news_gdelt, 'pull', lambda **k: {'articles': [], 'available': True,
                                                         'note': 'none'})
    out = listen.run(use_paid=False)
    assert out['cost_usd'] == 0.0
