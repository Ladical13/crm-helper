"""One clock for the company, in Colorado — shared by all four apps.

The server runs in UTC. From 6pm Mountain, UTC has already rolled over, so for
the last six hours of every day a UTC clock answers "what day is it" with
tomorrow. That is not a rounding error; it is the difference between a roof
signed on the 30th counting toward September and counting toward October.

**Stored timestamps stay UTC and that is correct.** Nothing here converts
storage. `created_at`, `signed_at`, `due_at` are UTC ISO strings, they sort and
compare correctly across four apps, and in November a local-time column would
contain an hour that happens twice with no way to tell the two apart.

What this module fixes is the other thing: a DECISION about a day. Which month
did this land in, is this due today, what does "the last 30 days" mean. The
boundary is Colorado's; the value is still UTC, because that is what it gets
compared against. `start_of_today_utc()` returns the UTC instant at which
Colorado's today began — which is what makes it drop straight into SQL beside
columns nobody is migrating.

This lives in `portal/` for the reason `geo.py` and `funnel.py` do: the four
apps keep separate databases and anything genuinely shared needs a home
belonging to none of them. It replaced two independent copies —
`estimator._company_today` and `agents.events.company_today` — written days
apart, which is exactly how two answers to one question start to drift.

**A fixed UTC offset cannot do this job.** Colorado is UTC-6 on MDT and UTC-7
on MST, so `hour=13  # ~7am Denver` is true from March to November and wrong
the rest of the year. That comment was in salescrm before this module existed.

**Without the tz database every function here falls back to UTC**, silently and
on purpose — a missing package must not take the site down. `available()` is
how a test or a health check notices, because a silent fallback means every fix
built on this module quietly does nothing while all its tests still pass.
`tzdata` is pinned in requirements.txt so the fallback stays theoretical.
"""
from datetime import datetime, timedelta, timezone

COMPANY_TZ = 'America/Denver'


def _tz():
    """The company zone, or None when this image has no tz database."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(COMPANY_TZ)
    except Exception:
        return None


def available():
    """True when Colorado time is really available.

    Every other function degrades to UTC rather than raising. That is the right
    behaviour for a live request and the wrong thing to be unaware of, so this
    exists to be asserted on — `portal/tests/test_clock.py` fails the build if
    the tz database goes missing, rather than letting the whole timezone fix
    quietly become a no-op.
    """
    return _tz() is not None


def company_now():
    """Now, as an aware datetime in Colorado (or UTC if unavailable)."""
    now = datetime.now(timezone.utc)
    tz = _tz()
    return now.astimezone(tz) if tz else now


def company_today():
    """Today's date in Colorado.

    With no tz database this is UTC's date, which is never EARLIER than
    Colorado's — so anything gated on it happens early rather than late. For an
    expiring quote that means expiring a few hours soon; for an event list it
    means dropping tonight's mixer. Both are the safe direction to fail.
    """
    return company_now().date()


def company_month():
    """This month in Colorado, as 'YYYY-MM'."""
    return company_today().strftime('%Y-%m')


# ── Reading a stored UTC timestamp in Colorado terms ────────────────────────

def parse_utc(value):
    """A stored timestamp as an aware UTC datetime, or None.

    Tolerant on the way in because four apps have written these: with and
    without the trailing Z, with and without microseconds, with an explicit
    +00:00. A value this cannot read returns None rather than raising — a
    malformed stamp on one old row must not take out the report it appears in.
    """
    s = str(value or '').strip()
    if not s:
        return None
    s = s.replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def to_company(value):
    """A stored UTC timestamp, as an aware datetime in Colorado. None passes."""
    dt = parse_utc(value)
    if dt is None:
        return None
    tz = _tz()
    return dt.astimezone(tz) if tz else dt


def month_of(value):
    """Which month a stored UTC timestamp belongs to, in Colorado. 'YYYY-MM'.

    The reason this module exists. A roof signed at 7pm Mountain on 30
    September is `2026-10-01T01:00Z`, and slicing the first seven characters of
    that string files it under October — out of the month it was sold in, off
    that month's revenue, off that rep's number, and into the next month's.
    Month end is exactly when reps push to close.

    Returns '' for an unreadable stamp, which callers already treat as
    "not in any month" rather than guessing one.
    """
    dt = to_company(value)
    return dt.strftime('%Y-%m') if dt else ''


def day_of(value):
    """Which day a stored UTC timestamp belongs to, in Colorado. 'YYYY-MM-DD'."""
    dt = to_company(value)
    return dt.strftime('%Y-%m-%d') if dt else ''


# ── Colorado day boundaries, expressed in UTC ───────────────────────────────
#
# The boundary is Colorado's and the value is UTC, because every column these
# are compared against stores UTC. That split is what lets a timezone fix land
# without migrating a single row.

def _utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _start_of(day):
    tz = _tz()
    naive = datetime(day.year, day.month, day.day)
    return naive.replace(tzinfo=tz or timezone.utc)


def start_of_today_utc():
    """The UTC instant Colorado's today began. For `>= ?` in SQL."""
    return _utc_iso(_start_of(company_today()))


def end_of_today_utc():
    """The UTC instant Colorado's today ends. For `<= ?` in SQL.

    Built as the start of tomorrow minus a second rather than 23:59:59 of
    today, so the two DST days a year — one 23 hours long, one 25 — land on
    the real end of the day rather than an hour either side of it.
    """
    tomorrow = _start_of(company_today() + timedelta(days=1))
    return _utc_iso(tomorrow - timedelta(seconds=1))


def days_ago_utc(days):
    """The UTC instant the Colorado day `days` ago began. For a window start."""
    return _utc_iso(_start_of(company_today() - timedelta(days=int(days))))


def at_hour_utc(hour, day=None):
    """A wall-clock hour in Colorado, as a UTC instant.

    What `now.replace(hour=13)  # ~7am Denver` was reaching for. That is true
    on MDT and an hour out on MST, and a fixed offset can never be anything
    else; this asks the tz database and is right in both.
    """
    day = day or company_today()
    tz = _tz()
    naive = datetime(day.year, day.month, day.day, int(hour))
    return _utc_iso(naive.replace(tzinfo=tz or timezone.utc))
