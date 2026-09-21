"""The company's clock — `portal/clock.py`.

Two kinds of test here and the distinction matters. Most pin the behaviour:
which month a stamp lands in, where a Colorado day starts and ends in UTC,
what 7am Denver is in January and in July. Those are the fixes.

The first one pins something else. Every function in `clock` falls back to UTC
when the tz database is missing — deliberately, because a slim image must not
take the site down — and that fallback is invisible. Without this test a
Railway image that dropped `tzdata` would make every timezone fix in this repo
a silent no-op, and the whole suite would stay green.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from portal import clock


def test_the_tz_database_is_actually_present():
    """The one test that guards the other twelve.

    `clock` degrades to UTC rather than raising, so a missing tz database looks
    exactly like a working one from inside any other test. `tzdata` is pinned in
    requirements.txt; this is what notices when it stops being installed.
    """
    assert clock.available(), (
        'No tz database in this environment, so every clock function is '
        'silently answering in UTC and every timezone fix built on it is a '
        'no-op. Install tzdata (it is pinned in requirements.txt).')


# ── month_of: the money one ────────────────────────────────────────────────

def test_a_roof_signed_at_7pm_on_the_30th_counts_for_that_month():
    """7pm Mountain on 30 September is 01:00Z on 1 October.

    Slicing seven characters off the stored string files it under October: off
    September's revenue, off that rep's September number, and onto a month they
    had not started selling. Month end is exactly when reps push to close, so
    this is not a rare row.
    """
    assert clock.month_of('2026-10-01T01:00:00Z') == '2026-09'


def test_a_daytime_stamp_lands_in_the_month_it_reads():
    assert clock.month_of('2026-09-15T18:30:00Z') == '2026-09'


def test_an_unreadable_stamp_has_no_month():
    """'' is what callers already treat as "not in any month". Guessing one
    would put a broken row into a real month's revenue."""
    assert clock.month_of('') == ''
    assert clock.month_of(None) == ''
    assert clock.month_of('not a date') == ''


def test_day_of_crosses_the_same_boundary():
    assert clock.day_of('2026-10-01T01:00:00Z') == '2026-09-30'


# ── parse_utc: four apps have written these ────────────────────────────────

@pytest.mark.parametrize('stamp', [
    '2026-09-15T18:30:00Z',
    '2026-09-15T18:30:00',
    '2026-09-15T18:30:00+00:00',
    '2026-09-15T18:30:00.123456Z',
    '2026-09-15T18:30:00.123456',
])
def test_every_spelling_the_four_apps_write_is_readable(stamp):
    dt = clock.parse_utc(stamp)
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 9, 15, 18)
    assert dt.tzinfo is not None


def test_a_naive_stamp_is_read_as_utc_not_as_local():
    """Everything stored is UTC. Reading a naive stamp as Colorado time would
    shift it six hours and put some of them in the wrong day."""
    assert clock.parse_utc('2026-09-15T18:30:00') == \
        datetime(2026, 9, 15, 18, 30, tzinfo=timezone.utc)


# ── Day boundaries: Colorado's boundary, UTC's value ───────────────────────

def test_the_day_boundaries_are_utc_strings_sql_can_compare():
    """These go straight into `WHERE due_at <= ?` beside columns nobody is
    migrating, so they have to be spelled the way those columns are."""
    for s in (clock.start_of_today_utc(), clock.end_of_today_utc()):
        assert s.endswith('Z')
        assert clock.parse_utc(s) is not None


def test_today_starts_before_it_ends_and_spans_a_day():
    start = clock.parse_utc(clock.start_of_today_utc())
    end = clock.parse_utc(clock.end_of_today_utc())
    assert start < end
    # 23, 24 or 25 hours — the two DST days a year are short and long.
    assert timedelta(hours=22) < (end - start) < timedelta(hours=26)


def test_the_day_boundary_is_colorados_midnight_not_utcs():
    start = clock.to_company(clock.start_of_today_utc())
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert start.date() == clock.company_today()


def test_end_of_today_is_built_off_tomorrow_so_dst_cannot_shift_it():
    """1 November 2026 is 25 hours long in Colorado. Spelling the end of the
    day as 23:59:59 of today would leave the last hour of it outside "today"."""
    dst_day = date(2026, 11, 1)
    start = clock.parse_utc(clock._utc_iso(clock._start_of(dst_day)))
    end = clock.parse_utc(
        clock._utc_iso(clock._start_of(dst_day + timedelta(days=1))
                       - timedelta(seconds=1)))
    assert (end - start) == timedelta(hours=25, seconds=-1)


def test_days_ago_reaches_back_whole_colorado_days():
    start = clock.parse_utc(clock.start_of_today_utc())
    week = clock.parse_utc(clock.days_ago_utc(7))
    assert timedelta(days=6, hours=22) < (start - week) < timedelta(days=7, hours=2)


# ── at_hour_utc: what a fixed offset cannot do ─────────────────────────────

def test_7am_denver_is_1pm_utc_in_summer_and_2pm_in_winter():
    """`replace(hour=13)  # ~7am Denver` was right for eight months of the year.
    A task queued at 7am winter-time under that rule landed at 6am — before the
    rep is up, on a board they check once."""
    assert clock.at_hour_utc(7, date(2026, 7, 15)) == '2026-07-15T13:00:00Z'
    assert clock.at_hour_utc(7, date(2027, 1, 15)) == '2027-01-15T14:00:00Z'


def test_at_hour_defaults_to_today():
    assert clock.at_hour_utc(7).startswith(clock.company_today().isoformat())


# ── The two copies this module replaced ────────────────────────────────────

def test_the_estimator_and_nimbus_read_the_same_clock():
    """Two independent copies of this logic, written days apart, is how two
    answers to one question start to drift. Both now call this module."""
    import estimator.app as A
    from agents import events

    assert A._company_today() == clock.company_today()
    assert events.company_today() == clock.company_today()
