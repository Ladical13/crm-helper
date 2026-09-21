"""Networking events, ranked by who is actually in the room.

A list of events near Fort Collins is a Google search. What makes this worth
running is that Nimbus knows which partner segments the CRM is thin on, so it
can say the insurance mixer is worth an evening and the fortieth realtor coffee
is not. These tests hold down that ranking and the four ways the feature fails
quietly if nobody is watching.
"""
from datetime import date, timedelta

import pytest

from agents import events as E


TODAY = date(2026, 9, 21)


def _raw(**kw):
    row = {'name': 'Chamber Business After Hours',
           'host': 'Fort Collins Area Chamber of Commerce',
           'date': '2026-10-02', 'time': '17:30', 'city': 'Fort Collins',
           'venue': 'The Elizabeth', 'url': 'https://example.test/events/1',
           'cost': 'Free', 'summary': 'Realtors and insurance agents attend.'}
    row.update(kw)
    return row


def _norm(*raws, city='Fort Collins', today=TODAY):
    return E.normalize_all({'events': list(raws)}, city=city, today=today)


# ── What is allowed in ───────────────────────────────────────────────────────

def test_an_event_with_no_date_is_not_an_event():
    """It is a webpage. A row that cannot go in a calendar cannot be acted on,
    and — worse — a row with no date can never expire, so it sits at the top of
    the list forever."""
    assert _norm(_raw(date='sometime in October')) == []
    assert _norm(_raw(date='')) == []


def test_an_event_with_no_url_is_dropped():
    """The citation is the defence against a model inventing a chamber
    breakfast, and an event Luke cannot click through to register for is not
    one he is going to."""
    assert _norm(_raw(url='')) == []
    assert _norm(_raw(url='call the chamber')) == []


def test_a_past_event_never_enters():
    assert _norm(_raw(date='2026-09-01')) == []


def test_a_far_future_event_is_dropped():
    """Past the horizon the listings thin out and the answers drift into
    "the chamber usually does something in spring"."""
    assert _norm(_raw(date='2027-06-01')) == []


def test_the_same_event_found_twice_is_one_event():
    """Two sources describe one event with two links, and a monthly re-run
    must not stack a second copy of every recurring meeting."""
    rows = _norm(_raw(), _raw(url='https://other.test/x', host='Someone else'))
    assert len(rows) == 1


def test_the_date_is_stored_as_local_wall_clock():
    """A 7am chamber breakfast in Fort Collins is at 7am in Fort Collins."""
    stored = _norm(_raw())[0]['starts_at']
    assert stored == '2026-10-02'
    assert not stored.endswith('Z') and '+' not in stored


def test_the_audience_is_read_from_the_host_too():
    """"Fort Collins Board of Realtors" names its audience in the host line and
    nowhere else."""
    row = _norm(_raw(name='Monthly Breakfast', host='Fort Collins Board of Realtors',
                     summary='Breakfast and announcements.'))[0]
    assert 'realtor' in row['audience']


# ── The read path is where a list decays ─────────────────────────────────────

def test_a_stored_event_disappears_the_day_after_it_happens():
    """The one nobody forgives. The table outlives the search that filled it,
    so a row stored three weeks ago is still there on the day it happens and
    the day after — and Nimbus confidently naming a meeting that was last
    Tuesday costs the page its credibility."""
    E.record(_norm(_raw(date='2026-10-02')))
    assert [e['name'] for e in E.upcoming(today=date(2026, 10, 2))]
    assert E.upcoming(today=date(2026, 10, 3)) == []


def test_skipped_events_are_hidden_unless_asked_for():
    E.record(_norm(_raw()))
    ev = E.upcoming(today=TODAY)[0]
    E.decide(ev['id'], 'skipped', 'luke')
    assert E.upcoming(today=TODAY) == []
    assert len(E.upcoming(today=TODAY, include_skipped=True)) == 1


def test_a_read_never_deletes():
    """A read that deletes behaves differently the second time it is called."""
    E.record(_norm(_raw(date='2026-10-02')))
    E.upcoming(today=date(2026, 12, 1))
    E.upcoming(today=date(2026, 12, 1))
    assert len(E.upcoming(today=TODAY, include_skipped=True)) == 1
    assert E.purge_past(today=date(2027, 6, 1)) == 1


