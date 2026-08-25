"""The sales funnel join — the one place the estimator and the CRM agree.

The two halves of the funnel used to be unjoinable. The CRM knew a lead had
reached `estimate_presented`; the estimator knew an estimate had been sent,
viewed and signed; and nothing connected the two, so the only question that
matters — of the doors we knocked, where do we actually lose people — had no
answer in either app. `leads.estimate_id` existed as a column and was never
written by anything.

This module is that join. One row per estimate, holding the CRM lead it came
from and the furthest state the estimate has reached. The estimator writes;
the CRM reads and drains. It lives in PORTAL_DATA_DIR next to portal.db for
the same reason `users.py` does: the apps keep separate databases (the CRM's
SQLite, the estimator's Postgres-or-JSON), so anything genuinely shared needs
a home that belongs to neither.

**States only ever move forward.** `_RANK` orders them and `record()` refuses a
regression. That is what makes the CRM's auto-advance safe to re-run: a
replayed or out-of-order event cannot walk a signed estimate back to `sent`,
and a rep who deliberately moved a lead backwards is not overruled by an event
that was already applied once.

**`pending` is the CRM's work queue.** It is set when a state actually changes
and cleared by `claim_pending()`, which uses the same conditional-UPDATE-and-
check-rowcount claim the Nimbus scheduler uses — two gunicorn workers can both
drain and exactly one wins each row.
"""
import os
import sqlite3
from datetime import datetime

from portal import dbtune

# The estimate's own ladder, in order. Anything not listed ranks 0 and can
# therefore never displace a real state.
STATES = ('draft', 'sent', 'viewed', 'signed', 'lost')
_RANK = {s: i for i, s in enumerate(STATES)}

# An estimate ends up signed or lost, never both, and they are equally
# terminal. Leaving `lost` ranked above `signed` would let a stray loss
# overwrite a signature, so they share a rank and the terminal guard in
# record() settles which one sticks — the first to arrive.
_RANK['lost'] = _RANK['signed']
# `declined` is the old name for `lost` and still arrives from anything not yet
# updated. Accepted as an alias rather than rejected.
_RANK['declined'] = _RANK['signed']

TERMINAL = ('signed', 'lost', 'declined')

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

_initialized = set()


def db_path():
    """Resolved per call, not frozen at import — same reason as users.py."""
    data_dir = os.environ.get('PORTAL_DATA_DIR') or _REPO_ROOT
    return os.path.join(data_dir, 'portal.db')


