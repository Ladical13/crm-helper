"""Fun posts: light, clean, never a repeat, never a sales pitch in disguise.

What must hold: a fun post goes through the SAME honesty check as every other
post plus its own guard rails; it only ever drafts for Facebook and Instagram;
the drafts land in the normal review queue marked as fun; and the weekly
lineup is Friday Funnies plus one rotating series.
"""
from datetime import date

import pytest

from agents import config, perplexity
from agents.content import fun


def _answer(body, hook='Weather Whiplash'):
    return lambda *a, **k: {'data': {'body': body, 'call_to_action': 'Tell us in the comments!',
                                     'hashtags': ['#Colorado'], 'image_prompt': 'x',
                                     'alt_text': 'x', 'hook': hook},
                            'cost_usd': 0.001}


def _drafts():
    with config.get_cache_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT platform, topic, source, review_notes FROM content_drafts ORDER BY id")]


def test_a_fun_post_lands_in_the_review_queue_marked_fun(monkeypatch):
    monkeypatch.setattr(perplexity, 'search_json',
                        _answer('Colorado forecast: sunny, snowy and windy. Before lunch.'))
    r = fun.build('friday_funnies')
    assert [p['platform'] for p in r['posts']] == ['facebook', 'instagram']
    d = _drafts()
    assert all(x['source'] == 'fun' for x in d)
    assert d[0]['topic'] == '🎉 Friday Funnies: Weather Whiplash'
    assert 'post on Friday' in d[0]['review_notes']


def test_fun_posts_never_go_to_linkedin_or_google(monkeypatch):
    monkeypatch.setattr(perplexity, 'search_json', _answer('A clean pun about gutters.'))
    r = fun.build('friday_funnies', platforms=('linkedin', 'google_business'))
    assert r['posts'] == [] and len(r['rejected']) == 2


@pytest.mark.parametrize('body', [
    'Vote before the election: shingles or metal?',
    'Nothing says Friday like a cold beer on a new roof.',
    'Pray for dry weather, folks!',
    'This roof is damn good.',
])
def test_off_limits_topics_are_dropped(monkeypatch, body):
    monkeypatch.setattr(perplexity, 'search_json', _answer(body))
    r = fun.build('friday_funnies')
    assert r['posts'] == [] and 'off-limits' in r['rejected'][0]['reason']


def test_the_honesty_guard_still_runs_on_a_joke(monkeypatch):
    monkeypatch.setattr(perplexity, 'search_json',
                        _answer('Why are we the #1 roofer in Colorado? Because we are up here all day.'))
    assert fun.build('friday_funnies')['posts'] == []


def test_a_near_repeat_of_a_recent_post_is_dropped(monkeypatch):
    monkeypatch.setattr(perplexity, 'search_json',
                        _answer('Colorado weather: four seasons before lunch again today.'))
    fun.build('friday_funnies')
    r = fun.build('friday_funnies')
    assert r['posts'] == [] and 'too close' in r['rejected'][0]['reason']


def test_recent_posts_are_passed_in_as_already_used(monkeypatch):
    monkeypatch.setattr(perplexity, 'search_json', _answer('First joke about ladders.'))
    fun.build('friday_funnies')
    seen = {}

    def capture(prompt, **k):
        seen['prompt'] = prompt
        return _answer('A different joke about snow in May.')()
    monkeypatch.setattr(perplexity, 'search_json', capture)
    fun.build('friday_funnies', platforms=('facebook',))
    assert 'First joke about ladders.' in seen['prompt']


def test_photo_series_ask_for_a_real_photo(monkeypatch):
    monkeypatch.setattr(perplexity, 'search_json', _answer('What is this? Guess below!'))
    fun.build('guess_what', platforms=('facebook',))
    assert 'Needs a real photo' in _drafts()[-1]['review_notes']


def test_weekly_lineup_is_friday_funnies_plus_a_rotation():
    weeks = {tuple(fun.weekly_series(date(2026, 1, 5) .fromordinal(date(2026, 1, 5).toordinal() + 7 * i)))
             for i in range(4)}
    assert all(w[0] == 'friday_funnies' for w in weeks)
    assert {w[1] for w in weeks} == set(fun.WEEKLY_ROTATION)