# ── A decision is the human's, and a re-run may not undo it ──────────────────

def test_a_rerun_refreshes_the_facts():
    E.record(_norm(_raw(venue='The Elizabeth', cost='Free')))
    E.record(_norm(_raw(venue='Moved to Ginger and Baker', cost='$15')))
    rows = E.upcoming(today=TODAY)
    assert len(rows) == 1
    assert rows[0]['venue'] == 'Moved to Ginger and Baker'
    assert rows[0]['cost'] == '$15'


def test_a_rerun_never_undoes_a_decision():
    """A venue moves and a price changes; neither is a reason to un-skip an
    event Luke already looked at and passed on."""
    E.record(_norm(_raw()))
    ev = E.upcoming(today=TODAY)[0]
    E.decide(ev['id'], 'skipped', 'luke')
    E.record(_norm(_raw(venue='Somewhere else')))
    kept = E.upcoming(today=TODAY, include_skipped=True)[0]
    assert kept['decision'] == 'skipped'
    assert kept['decided_by'] == 'luke'
    assert kept['venue'] == 'Somewhere else', 'the facts should still refresh'


def test_decision_is_not_free_text():
    E.record(_norm(_raw()))
    ev = E.upcoming(today=TODAY)[0]
    with pytest.raises(ValueError):
        E.decide(ev['id'], 'maybe', 'luke')


def test_deciding_an_unknown_event_is_none_not_a_crash():
    assert E.decide(999999, 'going', 'luke') is None


# ── The ranking, which is the point ──────────────────────────────────────────

def _ev(audience, **kw):
    row = {'audience': audience, 'in_service_area': True, 'cost': 'Free'}
    row.update(kw)
    return row


def test_a_thin_segment_outranks_a_saturated_one():
    """The fortieth realtor contact this year is not worth an evening and the
    second insurance agent is. This is the whole feature."""
    counts = {'realtor': 41, 'insurance_agent': 2}
    thin, _ = E.score(_ev(['insurance_agent']), counts)
    fat,  _ = E.score(_ev(['realtor']), counts)
    assert thin > fat


def test_the_reason_names_the_thin_segment():
    """A score a human cannot argue with is a score they will not act on."""
    _, why = E.score(_ev(['insurance_agent']), {'realtor': 41, 'insurance_agent': 2})
    assert 'insurance agent' in why and '2' in why


def test_a_segment_absent_from_the_counts_is_not_reported_as_zero():
    """Absent is not empty. Scoring an unreported type as zero would invent a
    gap and then say "you have 0" about a number nobody supplied."""
    _, why = E.score(_ev(['insurance_agent', 'referral_partner']),
                     {'realtor': 41, 'insurance_agent': 2})
    assert 'referral partner' not in why.split('only')[-1]
    _, why_zero = E.score(_ev(['insurance_agent', 'referral_partner']),
                          {'realtor': 41, 'insurance_agent': 2, 'referral_partner': 0})
    assert 'referral partner' in why_zero


def test_with_no_counts_it_still_ranks_and_says_less():
    """Degrades to audience-and-area, which is the scheduler's honest output."""
    total, why = E.score(_ev(['insurance_agent']), None)
    assert total > 0
    assert 'thinnest' not in why


def test_an_event_with_no_named_audience_scores_below_one_that_has_it():
    counts = {'realtor': 10}
    assert E.score(_ev([]), counts)[0] < E.score(_ev(['realtor']), counts)[0]


def test_cost_unstated_is_not_treated_as_free():
    """"The page did not say" is not a fact about the event, and calling it
    free would flatter every event nobody priced."""
    assert E._dollars('') is None
    assert E._dollars('Free') == 0.0
    assert E._dollars('$25 members') == 25.0
    _, why = E.score(_ev(['realtor'], cost=''), {'realtor': 1})
    assert 'free' not in why