def get_db():
    path = db_path()
    if path not in _initialized:
        _init(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return dbtune.tune(conn)


def _init(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = dbtune.tune(sqlite3.connect(path))
    try:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS estimate_links (
                estimate_id TEXT PRIMARY KEY,
                lead_id     TEXT DEFAULT '',
                contact_id  TEXT DEFAULT '',
                rep         TEXT DEFAULT '',
                state       TEXT NOT NULL DEFAULT 'draft',
                value       REAL DEFAULT 0,
                share_token TEXT DEFAULT '',
                sent_at     TEXT DEFAULT '',
                viewed_at   TEXT DEFAULT '',
                signed_at   TEXT DEFAULT '',
                pending     INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS est_link_lead_idx    ON estimate_links(lead_id);
            CREATE INDEX IF NOT EXISTS est_link_contact_idx ON estimate_links(contact_id);
            CREATE INDEX IF NOT EXISTS est_link_pending_idx ON estimate_links(pending);
        ''')
        conn.commit()
    finally:
        conn.close()
    _initialized.add(path)


def reset_cache():
    """Forget which paths are initialized — for tests that swap PORTAL_DATA_DIR."""
    _initialized.clear()


def _now():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _row(r):
    return dict(r) if r is not None else None


def record(estimate_id, state='draft', lead_id=None, contact_id=None, rep=None,
           value=None, at=None, share_token=None):
    """Upsert an estimate's funnel state. Returns the stored row.

    Every argument except `estimate_id` is optional, and `None` means "leave
    whatever is there" — the estimator calls this from several places and only
    the signing path knows the signature time, only the handoff knows the lead.

    A state at or below the one already stored updates the other fields but
    does not move the state and does not set `pending`.
    """
    estimate_id = (estimate_id or '').strip()
    if not estimate_id:
        raise ValueError('estimate_id required')
    state = (state or 'draft').strip() or 'draft'
    at = at or _now()

    db = get_db()
    try:
        cur = db.execute('SELECT * FROM estimate_links WHERE estimate_id=?',
                         (estimate_id,)).fetchone()
        if cur is None:
            db.execute(
                'INSERT INTO estimate_links (estimate_id, lead_id, contact_id, rep,'
                ' state, value, share_token, sent_at, viewed_at, signed_at, pending,'
                ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (estimate_id, lead_id or '', contact_id or '', rep or '', state,
                 value or 0, share_token or '',
                 at if state == 'sent' else '',
                 at if state == 'viewed' else '',
                 at if state in TERMINAL else '',
                 1 if _RANK.get(state, 0) > 0 else 0, at, at))
        else:
            old = cur['state']
            forward = (_RANK.get(state, 0) > _RANK.get(old, 0)
                       and old not in TERMINAL)
            new_state = state if forward else old
            db.execute(
                'UPDATE estimate_links SET lead_id=?, contact_id=?, rep=?, state=?,'
                ' value=?, share_token=?, sent_at=?, viewed_at=?, signed_at=?,'
                ' pending=?, updated_at=? WHERE estimate_id=?',
                (lead_id if lead_id is not None else cur['lead_id'],
                 contact_id if contact_id is not None else cur['contact_id'],
                 rep if rep is not None else cur['rep'],
                 new_state,
                 value if value is not None else cur['value'],
                 share_token if share_token is not None else cur['share_token'],
                 at if (forward and state == 'sent') else cur['sent_at'],
                 at if (forward and state == 'viewed') else cur['viewed_at'],
                 at if (forward and state in TERMINAL) else cur['signed_at'],
                 1 if forward else cur['pending'],
                 at, estimate_id))
        db.commit()
        return _row(db.execute('SELECT * FROM estimate_links WHERE estimate_id=?',
                               (estimate_id,)).fetchone())
    finally:
        db.close()


def link(estimate_id, lead_id='', contact_id='', rep=''):
    """Attach an estimate to a CRM lead without asserting anything about state."""
    return record(estimate_id, state='draft', lead_id=lead_id,
                  contact_id=contact_id, rep=rep)


def get(estimate_id):
    db = get_db()
    try:
        return _row(db.execute('SELECT * FROM estimate_links WHERE estimate_id=?',
                               ((estimate_id or '').strip(),)).fetchone())
    finally:
        db.close()


def for_lead(lead_id):
    """Every estimate attached to a lead, newest first."""
    lead_id = (lead_id or '').strip()
    if not lead_id:
        return []
    db = get_db()
    try:
        return [dict(r) for r in db.execute(
            'SELECT * FROM estimate_links WHERE lead_id=? ORDER BY created_at DESC',
            (lead_id,)).fetchall()]
    finally:
        db.close()


def for_contact(contact_id):
    """Every estimate attached to a Base44 contact, newest first.

    The fallback join for estimates started straight from the estimator, where
    the rep picked the contact rather than arriving from a CRM lead.
    """
    contact_id = (contact_id or '').strip()
    if not contact_id:
        return []
    db = get_db()
    try:
        return [dict(r) for r in db.execute(
            'SELECT * FROM estimate_links WHERE contact_id=? ORDER BY created_at DESC',
            (contact_id,)).fetchall()]
    finally:
        db.close()


def claim_pending(limit=200):
    """Take ownership of unprocessed state changes. Returns the claimed rows.

    The conditional UPDATE is the claim: with two gunicorn workers draining at
    once, `rowcount` is 1 for exactly the worker that won the row and 0 for the
    other, so an event is never applied to a lead twice.
    """
    claimed = []
    db = get_db()
    try:
        candidates = db.execute(
            'SELECT * FROM estimate_links WHERE pending=1 ORDER BY updated_at LIMIT ?',
            (max(1, int(limit)),)).fetchall()
        for c in candidates:
            cur = db.execute(
                'UPDATE estimate_links SET pending=0 WHERE estimate_id=? AND pending=1',
                (c['estimate_id'],))
            if cur.rowcount == 1:
                claimed.append(dict(c))
        db.commit()
    finally:
        db.close()
    return claimed
