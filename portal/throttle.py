"""Failed-login throttling for the one login route the whole site shares.

Before this, `/login` accepted unlimited attempts. Two things made that worse
than the usual case:

  * werkzeug's password hash is deliberately expensive (~50-100 ms of CPU per
    check), and the site runs **two** gunicorn workers, so a few hundred
    guesses per second is not just a credential-stuffing path — it is a denial
    of service against the reps trying to work.
  * One login now reaches the estimator, the CRM, and the canvasser. A single
    guessed rep password reads every lead and every signed contract that rep
    can see.

State lives in portal.db rather than process memory, for two reasons that both
bite in production: the two workers would otherwise each keep their own counter
and hand out double the allowance, and every deploy would reset the count —
which is roughly "no throttle at all" for a patient attacker.

Two independent counters, because they catch different attacks:

  * **per username** — someone guessing one rep's password. Tight limit.
  * **per IP** — someone spraying one common password across many usernames,
    which never trips a per-username counter. Looser limit, because a crew
    sharing an office network shares an IP and a whole team fat-fingering
    their passwords on phones must not lock each other out.

Lockouts escalate: each one that lands while the previous count is still alive
doubles the wait, up to MAX_LOCK_SECONDS. That way an honest rep who mistyped
twice waits seconds, while a script grinding away gets a wait that grows faster
than it can work.

Deliberately NOT here: throttling of the password-change routes. Both require
an authenticated session and the current password, so an attacker who can reach
them already has what the throttle would protect.
"""
import os
from datetime import datetime, timedelta

from portal.users import get_db

# Failures allowed before the first lockout.
USER_MAX_FAILS = 8
IP_MAX_FAILS = 30

# Failures older than this stop counting, so a rep who mistyped once last
# Tuesday starts today with a clean slate.
WINDOW = timedelta(minutes=30)

# First lockout, and the ceiling escalation can reach.
BASE_LOCK_SECONDS = 15 * 60
MAX_LOCK_SECONDS = 60 * 60

_TS = '%Y-%m-%dT%H:%M:%SZ'

_initialized = False


def _now():
    return datetime.utcnow()


def _fmt(dt):
    return dt.strftime(_TS)


def _parse(raw):
    try:
        return datetime.strptime(raw, _TS)
    except (TypeError, ValueError):
        return None


def enabled():
    """Off only when explicitly disabled — a test suite that wants to try many
    bad passwords in a row sets this, production never does."""
    return os.environ.get('PORTAL_DISABLE_LOGIN_THROTTLE', '').strip().lower() \
        not in ('1', 'true', 'yes')


def _ensure_table(db):
    global _initialized
    if _initialized:
        return
    db.executescript('''
        CREATE TABLE IF NOT EXISTS login_attempts (
            scope        TEXT PRIMARY KEY,
            fails        INTEGER DEFAULT 0,
            lock_seconds INTEGER DEFAULT 0,
            first_at     TEXT NOT NULL,
            last_at      TEXT NOT NULL,
            locked_until TEXT DEFAULT ''
        );
    ''')
    _initialized = True


def reset_cache():
    """Forget that the table exists — for tests that swap PORTAL_DATA_DIR."""
    global _initialized
    _initialized = False


# Bucket for attempts whose client address we cannot see. Railway sets
# X-Forwarded-For and ProxyFix turns it into request.remote_addr, so in
# production this is unused — but an absent header must not mean "no per-IP
# throttle at all", which is what a falsy check here would silently do. Every
# such attempt shares one counter: fail closed, not open.
UNKNOWN_IP = 'unknown'


def _scopes(username, ip):
    """The counters a single attempt touches, each with its own failure budget.

    The IP scope is always present — see UNKNOWN_IP. The username scope is not,
    because an attempt with no username never reaches the password check.
    """
    out = []
    if username:
        out.append((f'user:{username}', USER_MAX_FAILS))
    out.append((f'ip:{ip or UNKNOWN_IP}', IP_MAX_FAILS))
    return out


def retry_after(username, ip):
    """Seconds the caller must wait, or 0 when the attempt may proceed.

    Checked before the password is verified, so a locked-out scope never spends
    a hash. Returns the longest wait across the scopes involved.
    """
    if not enabled():
        return 0
    now = _now()
    worst = 0
    with get_db() as db:
        _ensure_table(db)
        _prune(db, now)
        for scope, _ in _scopes(username, ip):
            row = db.execute('SELECT locked_until FROM login_attempts WHERE scope=?',
                             (scope,)).fetchone()
            if not row or not row['locked_until']:
                continue
            until = _parse(row['locked_until'])
            if until and until > now:
                worst = max(worst, int((until - now).total_seconds()) + 1)
    return worst