def test_an_expensive_event_only_loses_a_tiebreak():
    """A $95 dinner in front of ten property managers is still the right
    evening — cost must not outweigh the room."""
    counts = {'property_manager': 1, 'realtor': 40}
    pricey = E.score(_ev(['property_manager'], cost='$95'), counts)[0]
    cheap  = E.score(_ev(['realtor'], cost='Free'), counts)[0]
    assert pricey > cheap


def test_rescore_moves_a_scheduler_scored_event():
    """The scheduled run has no session and cannot read the CRM, so its scores
    are audience-and-area only. This is the second half."""
    E.record(_norm(_raw(name='HOA Managers Lunch', host='CAI Northern Colorado',
                        summary='community association managers')))
    blunt = E.upcoming(today=TODAY)[0]['score']
    assert E.rescore({'realtor': 41, 'hoa': 1}, today=TODAY) == 1
    assert E.upcoming(today=TODAY)[0]['score'] > blunt


def test_rescore_is_idempotent():
    E.record(_norm(_raw()))
    counts = {'realtor': 41, 'insurance_agent': 2}
    E.rescore(counts, today=TODAY)
    assert E.rescore(counts, today=TODAY) == 0


# ── The run ──────────────────────────────────────────────────────────────────

def test_one_bad_city_does_not_sink_the_others(monkeypatch):
    def flaky(city, **kw):
        if city == 'Greeley':
            raise RuntimeError('timeout')
        return _norm(_raw(name=f'{city} mixer', url=f'https://x.test/{city}'))
    monkeypatch.setattr(E, 'find', flaky)
    out = E.run(['Fort Collins', 'Greeley', 'Loveland'], today=TODAY)
    assert out['added'] == 2
    assert [e['city'] for e in out['errors']] == ['Greeley']
    assert out['ok'] is True


def test_the_spend_cap_stops_the_whole_run(monkeypatch):
    """Every remaining city would hit the same cap, and six identical failures
    is noise rather than information."""
    from agents import perplexity
    calls = []

    def capped(city, **kw):
        calls.append(city)
        raise perplexity.SpendCapReached('cap reached')
    monkeypatch.setattr(E, 'find', capped)
    out = E.run(['Fort Collins', 'Greeley', 'Loveland'], today=TODAY)
    assert calls == ['Fort Collins']
    assert out['ok'] is False and out['stopped_early']


def test_service_cities_decide_what_counts_as_local():
    rows = _norm(_raw(city='Denver'), _raw(name='Local one', city='Fort Collins',
                                           url='https://x.test/local'))
    E.record(rows, service_cities=['Fort Collins', 'Loveland'])
    by_name = {e['name']: e for e in E.upcoming(today=TODAY)}
    assert by_name['Local one']['in_service_area'] is True
    assert by_name['Chamber Business After Hours']['in_service_area'] is False


def test_with_no_service_cities_everything_counts_as_local():
    """An empty list is "not configured", not "nowhere qualifies"."""
    E.record(_norm(_raw(city='Denver')), service_cities=[])
    assert E.upcoming(today=TODAY)[0]['in_service_area'] is True


# ── Cost control ─────────────────────────────────────────────────────────────

def test_events_are_cached_far_shorter_than_the_global_default():
    """A fortnight-old answer has stale times, moved venues and events that
    already happened. The 30-day global default is wrong here by an order of
    magnitude."""
    from agents import config
    assert E.CACHE_TTL_DAYS <= 7
    assert E.CACHE_TTL_DAYS < config.DEFAULT_SETTINGS['cache_ttl_days']


def test_the_prompt_forbids_guessing_a_date():
    assert 'skip it' in E.SYSTEM.lower() or 'skip' in E.SYSTEM.lower()
    assert 'never invent' in E.SYSTEM.lower()
    assert 'YYYY-MM-DD' in E.PROMPT


def test_today_is_colorado_not_utc():
    """From 6pm Mountain, UTC is already tomorrow. The fallback can only drop
    an event EARLY, which costs an evening rather than the page's credibility."""
    assert E.COMPANY_TZ == 'America/Denver'
    assert abs((E.company_today() - date.today()).days) <= 1