def record_failure(username, ip):
    """Count one failed attempt against every scope, locking those that overflow.

    Returns the seconds to wait if this attempt tripped a lock, else 0.
    """
    if not enabled():
        return 0
    now = _now()
    stamp = _fmt(now)
    worst = 0
    with get_db() as db:
        _ensure_table(db)
        for scope, budget in _scopes(username, ip):
            row = db.execute('SELECT * FROM login_attempts WHERE scope=?',
                             (scope,)).fetchone()
            fails, lock_seconds = 1, 0
            if row:
                first = _parse(row['first_at'])
                # A stale run of failures is forgotten, but an existing lock's
                # escalation is not — otherwise waiting out one lockout resets
                # the attacker to a fresh full budget every time.
                if first and now - first <= WINDOW:
                    fails = int(row['fails'] or 0) + 1
                lock_seconds = int(row['lock_seconds'] or 0)
            if fails >= budget:
                lock_seconds = min(max(lock_seconds * 2, BASE_LOCK_SECONDS),
                                   MAX_LOCK_SECONDS)
                locked_until = _fmt(now + timedelta(seconds=lock_seconds))
                fails = 0                      # next lock needs a fresh run
                worst = max(worst, lock_seconds)
            else:
                locked_until = ''
            first_at = stamp if fails <= 1 else (row['first_at'] if row else stamp)
            db.execute(
                'INSERT INTO login_attempts (scope, fails, lock_seconds, first_at,'
                ' last_at, locked_until) VALUES (?,?,?,?,?,?)'
                ' ON CONFLICT(scope) DO UPDATE SET fails=excluded.fails,'
                ' lock_seconds=excluded.lock_seconds, first_at=excluded.first_at,'
                ' last_at=excluded.last_at, locked_until=excluded.locked_until',
                (scope, fails, lock_seconds, first_at, stamp, locked_until))
    return worst


def clear(username, ip):
    """Wipe the counters after a successful sign-in.

    Both scopes, so a rep who finally got it right also un-penalizes the office
    IP they share.
    """
    scopes = [s for s, _ in _scopes(username, ip)]
    if not scopes:
        return
    with get_db() as db:
        _ensure_table(db)
        db.execute('DELETE FROM login_attempts WHERE scope IN (%s)'
                   % ','.join('?' * len(scopes)), scopes)


def unlock_user(username):
    """Clear one username's lockout, whatever IP it came from.

    The release valve for the obvious operational cost of a lockout: a rep
    standing in a driveway with a customer waiting should not have to sit out 15
    minutes because they fumbled a phone keyboard. Deliberately username-only —
    an admin should not be able to clear a whole IP's spray counter by accident.
    """
    username = (username or '').strip().lower()
    if not username:
        return False
    with get_db() as db:
        _ensure_table(db)
        cur = db.execute('DELETE FROM login_attempts WHERE scope=?',
                         (f'user:{username}',))
        return bool(cur.rowcount)


def locked_usernames():
    """`{username: seconds_remaining}` for every currently locked account, so the
    admin panel can show who is stuck instead of making Luke guess."""
    now = _now()
    out = {}
    with get_db() as db:
        _ensure_table(db)
        rows = db.execute("SELECT scope, locked_until FROM login_attempts"
                          " WHERE locked_until != '' AND scope LIKE 'user:%'").fetchall()
    for r in rows:
        until = _parse(r['locked_until'])
        if until and until > now:
            out[r['scope'][len('user:'):]] = int((until - now).total_seconds()) + 1
    return out


def reset_all():
    """Drop every counter. For tests, which share one IP across the whole suite
    and would otherwise trip the per-IP budget on unrelated cases."""
    with get_db() as db:
        _ensure_table(db)
        db.execute('DELETE FROM login_attempts')


def _prune(db, now):
    """Drop rows that are neither locked nor inside the counting window, so the
    table stays a handful of rows instead of one per IP that ever guessed."""
    cutoff = _fmt(now - WINDOW)
    db.execute("DELETE FROM login_attempts WHERE last_at < ?"
               " AND (locked_until = '' OR locked_until < ?)", (cutoff, _fmt(now)))
