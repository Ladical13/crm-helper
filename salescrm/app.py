"""
Project One — Sales CRM ("The Pipeline")
========================================
A sales-driven CRM that sits at the top of the funnel. Reps manage their own
pipeline; managers coach off the numbers. Hands off downstream:

  • The Den (Base44): moving a lead to "won" auto-creates a Contact + Project.
  • Estimator: the shared Base44 contact_id is the join key, so a lead that
    reaches a full estimate links up and its status reads back here.

Mirrors the canvasser app: Flask + SQLite + PWA. Storage is a single SQLite
file (DATA_DIR/salescrm.db). No pricing math lives here. Identity is NOT here
either — the portal owns login and the user table (see portal/users.py); this
app only reads who the session says you are.
"""
import os
import sys
import json
import uuid
import sqlite3
import threading
import time
from datetime import datetime, timedelta, date
from functools import wraps
from urllib.parse import quote

from flask import Flask, request, jsonify, send_from_directory, session

# The portal package lives one directory up. Put the repo root on the path so
# this app works both mounted by portal/wsgi.py and run standalone (its test
# suite imports app.py directly with the repo root nowhere in sight).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from portal import dbtune                # noqa: E402
from portal import funnel as pfunnel     # noqa: E402
from portal import lost_reasons as plost  # noqa: E402
from portal import mail as pmail          # noqa: E402
from portal import geo as pgeo            # noqa: E402
from hail import join as hjoin            # noqa: E402
from hail import storms as hstorms        # noqa: E402
from portal import session as psession   # noqa: E402
from portal import users as pusers       # noqa: E402

try:
    import requests as http
except ImportError:
    http = None

app = Flask(__name__, static_folder='static')
# Secret key, ProxyFix, and cookie settings identical to the other three apps.
# They share one cookie and each re-saves it whenever it touches session, so a
# mismatched flag here logs the rep out of all of them at random.
psession.configure(app)

HERE         = os.path.dirname(os.path.abspath(__file__))
BASE44_TOKEN = os.environ.get('BASE44_TOKEN', '')
BASE44_URL   = 'https://base44.app/api/apps/69320ef0c647fee442697971'
# The Den scopes every Colorado report to this location, and it is chosen from a
# dropdown when a job is created in its own UI — so anything created through the
# API without it is invisible to the estimator's contact search and to every
# executive-team skill, which all filter on it. Sending it is a guess we can
# make safely: if the API ignores the field the job still lands, and the only
# cost is that someone sets the location by hand, exactly as they do today.
CO_LOCATION_ID = os.environ.get('CO_LOCATION_ID', '6984bb86d86d9c92d6827a17')
# Deep-link target for "Start estimate". Same origin now that the estimator is
# mounted at /estimate, so the rep keeps their session and their tab.
ESTIMATOR_URL = os.environ.get('ESTIMATOR_URL', '/estimate')
EMAIL_DOMAIN  = pusers.EMAIL_DOMAIN
# SALESCRM_DATA_DIR must be set explicitly under the portal: the DATA_DIR
# fallback is the estimator's volume, so leaving it unset drops salescrm.db
# into the estimator's directory.
DATA_DIR      = os.environ.get('SALESCRM_DATA_DIR', os.environ.get('DATA_DIR', HERE))
DB_PATH       = os.path.join(DATA_DIR, 'salescrm.db')

# A deal with no activity for this many days is "stalled" — a coaching cue.
STALL_DAYS = int(os.environ.get('SALESCRM_STALL_DAYS', '5'))

# ── Domain config (PIN_TYPES-style; drives kanban + funnel math) ──────────────

# Ordered pipeline. `won`/`lost` are terminal. `open` flags stages still in play.
STAGES = [
    {'key': 'new',                'label': 'New Lead',          'color': '#6B7280', 'open': True},
    {'key': 'contacted',          'label': 'Contacted',         'color': '#3B82F6', 'open': True},
    {'key': 'appt_set',           'label': 'Appt Set',          'color': '#8B5CF6', 'open': True},
    {'key': 'inspected',          'label': 'Inspected',         'color': '#F97316', 'open': True},
    {'key': 'estimate_presented', 'label': 'Estimate Presented','color': '#EAB308', 'open': True},
    {'key': 'follow_up',          'label': 'Follow-up',         'color': '#F59E0B', 'open': True},
    {'key': 'won',                'label': 'Won',               'color': '#10B981', 'open': False},
    {'key': 'lost',               'label': 'Lost',              'color': '#EF4444', 'open': False},
]
STAGE_KEYS  = [s['key'] for s in STAGES]
STAGE_META  = {s['key']: s for s in STAGES}
OPEN_STAGES = [s['key'] for s in STAGES if s['open']]

LEAD_TYPES = [
    {'key': 'homeowner',         'label': 'Homeowner',         'partner': False},
    {'key': 'realtor',           'label': 'Realtor',           'partner': True},
    {'key': 'hoa',               'label': 'HOA',               'partner': True},
    {'key': 'insurance_agent',   'label': 'Insurance Agent',   'partner': True},
    {'key': 'property_manager',  'label': 'Property Manager',  'partner': True},
    {'key': 'adjuster',          'label': 'Adjuster',          'partner': True},
    {'key': 'commercial',        'label': 'Commercial',        'partner': False},
    {'key': 'referral_partner',  'label': 'Referral Partner',  'partner': True},
    # Nimbus segments — cold outreach targets seeded by the AI agents.
    {'key': 'church',            'label': 'Church',            'partner': False},
    {'key': 'school',            'label': 'School',            'partner': False},
    {'key': 'gc',                'label': 'General Contractor','partner': True},
    # A principal does not buy a roof — the district facilities director does,
    # and one of them can be responsible for forty buildings. Rolling schools
    # up to their district turned 116 cold cards into 7 real accounts.
    {'key': 'school_district',   'label': 'School District',   'partner': False},
]
LEAD_TYPE_KEYS = [t['key'] for t in LEAD_TYPES]
PARTNER_TYPES  = [t['key'] for t in LEAD_TYPES if t['partner']]

SOURCES     = ['referral', 'door_knock', 'phone_call', 'website', 'social_media', 'storm',
               'existing_customer', 'other']
TEMPERATURE = ['hot', 'warm', 'cold']

# Service lines. Every lead is a deal for ONE service; pitching a second service
# to the same customer creates a second lead (see the clone flow in app.js).
SERVICES = [
    {'key': 'roofing',              'label': 'Roofing',              'icon': '🏠'},
    {'key': 'window_cleaning',      'label': 'Window Cleaning',      'icon': '🪟'},
    {'key': 'exterior_maintenance', 'label': 'Exterior Maintenance', 'icon': '🏡'},
]
SERVICE_KEYS = [s['key'] for s in SERVICES]
SERVICE_META = {s['key']: s for s in SERVICES}

# Recurring billing. '' = one-time job. months-per-period drives MRR normalization
# (a won recurring deal is an ACTIVE PLAN — its monthly value counts toward MRR).
BILLING_KEYS   = ['', 'monthly', 'quarterly', 'annual']
BILLING_MONTHS = {'monthly': 1, 'quarterly': 3, 'annual': 12}
BILLING_SUFFIX = {'monthly': '/mo', 'quarterly': '/qtr', 'annual': '/yr'}

# Which local activity kinds count as "outreach" for scorecards.
OUTREACH_KINDS = ('call', 'text', 'email', 'door', 'meeting')

# ── Contact normalization ─────────────────────────────────────────────────────
# Defined up here rather than beside the other lead helpers because migrate_db()
# backfills with them, and that runs at import time before those are bound.
#
# These exist so the bulk prospect importer can dedupe. Nothing else compares
# contact details: the Base44 dedup in _find_existing_contact() does its own
# exact-string match against the Den, and deliberately stays that way.

def _norm_phone(s):
    """Digits only, US country code dropped. '' when it can't be a real number.

    '(970) 555-1212', '970-555-1212' and '+1 970 555 1212' all collapse to
    '9705551212' so the importer sees one contact, not three.
    """
    digits = ''.join(c for c in (s or '') if c.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits if len(digits) == 10 else ''

def _norm_email(s):
    return (s or '').strip().lower()

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return dbtune.tune(conn)

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                username   TEXT PRIMARY KEY,
                pw_hash    TEXT NOT NULL,
                is_admin   INTEGER DEFAULT 0,
                role       TEXT DEFAULT 'rep',
                full_name  TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invites (
                code       TEXT PRIMARY KEY,
                username   TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_by    TEXT DEFAULT '',
                used_at    TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS leads (
                id             TEXT PRIMARY KEY,
                lead_type      TEXT NOT NULL DEFAULT 'homeowner',
                service        TEXT NOT NULL DEFAULT 'roofing',
                plan           TEXT DEFAULT '',
                billing        TEXT DEFAULT '',
                first_name     TEXT DEFAULT '',
                last_name      TEXT DEFAULT '',
                company        TEXT DEFAULT '',
                phone          TEXT DEFAULT '',
                email          TEXT DEFAULT '',
                address        TEXT DEFAULT '',
                city           TEXT DEFAULT '',
                state          TEXT DEFAULT '',
                zip            TEXT DEFAULT '',
                source         TEXT DEFAULT '',
                temperature    TEXT DEFAULT 'warm',
                stage          TEXT NOT NULL DEFAULT 'new',
                rep            TEXT NOT NULL,
                est_value      REAL DEFAULT 0,
                referred_by    TEXT DEFAULT '',
                crm_contact_id TEXT DEFAULT '',
                crm_project_id TEXT DEFAULT '',
                estimate_id    TEXT DEFAULT '',
                lost_reason    TEXT DEFAULT '',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                last_activity_at TEXT DEFAULT '',
                next_action_at TEXT DEFAULT '',
                won_at         TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS leads_rep_idx   ON leads(rep);
            CREATE INDEX IF NOT EXISTS leads_stage_idx ON leads(stage);
            CREATE INDEX IF NOT EXISTS leads_type_idx  ON leads(lead_type);
            CREATE INDEX IF NOT EXISTS leads_next_idx  ON leads(next_action_at);

            CREATE TABLE IF NOT EXISTS activities (
                id         TEXT PRIMARY KEY,
                lead_id    TEXT NOT NULL,
                rep        TEXT NOT NULL,
                kind       TEXT NOT NULL,
                outcome    TEXT DEFAULT '',
                body       TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS act_lead_idx ON activities(lead_id);
            CREATE INDEX IF NOT EXISTS act_rep_idx  ON activities(rep);

            CREATE TABLE IF NOT EXISTS tasks (
                id            TEXT PRIMARY KEY,
                lead_id       TEXT NOT NULL,
                rep           TEXT NOT NULL,
                kind          TEXT DEFAULT 'call',
                title         TEXT DEFAULT '',
                due_at        TEXT NOT NULL,
                done          INTEGER DEFAULT 0,
                done_at       TEXT DEFAULT '',
                enrollment_id TEXT DEFAULT '',
                created_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS task_rep_idx  ON tasks(rep, done);
            CREATE INDEX IF NOT EXISTS task_lead_idx ON tasks(lead_id);

            CREATE TABLE IF NOT EXISTS cadence_enrollments (
                id          TEXT PRIMARY KEY,
                lead_id     TEXT NOT NULL,
                cadence_id  TEXT NOT NULL,
                step_idx    INTEGER DEFAULT 0,
                started_at  TEXT NOT NULL,
                active      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS coaching_notes (
                id          TEXT PRIMARY KEY,
                subject_rep TEXT NOT NULL,
                author      TEXT NOT NULL,
                body        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS coach_rep_idx ON coaching_notes(subject_rep);

            CREATE TABLE IF NOT EXISTS goals (
                id      TEXT PRIMARY KEY,
                rep     TEXT NOT NULL,
                period  TEXT NOT NULL,
                metric  TEXT NOT NULL,
                target  REAL DEFAULT 0
            );
        ''')

# Prospecting columns, added after the first release. Additive only — SQLite
# cannot drop a column, so nothing here is ever removed, only stopped being read.
_PROSPECT_COLS = [
    ('website',      "TEXT DEFAULT ''"),
    ('license_no',   "TEXT DEFAULT ''"),   # DORA licence number — a dedupe key
    ('icp_score',    'INTEGER DEFAULT 0'),  # queue ordering
    ('source_ref',   "TEXT DEFAULT ''"),   # dataset + row id, for provenance
    ('hook',         "TEXT DEFAULT ''"),   # the one personalization slot needing judgment
    ('import_batch', "TEXT DEFAULT ''"),   # '' means hand-entered, never deduped
    ('dnc',          'INTEGER DEFAULT 0'),
    ('phone_norm',   "TEXT DEFAULT ''"),
    ('email_norm',   "TEXT DEFAULT ''"),
    # Nimbus enrichment. Filled by agents/b2b/enrich.py; surfaced in the lead drawer.
    ('research_notes',     "TEXT DEFAULT ''"),   # Perplexity JSON body, decision-makers, news, etc.
    ('research_citations', "TEXT DEFAULT '[]'"), # JSON array of source URLs; reps verify before calling
    ('recent_storm',       "TEXT DEFAULT ''"),   # canvasser hail-cache summary at this address
    ('enriched_at',        "TEXT DEFAULT ''"),   # ISO timestamp of last enrichment pass
    # When the appointment actually is. `appt_set` was a stage with no clock:
    # a rep booked Thursday at 2pm and the CRM had nowhere to put it, so the
    # commitment lived in the rep's head or in a task title. Deliberately NOT
    # required to enter the stage -- the canvasser creates leads straight into
    # `appt_set` from a doorstep, and rejecting those would break the handoff
    # the tool exists for. A missing time is surfaced instead (see
    # `appt_missing`), which is what gets it filled in.
    ('appt_at',            "TEXT DEFAULT ''"),
    # The rung a lead entered on. Most start at `new`, but the canvasser hands
    # over doorstep leads straight into `contacted`/`appt_set`/`inspected`, and
    # without this a cohort funnel cannot tell "never got that far" from
    # "started past there" -- it would under-report conversion on exactly the
    # leads the door-knocking exists to produce.
    ('entry_stage',        "TEXT DEFAULT ''"),
    ('customer_id',        "TEXT DEFAULT ''"),   # the person this deal is for
    # Which appointment time the customer has been told about, and reminded of.
    # They store the `appt_at` VALUE rather than a flag or a timestamp, so a
    # reschedule invalidates itself: the moment appt_at differs from these, the
    # customer is holding the wrong time and is owed a fresh message. A boolean
    # would have quietly confirmed the first time forever.
    ('appt_confirmed_for', "TEXT DEFAULT ''"),
    ('appt_reminded_for',  "TEXT DEFAULT ''"),
]

def migrate_db():
    """Additive column migrations for DBs created before a field existed."""
    with get_db() as db:
        cols = [r['name'] for r in db.execute('PRAGMA table_info(leads)')]
        if 'service' not in cols:
            db.execute("ALTER TABLE leads ADD COLUMN service TEXT DEFAULT 'roofing'")
        if 'plan' not in cols:
            db.execute("ALTER TABLE leads ADD COLUMN plan TEXT DEFAULT ''")
        if 'billing' not in cols:
            db.execute("ALTER TABLE leads ADD COLUMN billing TEXT DEFAULT ''")
        for name, decl in _PROSPECT_COLS:
            if name not in cols:
                db.execute(f'ALTER TABLE leads ADD COLUMN {name} {decl}')
        doc_cols = [r['name'] for r in db.execute('PRAGMA table_info(documents)')]
        if doc_cols and 'customer_id' not in doc_cols:
            db.execute("ALTER TABLE documents ADD COLUMN customer_id TEXT DEFAULT ''")
        db.executescript('''
            CREATE TABLE IF NOT EXISTS documents (
                id          TEXT PRIMARY KEY,
                lead_id     TEXT NOT NULL,
                filename    TEXT NOT NULL,
                orig_name   TEXT NOT NULL,
                size        INTEGER DEFAULT 0,
                uploaded_by TEXT NOT NULL,
                customer_id TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS doc_lead_idx ON documents(lead_id);

            -- Opt-outs. Checked on import AND on every queue build, because a
            -- partner who asks to be left alone must not resurface tomorrow in
            -- a batch sourced from a different dataset.
            CREATE TABLE IF NOT EXISTS suppressions (
                id         TEXT PRIMARY KEY,
                kind       TEXT NOT NULL CHECK (kind IN ('email','phone','domain')),
                value      TEXT NOT NULL,
                reason     TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS supp_val_idx ON suppressions(kind, value);

            CREATE INDEX IF NOT EXISTS leads_phone_idx ON leads(phone_norm);
            CREATE INDEX IF NOT EXISTS leads_email_idx ON leads(email_norm);
            CREATE INDEX IF NOT EXISTS leads_batch_idx ON leads(import_batch);

            -- The queue's net-new top-up, which is the one query that grows with
            -- the prospect list. Without this SQLite picks leads_stage_idx and
            -- scans every 'new' lead in the table -- and in a prospecting DB
            -- almost every lead is 'new', so that index selects nothing. The
            -- trailing icp_score/created_at also satisfy the ORDER BY, dropping
            -- the temp B-tree sort over the whole candidate set.
            CREATE INDEX IF NOT EXISTS leads_queue_idx
                ON leads(rep, stage, icp_score DESC, created_at);

            -- Stage history. The leaderboard and the funnel both ask "who
            -- reached stage X in this window", which is four equalities and a
            -- date range; without this it is a scan of every activity ever
            -- logged, and activities is the fastest-growing table here.
            CREATE INDEX IF NOT EXISTS act_stage_idx
                ON activities(kind, outcome, rep, created_at);

            CREATE INDEX IF NOT EXISTS leads_appt_idx ON leads(rep, appt_at);

            -- Replayed writes. A rep logs a door knock with no signal, the
            -- service worker queues it, and it arrives when the phone finds a
            -- bar -- possibly twice, because the first attempt may have
            -- reached the server and only the RESPONSE got lost. Without this
            -- the retry is a second call on the timeline and a second point on
            -- the leaderboard. The stored response is replayed verbatim so a
            -- retry is indistinguishable from the original success.
            CREATE TABLE IF NOT EXISTS idempotency (
                key        TEXT PRIMARY KEY,
                response   TEXT NOT NULL,
                status     INTEGER DEFAULT 200,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idem_age_idx ON idempotency(created_at);

            -- Which reps have already been told about which storm. One row per
            -- (storm, rep): a rep must not get the same swath again every time
            -- the loop comes round, and a swath that arrives while a rep is on
            -- holiday still has to reach them once when it is re-checked.
            -- The PERSON, as distinct from the deal.
            --
            -- `leads` is one row per deal, deliberately: the cross-sell Pitch
            -- button creates a second lead for the same homeowner on purpose,
            -- and POST /api/leads stays duplicate-friendly for it. That is
            -- right at the deal level and it left nothing at the person level
            -- -- so a homeowner with a roof in spring and siding in autumn was
            -- two unrelated rows, their documents were split across both, and
            -- "what has this customer ever had from us" had no answer.
            --
            -- This is also the half of the record Base44 holds that would be
            -- hardest to re-create: identity, address and history. Production
            -- and money stay in The Den; the customer lives here.
            CREATE TABLE IF NOT EXISTS customers (
                id             TEXT PRIMARY KEY,
                first_name     TEXT DEFAULT '',
                last_name      TEXT DEFAULT '',
                company        TEXT DEFAULT '',
                phone          TEXT DEFAULT '',
                email          TEXT DEFAULT '',
                address        TEXT DEFAULT '',
                city           TEXT DEFAULT '',
                state          TEXT DEFAULT '',
                zip            TEXT DEFAULT '',
                phone_norm     TEXT DEFAULT '',
                email_norm     TEXT DEFAULT '',
                addr_key       TEXT DEFAULT '',
                crm_contact_id TEXT DEFAULT '',
                notes          TEXT DEFAULT '',
                -- How we feel about working for this person again. Two levels,
                -- because "difficult" and "never again" behave differently: a
                -- caution still gets storm alerts and outreach (they may still
                -- be a good job, the rep just wants warning), while do_not_serve
                -- is excluded from everything that would put us in front of
                -- them. Distinct from `leads.dnc`, which is the customer's
                -- choice not to hear from us; this one is ours.
                flag           TEXT DEFAULT '',
                flag_reason    TEXT DEFAULT '',
                flag_by        TEXT DEFAULT '',
                flag_at        TEXT DEFAULT '',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS cust_phone_idx ON customers(phone_norm);
            CREATE INDEX IF NOT EXISTS cust_email_idx ON customers(email_norm);
            CREATE INDEX IF NOT EXISTS cust_addr_idx  ON customers(addr_key);
            CREATE INDEX IF NOT EXISTS leads_cust_idx ON leads(customer_id);
            CREATE INDEX IF NOT EXISTS doc_cust_idx   ON documents(customer_id);

            CREATE TABLE IF NOT EXISTS storm_notices (
                event_id TEXT NOT NULL,
                rep      TEXT NOT NULL,
                affected INTEGER DEFAULT 0,
                sent_at  TEXT NOT NULL,
                PRIMARY KEY (event_id, rep)
            );
        ''')
        _backfill_norms(db)
        _backfill_stage_keys(db)
        _backfill_entry_stage(db)
        _backfill_customers(db)

# Reverse of STAGE_META, for reading a stage back out of a pre-existing log line.
_LABEL_TO_STAGE = {m['label']: k for k, m in STAGE_META.items()}


def _backfill_stage_keys(db):
    """Fill `outcome` on stage_change rows written before it carried the key.

    These are real deal histories on a live volume -- the only record of when
    each appointment was set -- so they are parsed and kept, not dropped. The
    body reads "Contacted → Appt Set" or "Contacted → Won (contract signed)";
    the destination label is what sits after the arrow, minus any reason.
    Idempotent: only rows still missing a key are touched, so every run after
    the first is a no-op.
    """
    rows = db.execute(
        "SELECT id, body FROM activities WHERE kind='stage_change' AND outcome=''"
    ).fetchall()
    for r in rows:
        label = (r['body'] or '').split('→')[-1].strip()
        if label.endswith(')') and '(' in label:
            label = label[:label.rindex('(')].strip()
        stage = _LABEL_TO_STAGE.get(label)
        if stage:
            db.execute('UPDATE activities SET outcome=? WHERE id=?', (stage, r['id']))


def _backfill_entry_stage(db):
    """Where each pre-existing lead entered the pipeline.

    The earliest stage_change records the move OUT of the entry stage, and its
    body names it on the left of the arrow -- the one place that fact survives
    for rows written before the column existed. A lead that has never changed
    stage is still sitting on the rung it entered on. Idempotent.
    """
    rows = db.execute("SELECT id, stage FROM leads WHERE entry_stage=''").fetchall()
    for r in rows:
        first = db.execute(
            "SELECT body FROM activities WHERE lead_id=? AND kind='stage_change' "
            "ORDER BY created_at LIMIT 1", (r['id'],)).fetchone()
        entry = ''
        if first:
            entry = _LABEL_TO_STAGE.get((first['body'] or '').split('→')[0].strip(), '')
        db.execute('UPDATE leads SET entry_stage=? WHERE id=?',
                   (entry or r['stage'] or 'new', r['id']))


# Defined up here with the other pre-migration helpers, for the reason
# _norm_phone already documents: migrate_db() runs at import time and its
# backfills need these bound before anything else in the file is.
def _now_dt():
    return datetime.utcnow()

def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

# Derived from _now_dt() rather than reading the clock again, so the app has
# ONE clock. Two independent utcnow() calls can straddle a second boundary --
# and they made the clock impossible to hold still under test, which is why
# the appointment window's "today" behaviour was only ever tested by accident,
# passing or failing on what time of day the suite happened to run.
def _now():
    return _iso(_now_dt())


# ── The customer, as distinct from the deal ──────────────────────────────────
#
# Matching is on CONTACT DETAILS, never on name alone. Two Jon Smiths in one
# county are two people, and a name-only key merges their files -- which in this
# business means one homeowner's signed contract filed under another's. The
# estimator's custKey() is name-based and stays that way; it groups estimates a
# rep is already looking at, which is a much more forgiving job than deciding
# who somebody is.
#
# Address counts only WITH a surname, because a roof outlives its owner: the
# address alone would merge whoever we sold to in 2019 with whoever lives there
# now, and quietly attribute one family's history to another.
#
# This does NOT change the rule that leads are duplicate-friendly. That rule was
# always about deals -- the cross-sell Pitch button deliberately creates a
# second lead for the same homeowner -- and it was being asked to stand in for a
# person-level identity it could never provide. Deals stay separate; the person
# is what joins them.


def _addr_key(row):
    """Normalized address, or '' when there isn't enough to key on.

    Reuses portal.geo's normalizer rather than inventing a second one: it
    already collapses street suffixes and directions and strips a trailing zip,
    and the coordinates cache is keyed the same way, so an address that groups
    two leads here is the same address that places them under a hail swath.
    """
    if not (row.get('address') or '').strip():
        return ''
    return pgeo.norm_address(row.get('address'), row.get('city'),
                             row.get('state'), row.get('zip'))


def _match_customer(db, row):
    """The existing customer this lead belongs to, or None.

    Ordered strongest first. A phone number is the closest thing to an identity
    a homeowner gives us; an address plus a surname is the weakest thing still
    worth trusting.
    """
    phone, email = row.get('phone_norm') or '', row.get('email_norm') or ''
    if phone:
        hit = db.execute('SELECT * FROM customers WHERE phone_norm=?', (phone,)).fetchone()
        if hit:
            return hit
    if email:
        hit = db.execute('SELECT * FROM customers WHERE email_norm=?', (email,)).fetchone()
        if hit:
            return hit
    akey, last = _addr_key(row), (row.get('last_name') or '').strip().lower()
    if akey and last:
        hit = db.execute(
            'SELECT * FROM customers WHERE addr_key=? AND LOWER(last_name)=?',
            (akey, last)).fetchone()
        if hit:
            return hit
    return None


def _identifiable(row):
    """Whether there is enough here to say who this is.

    An open-data row with a company name, a city and a licence number is not a
    person -- it is a record we might one day call. Minting a customer for each
    would put tens of thousands of rows in the table that name nobody, and the
    first one with a blank key would swallow all the others.
    """
    return bool((row.get('phone_norm') or '').strip()
                or (row.get('email_norm') or '').strip()
                or (_addr_key(row) and (row.get('last_name') or '').strip()))


CUSTOMER_FIELDS = ('first_name', 'last_name', 'company', 'phone', 'email',
                   'address', 'city', 'state', 'zip')

# '' is the normal state. `caution` warns the rep and changes nothing else --
# a difficult customer can still be a job worth doing, and burying that decision
# in a suppression list takes it away from the person best placed to make it.
# `do_not_serve` is the one that actually stops things happening.
CUSTOMER_FLAGS = ('', 'caution', 'do_not_serve')
# Spelled once and reused: three separate queries have to honour this, and
# three hand-copied subqueries are three chances for one to drift and quietly
# start putting a customer we refuse to serve back in front of a rep.
NOT_BANNED_SQL = ("customer_id NOT IN (SELECT id FROM customers "
                  "WHERE flag='do_not_serve')")

CUSTOMER_FLAG_LABELS = {
    'caution': '⚠ Difficult customer — read the note before engaging',
    'do_not_serve': '⛔ Do not work with again',
}


def _link_customer(db, lead_id, row):
    """Attach a lead to its customer, creating one if this person is new.

    Returns the customer id, or '' when the row identifies nobody. Filling in
    blanks on an existing customer as later deals learn more -- a doorstep lead
    with only an address, then a phone number three days later -- is deliberate;
    overwriting a value that is already there is not, because the newest typing
    is not automatically the most correct.
    """
    if not _identifiable(row):
        return ''
    hit = _match_customer(db, row)
    if hit:
        fill = {f: row.get(f) for f in CUSTOMER_FIELDS
                if (row.get(f) or '').strip() and not (hit[f] or '').strip()}
        if fill:
            sets = ', '.join(f'{f}=?' for f in fill)
            db.execute(f'UPDATE customers SET {sets}, phone_norm=?, email_norm=?, '
                       f'addr_key=?, updated_at=? WHERE id=?',
                       list(fill.values())
                       + [hit['phone_norm'] or row.get('phone_norm') or '',
                          hit['email_norm'] or row.get('email_norm') or '',
                          hit['addr_key'] or _addr_key(row), _now(), hit['id']])
        cid = hit['id']
    else:
        cid = str(uuid.uuid4())
        db.execute(
            'INSERT INTO customers (id, first_name, last_name, company, phone, email, '
            'address, city, state, zip, phone_norm, email_norm, addr_key, '
            'created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [cid] + [row.get(f) or '' for f in CUSTOMER_FIELDS]
            + [row.get('phone_norm') or '', row.get('email_norm') or '',
               _addr_key(row), _now(), _now()])
    db.execute('UPDATE leads SET customer_id=? WHERE id=?', (cid, lead_id))
    return cid


def _backfill_customers(db):
    """Group the leads already here into people. Idempotent, and cheap after
    the first run.

    Oldest first, so the customer record is created from the earliest deal and
    later ones fill in what it was missing rather than the other way round.

    The identifiability test is repeated IN SQL rather than left to
    `_identifiable()` alone. This runs at import, on every gunicorn boot, and a
    prospecting table is mostly rows that will never name anybody -- they keep
    `customer_id=''` forever, so a bare scan re-examines all 36,000 of them on
    every deploy and runs the address normalizer over each. Measured at 40,000
    leads that was 650ms a boot, growing with every import; filtering here makes
    it 6ms.
    """
    rows = db.execute(
        "SELECT * FROM leads WHERE customer_id='' AND ("
        "  phone_norm != '' OR email_norm != ''"
        "  OR (address != '' AND last_name != ''))"
        " ORDER BY created_at").fetchall()
    for r in rows:
        _link_customer(db, r['id'], dict(r))


def _backfill_norms(db):
    """Populate phone_norm/email_norm for rows written before they existed.

    Idempotent and cheap: only rows with contact details but no normalized form
    are touched, so this is a no-op on every run after the first.
    """
    rows = db.execute(
        "SELECT id, phone, email FROM leads "
        "WHERE (phone != '' AND phone_norm = '') OR (email != '' AND email_norm = '')"
    ).fetchall()
    for r in rows:
        db.execute('UPDATE leads SET phone_norm=?, email_norm=? WHERE id=?',
                   (_norm_phone(r['phone']), _norm_email(r['email']), r['id']))

init_db()
migrate_db()

# ── JSON config (cadences, playbook) ──────────────────────────────────────────

def _load_json(name, default):
    try:
        with open(os.path.join(HERE, name), encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'[config] could not load {name}: {e}')
        return default

CADENCES = _load_json('cadences.json', [])
PLAYBOOK = _load_json('playbook.json', {'objections': [], 'scripts': [], 'principles': []})
PLANS    = _load_json('plans.json', [])
TEMPLATES = _load_json('outreach_templates.json',
                       {'signature': '', 'banned_phrases': [], 'templates': {}})
CADENCE_BY_ID = {c['id']: c for c in CADENCES}
PLAN_BY_ID    = {p['id']: p for p in PLANS}

# ── Helpers ───────────────────────────────────────────────────────────────────


# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        # Re-read the store rather than trusting the cookie, so a demotion
        # takes effect on the next request instead of at next sign-in.
        if not pusers.is_manager_up(session['username']):
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return wrapper

def is_manager():
    """Admins/managers see every rep's pipeline and the coaching views."""
    return pusers.is_manager_up(session.get('username'))

def current_rep():
    return session.get('username')

# ── Static ────────────────────────────────────────────────────────────────────

STATIC_DIR = os.path.join(HERE, 'static')

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')

@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory(STATIC_DIR, path)

@app.route('/manifest.json')
def manifest():
    return send_from_directory(STATIC_DIR, 'manifest.json')

@app.route('/sw.js')
def sw():
    return send_from_directory(STATIC_DIR, 'sw.js')

# ── Identity ──────────────────────────────────────────────────────────────────
# Login, logout, signup, invites, password resets and role changes all moved to
# the portal (portal/app.py) when the three tools merged onto one origin. What
# is left here is read-only: who the shared session says you are, and the
# roster the pipeline UI needs to render names and assignment dropdowns.

def _me_payload(user):
    return {
        'username':   user['username'],
        # is_admin means manager-or-above here — it is what gates the Numbers
        # and Coaching tabs, which managers are meant to see.
        'is_admin':   user['role'] in pusers.ELEVATED,
        'role':       user['role'],
        'full_name':  user['full_name'],
        'is_manager': user['role'] in pusers.ELEVATED,
    }

@app.route('/api/me')
def me():
    if 'username' not in session:
        return jsonify({'authenticated': False})
    user = pusers.get(session['username'])
    if not user:
        # Row deleted out from under a live cookie.
        session.clear()
        return jsonify({'authenticated': False})
    payload = _me_payload(user)
    payload['authenticated'] = True
    return jsonify(payload)

@app.route('/api/users')
@login_required
def list_users():
    """The roster, for assignment dropdowns and rendering names.

    Deliberately login-only rather than admin-only: reps need the names. Only
    the portal mutates the roster.
    """
    return jsonify([{'username': u['username'], 'is_admin': u['role'] in pusers.ELEVATED,
                     'role': u['role'], 'full_name': u['full_name'],
                     'created_at': u['created_at']}
                    for u in pusers.all_users()])

# ── Lead helpers ──────────────────────────────────────────────────────────────

def _lead_row(row):
    d = dict(row)
    meta = STAGE_META.get(d['stage'], {'label': d['stage'], 'color': '#6B7280'})
    d['stage_label'] = meta['label']
    d['stage_color'] = meta['color']
    smeta = SERVICE_META.get(d.get('service') or 'roofing', SERVICES[0])
    d['service_label'] = smeta['label']
    d['service_icon']  = smeta['icon']
    d['value_suffix']  = BILLING_SUFFIX.get(d.get('billing') or '', '')
    plan = PLAN_BY_ID.get(d.get('plan') or '')
    d['plan_name'] = plan['name'] if plan else ''
    d['name'] = (f"{d['first_name']} {d['last_name']}").strip() or d['company'] or '(no name)'
    # Days since last activity (stall detector). Empty = never touched.
    d['stalled'] = _is_stalled(d)
    d['overdue'] = bool(d['next_action_at']) and d['next_action_at'] <= _now()
    # A booked appointment with no time is a real defect in the data, not an
    # empty field: nobody can be anywhere at "sometime". Surfaced so the board
    # and My Day can nag rather than letting it sit there looking complete.
    d['appt_missing'] = d['stage'] == 'appt_set' and not d.get('appt_at')
    # Whether the CUSTOMER knows. Surfaced rather than assumed: mail can be
    # unconfigured and a lead can have no email address, and either way an
    # appointment nobody confirmed is a no-show waiting to happen. Silence here
    # is the failure mode, so the drawer says which of the three it is.
    if d.get('appt_at'):
        d['appt_confirmed'] = d.get('appt_confirmed_for') == d['appt_at']
        d['appt_reachable'] = bool((d.get('email') or '').strip()) and not d.get('dnc')
    return d

def _is_stalled(d):
    if d['stage'] not in OPEN_STAGES:
        return False
    ref = d['last_activity_at'] or d['created_at']
    try:
        ref_dt = datetime.strptime(ref, '%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return False
    return (_now_dt() - ref_dt) > timedelta(days=STALL_DAYS)

def _lead_visible(db, lead_id):
    """Return the lead row if the current user may see it, else None."""
    row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    if not row:
        return None
    if not is_manager() and row['rep'] != current_rep():
        return None
    return row

def _log_activity(db, lead_id, kind, body='', outcome='', rep=None):
    db.execute('INSERT INTO activities (id, lead_id, rep, kind, outcome, body, created_at) '
               'VALUES (?,?,?,?,?,?,?)',
               (str(uuid.uuid4()), lead_id, rep or current_rep(), kind, outcome, body, _now()))
    if kind in OUTREACH_KINDS:
        db.execute('UPDATE leads SET last_activity_at=?, updated_at=? WHERE id=?',
                   (_now(), _now(), lead_id))

# Every stage change is logged with the destination stage's KEY in `outcome`,
# and the human sentence in `body`. Those are two different jobs and they used
# to be done by one string: the leaderboard counted appointments with
# `body LIKE '%→ Appt Set%'`, so renaming a stage's label in STAGES -- a
# cosmetic edit with nothing anywhere to warn you -- silently zeroed every
# rep's appointment count forever. A key never changes for cosmetic reasons,
# and it is what the cohort funnel counts too.
def _log_stage_change(db, lead_id, old_stage, new_stage, rep=None, reason=''):
    body = f'{STAGE_META[old_stage]["label"]} → {STAGE_META[new_stage]["label"]}'
    if reason:
        body += f' ({reason})'
    _log_activity(db, lead_id, 'stage_change', body=body, outcome=new_stage, rep=rep)


def _log_appointment(db, lead_id, before, after, rep=None):
    """Record a booking, a reschedule or a cancellation on the timeline.

    An appointment is a promise to a customer, not a field. When it moves, the
    fact that it moved is what a manager needs on Monday -- "we rescheduled
    them twice" is the story, and a bare overwritten column cannot tell it.
    """
    if before == after:
        return
    if after and not before:
        body = f'📅 Appointment set for {_appt_label(after)}'
    elif after:
        body = f'📅 Appointment moved: {_appt_label(before)} → {_appt_label(after)}'
    else:
        body = f'📅 Appointment cleared (was {_appt_label(before)})'
    _log_activity(db, lead_id, 'system', body=body, rep=rep)


def _appt_label(iso):
    """'Thu 11 Sep, 2:00 PM' — what a human would say out loud."""
    try:
        return datetime.strptime(iso, '%Y-%m-%dT%H:%M:%SZ').strftime('%a %-d %b, %-I:%M %p')
    except Exception:
        return iso or '(no time)'


def _move_open_tasks(db, lead_id, new_rep):
    """Follow-ups belong to whoever owns the lead now.

    `tasks.rep` is a separate column from `leads.rep`, and nothing used to keep
    them in step -- so handing a deal to another rep left every open follow-up
    on the previous owner's My Day while the new owner saw a lead with no next
    action. The task the cadence engine scheduled was then worked by nobody.
    Done tasks keep their original rep: they are a record of who did the work.
    """
    db.execute('UPDATE tasks SET rep=? WHERE lead_id=? AND done=0', (new_rep, lead_id))


def _refresh_next_action(db, lead_id):
    """The soonest thing this lead needs: an open task, or the appointment.

    The appointment counts only while the lead is still IN `appt_set`. Once the
    rep has moved them on to `inspected`, the appointment happened -- leaving it
    driving the next action would mark every inspected lead permanently overdue.
    """
    row = db.execute('SELECT MIN(due_at) m FROM tasks WHERE lead_id=? AND done=0',
                     (lead_id,)).fetchone()
    lead = db.execute('SELECT stage, appt_at FROM leads WHERE id=?', (lead_id,)).fetchone()
    candidates = [c for c in (row['m'],
                              lead['appt_at'] if lead and lead['stage'] == 'appt_set' else '')
                  if c]
    db.execute('UPDATE leads SET next_action_at=?, updated_at=? WHERE id=?',
               (min(candidates) if candidates else '', _now(), lead_id))

# ── Replay safety ────────────────────────────────────────────────────────────
#
# Anything that INSERTS needs this, because the offline queue can deliver the
# same write twice: the phone gives up on a request whose response was lost in
# transit, and the row was already written. Stage moves and task completions do
# not -- setting a stage to `won` twice is the same as once -- so they are left
# alone rather than given ceremony they do not need.
#
# Keys are minted by the browser (`Idempotency-Key`), because only the browser
# knows that the retry IS the original request.

IDEMPOTENCY_TTL_DAYS = int(os.environ.get('SALESCRM_IDEMPOTENCY_TTL_DAYS', '14'))


def _idem_key():
    """The request's replay key, or '' when the caller did not mint one."""
    return (request.headers.get('Idempotency-Key') or '').strip()[:120]


def _idem_replay(db, key):
    """The original response for `key`, or None.

    A key matches until it is PRUNED, not until it is notionally expired --
    matching for longer than necessary only ever suppresses a duplicate, while
    expiring eagerly risks writing one. The TTL is enforced by the sweep in
    `_idem_remember`, which is the safe direction to get wrong.
    """
    if not key:
        return None
    row = db.execute('SELECT response, status FROM idempotency WHERE key=?',
                     (key,)).fetchone()
    if not row:
        return None
    return json.loads(row['response']), row['status']


def _idem_remember(db, key, payload, status=200):
    """Record what this key answered, so the retry answers the same thing."""
    if not key:
        return
    db.execute('INSERT OR REPLACE INTO idempotency (key, response, status, created_at) '
               'VALUES (?,?,?,?)', (key, json.dumps(payload), status, _now()))
    # Opportunistic prune. A queue that never drains is a bug elsewhere; keys
    # older than the window cannot still be in flight.
    db.execute('DELETE FROM idempotency WHERE created_at < ?',
               (_iso(_now_dt() - timedelta(days=IDEMPOTENCY_TTL_DAYS)),))


# ── Leads ─────────────────────────────────────────────────────────────────────

@app.route('/api/leads', methods=['GET'])
@login_required
def list_leads():
    _reconcile_funnel()
    rep     = request.args.get('rep')
    stage   = request.args.get('stage')
    ltype   = request.args.get('type')
    service = request.args.get('service')
    q       = (request.args.get('q') or '').strip().lower()
    limit   = min(int(request.args.get('limit', 1000)), 5000)

    clauses, params = [], []
    if not is_manager():
        clauses.append('rep=?'); params.append(current_rep())   # reps see only their own
    elif rep:
        clauses.append('rep=?'); params.append(rep)
    if stage:
        clauses.append('stage=?'); params.append(stage)
    if ltype:
        clauses.append('lead_type=?'); params.append(ltype)
    if service:
        clauses.append('service=?'); params.append(service)
    if q:
        # Matched in SQL, not in Python afterwards: filtering the page the LIMIT
        # already returned would search only the most recently updated `limit`
        # rows, so a partner outside that window could not be found at all. With
        # tens of thousands of imported prospects that is most of the table.
        # `_` and `%` are escaped so a literal one is not read as a wildcard.
        esc = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        clauses.append(
            "LOWER(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'') || ' ' ||"
            "      COALESCE(phone,'')      || ' ' || COALESCE(email,'')     || ' ' ||"
            "      COALESCE(address,'')    || ' ' || COALESCE(company,''))"
            " LIKE ? ESCAPE '\\'")
        params.append(f'%{esc}%')
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with get_db() as db:
        rows = db.execute(f'SELECT * FROM leads {where} ORDER BY updated_at DESC LIMIT ?',
                          params + [limit]).fetchall()
    return jsonify([_lead_row(r) for r in rows])

@app.route('/api/leads', methods=['POST'])
@login_required
def create_lead():
    data = request.get_json(force=True)
    lead_type = data.get('lead_type', 'homeowner')
    if lead_type not in LEAD_TYPE_KEYS:
        return jsonify({'error': 'Invalid lead type'}), 400
    service = data.get('service', 'roofing')
    if service not in SERVICE_KEYS:
        return jsonify({'error': 'Invalid service'}), 400
    billing = data.get('billing', '')
    if billing not in BILLING_KEYS:
        return jsonify({'error': 'Invalid billing'}), 400
    stage = data.get('stage', 'new')
    if stage not in STAGE_KEYS:
        stage = 'new'
    # Managers may assign to any rep; reps own what they create.
    rep = data.get('rep') if is_manager() and data.get('rep') else current_rep()
    lid = str(uuid.uuid4())
    fields = {
        'id': lid, 'lead_type': lead_type, 'service': service,
        'plan': data.get('plan', ''), 'billing': billing,
        'first_name': data.get('first_name', ''), 'last_name': data.get('last_name', ''),
        'company': data.get('company', ''), 'phone': data.get('phone', ''),
        'email': data.get('email', ''), 'address': data.get('address', ''),
        'city': data.get('city', ''), 'state': data.get('state', ''), 'zip': data.get('zip', ''),
        'source': data.get('source', ''), 'temperature': data.get('temperature', 'warm'),
        'stage': stage, 'entry_stage': stage, 'rep': rep,
        'est_value': float(data.get('est_value') or 0),
        'referred_by': data.get('referred_by', ''),
        'appt_at': data.get('appt_at', ''),
        'phone_norm': _norm_phone(data.get('phone', '')),
        'email_norm': _norm_email(data.get('email', '')),
        'created_at': _now(), 'updated_at': _now(),
    }
    cols = ','.join(fields.keys())
    ph   = ','.join('?' * len(fields))
    key = _idem_key()
    with get_db() as db:
        # A replayed create must return the ORIGINAL lead, not make a second
        # one. This endpoint is deliberately duplicate-friendly otherwise -- the
        # cross-sell "Pitch" button creates a second deal for the same person on
        # purpose -- so the replay guard is the key, never the contact details.
        prior = _idem_replay(db, key)
        if prior:
            return jsonify(prior[0]), prior[1]
        db.execute(f'INSERT INTO leads ({cols}) VALUES ({ph})', list(fields.values()))
        _log_activity(db, lid, 'system', body=f'Lead created in stage "{STAGE_META[stage]["label"]}"')
        # A new lead starts following itself up. The cadence engine and its four
        # cadences already existed; nothing ever enrolled anyone, so the whole
        # follow-up apparatus only ran for reps who remembered to ask for it.
        auto = _cadence_for(stage, lead_type)
        if auto:
            _enroll(db, lid, rep, auto)
        _link_customer(db, lid, fields)
        row = db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone()
        payload = _lead_row(row)
        _idem_remember(db, key, payload, 201)
    return jsonify(payload), 201

@app.route('/api/leads/<lead_id>', methods=['GET'])
@login_required
def get_lead(lead_id):
    _reconcile_funnel()
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        d = _lead_row(row)
        d['activities'] = [dict(a) for a in db.execute(
            'SELECT * FROM activities WHERE lead_id=? ORDER BY created_at DESC LIMIT 200',
            (lead_id,)).fetchall()]
        d['tasks'] = [dict(t) for t in db.execute(
            'SELECT * FROM tasks WHERE lead_id=? ORDER BY done, due_at', (lead_id,)).fetchall()]
        d['enrollments'] = [dict(e) for e in db.execute(
            'SELECT * FROM cadence_enrollments WHERE lead_id=? AND active=1', (lead_id,)).fetchall()]
        # The person, and how many other deals they have. A rep opening a lead
        # should be able to see at a glance that this homeowner already bought
        # a roof from us in 2023 -- which changes the conversation entirely.
        d['customer'] = None
        if d.get('customer_id'):
            c = db.execute('SELECT * FROM customers WHERE id=?',
                           (d['customer_id'],)).fetchone()
            if c:
                q = "SELECT COUNT(*) c FROM leads WHERE customer_id=? AND id!=?"
                p = [d['customer_id'], lead_id]
                if not is_manager():
                    q += ' AND rep=?'; p.append(current_rep())
                d['customer'] = {
                    'id': c['id'],
                    'name': (f"{c['first_name']} {c['last_name']}").strip()
                            or c['company'] or '(no name)',
                    'other_deals': db.execute(q, p).fetchone()['c'],
                    'flag': c['flag'],
                    'flag_label': CUSTOMER_FLAG_LABELS.get(c['flag'], ''),
                    'flag_reason': c['flag_reason'],
                    'flag_by': c['flag_by'],
                }
        if d['referred_by']:
            ref = db.execute('SELECT first_name,last_name,company FROM leads WHERE id=?',
                             (d['referred_by'],)).fetchone()
            d['referred_by_name'] = (f"{ref['first_name']} {ref['last_name']}".strip()
                                     or ref['company']) if ref else ''
        # Partners carry their referred "projects" so the UI can show them inline.
        if d['lead_type'] in PARTNER_TYPES:
            q, params = 'SELECT * FROM leads WHERE referred_by=?', [lead_id]
            if not is_manager():
                q += ' AND rep=?'; params.append(current_rep())
            d['referrals'] = [_lead_row(r) for r in db.execute(
                q + ' ORDER BY created_at DESC', params).fetchall()]
    for e in d['enrollments']:
        cad = CADENCE_BY_ID.get(e['cadence_id'])
        e['cadence_name'] = cad['name'] if cad else e['cadence_id']
    return jsonify(d)

# Fields a rep may PUT directly. source_ref/import_batch/phone_norm/email_norm
# are server-owned — they record where a row came from and must not be editable.
LEAD_EDITABLE = ['lead_type', 'service', 'plan', 'billing', 'first_name', 'last_name',
                 'company', 'phone', 'email', 'address', 'city', 'state', 'zip', 'source',
                 'temperature', 'est_value', 'referred_by', 'lost_reason', 'estimate_id', 'rep',
                 'appt_at',
                 'website', 'license_no', 'icp_score', 'dnc', 'hook']

@app.route('/api/leads/<lead_id>', methods=['PUT'])
@login_required
def update_lead(lead_id):
    data = request.get_json(force=True)
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        sets, params = [], []
        for f in LEAD_EDITABLE:
            if f in data:
                if f == 'rep' and not is_manager():
                    continue                      # only managers reassign owner
                if f == 'lead_type' and data[f] not in LEAD_TYPE_KEYS:
                    return jsonify({'error': 'Invalid lead type'}), 400
                if f == 'service' and data[f] not in SERVICE_KEYS:
                    return jsonify({'error': 'Invalid service'}), 400
                if f == 'billing' and data[f] not in BILLING_KEYS:
                    return jsonify({'error': 'Invalid billing'}), 400
                if f == 'lost_reason' and not plost.valid(data[f]):
                    return jsonify({'error': 'Invalid lost reason'}), 400
                if f == 'est_value':
                    sets.append('est_value=?'); params.append(float(data[f] or 0)); continue
                if f in ('icp_score', 'dnc'):
                    sets.append(f'{f}=?'); params.append(int(data[f] or 0)); continue
                # Keep the normalized forms in step, or an edited phone number
                # would still dedupe against the value it replaced.
                if f == 'phone':
                    sets.append('phone_norm=?'); params.append(_norm_phone(data[f]))
                if f == 'email':
                    sets.append('email_norm=?'); params.append(_norm_email(data[f]))
                sets.append(f'{f}=?'); params.append(data[f])
        if not sets:
            return jsonify({'error': 'Nothing to update'}), 400
        sets.append('updated_at=?'); params.append(_now()); params.append(lead_id)
        old_rep, old_appt = row['rep'], row['appt_at']
        db.execute(f'UPDATE leads SET {", ".join(sets)} WHERE id=?', params)
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
        if row['appt_at'] != old_appt:
            _log_appointment(db, lead_id, old_appt, row['appt_at'])
            _refresh_next_action(db, lead_id)
            if row['appt_at']:
                _send_appt_mail(db, lead_id, 'confirm')
        if row['rep'] != old_rep:
            _move_open_tasks(db, lead_id, row['rep'])
            _log_activity(db, lead_id, 'system',
                          body=f'Reassigned: {pusers.display_name(old_rep)} → '
                               f'{pusers.display_name(row["rep"])}')
        # A corrected phone number or address can identify a lead that was
        # anonymous when it arrived, so linking is retried on every edit. An
        # existing link is left alone: re-pointing a deal at a different person
        # because somebody fixed a typo is how a customer's history splits.
        if not row['customer_id']:
            _link_customer(db, lead_id, dict(row))
        # Re-read last: _refresh_next_action writes, and returning the row from
        # before it hands the caller a next action that is already wrong.
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    return jsonify(_lead_row(row))

@app.route('/api/leads/<lead_id>/stage', methods=['PATCH'])
@login_required
def set_stage(lead_id):
    data = request.get_json(force=True)
    new_stage = data.get('stage')
    if new_stage not in STAGE_KEYS:
        return jsonify({'error': 'Invalid stage'}), 400
    den_result = None
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        old = row['stage']
        if old == new_stage:
            return jsonify(_lead_row(row))
        won_at = _now() if new_stage == 'won' else (row['won_at'] or '')
        lost_reason = data.get('lost_reason', row['lost_reason'])
        if not plost.valid(lost_reason):
            return jsonify({'error': 'Invalid lost reason'}), 400
        # Moving back out of lost clears the reason. Plenty of deals get
        # re-quoted, and a job that closes in March must not carry "went with
        # someone else" into the month it was won. Same rule as the estimator's.
        if new_stage != 'lost':
            lost_reason = ''
        # Moving INTO appt_set is where a time gets captured, because that is
        # the moment the rep has one. Anything else leaves it alone -- a lead
        # walked forward to `inspected` keeps the appointment it was seen on.
        appt_at = data.get('appt_at', row['appt_at']) if new_stage == 'appt_set' \
            else row['appt_at']
        db.execute('UPDATE leads SET stage=?, won_at=?, lost_reason=?, appt_at=?, '
                   'updated_at=? WHERE id=?',
                   (new_stage, won_at, lost_reason, appt_at, _now(), lead_id))
        _log_stage_change(db, lead_id, old, new_stage)
        _log_appointment(db, lead_id, row['appt_at'], appt_at)
        if appt_at and appt_at != row['appt_at']:
            _send_appt_mail(db, lead_id, 'confirm')
        # Terminal stages close out any pending follow-up tasks + cadences.
        if new_stage in ('won', 'lost'):
            db.execute("UPDATE tasks SET done=1, done_at=? WHERE lead_id=? AND done=0",
                       (_now(), lead_id))
            db.execute("UPDATE cadence_enrollments SET active=0 WHERE lead_id=?", (lead_id,))
        _refresh_next_action(db, lead_id)
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()

    # Auto-handoff to The Den on Won (unless already pushed).
    #
    # The guard is `crm_project_id`, and the distinction is the whole reason no
    # job ever reached The Den: `crm_contact_id` is set the moment a rep starts
    # an estimate, so guarding on it meant every lead that got a quote was read
    # as "already pushed" and skipped. A project id is only ever written by a
    # push that actually happened.
    if new_stage == 'won' and not row['crm_project_id']:
        est = (pfunnel.for_lead(lead_id) or [None])[0]
        den_result = _push_to_den(lead_id, estimate=est)

    with get_db() as db:
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    resp = _lead_row(row)
    if den_result is not None:
        resp['den'] = den_result
    return jsonify(resp)

@app.route('/api/leads/<lead_id>', methods=['DELETE'])
@login_required
def delete_lead(lead_id):
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        # Uploaded files go too, rows AND bytes. They used to be left behind:
        # the rows pointed at a lead that no longer existed and the files sat on
        # the volume forever, counting against the disk and appearing in every
        # backup as documents belonging to nobody.
        docs = db.execute('SELECT filename FROM documents WHERE lead_id=?',
                          (lead_id,)).fetchall()
        db.execute('DELETE FROM documents WHERE lead_id=?', (lead_id,))
        db.execute('DELETE FROM leads WHERE id=?', (lead_id,))
        db.execute('DELETE FROM activities WHERE lead_id=?', (lead_id,))
        db.execute('DELETE FROM tasks WHERE lead_id=?', (lead_id,))
        db.execute('DELETE FROM cadence_enrollments WHERE lead_id=?', (lead_id,))
        # A partner's referrals outlive the partner. Clearing the pointer keeps
        # them findable instead of attributed to a lead that is gone.
        db.execute("UPDATE leads SET referred_by='' WHERE referred_by=?", (lead_id,))
    for d in docs:
        try:
            os.remove(os.path.join(DOCS_DIR, d['filename']))
        except OSError:
            pass
    return jsonify({'ok': True})

# ── Activities ────────────────────────────────────────────────────────────────

@app.route('/api/leads/<lead_id>/activities', methods=['POST'])
@login_required
def add_activity(lead_id):
    data = request.get_json(force=True)
    kind = data.get('kind', 'note')
    key = _idem_key()
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        # A door knock queued with no signal and delivered twice is two calls on
        # the timeline and two points on the leaderboard.
        prior = _idem_replay(db, key)
        if prior:
            return jsonify(prior[0]), prior[1]
        _log_activity(db, lead_id, kind, body=data.get('body', ''), outcome=data.get('outcome', ''))
        acts = [dict(a) for a in db.execute(
            'SELECT * FROM activities WHERE lead_id=? ORDER BY created_at DESC LIMIT 200',
            (lead_id,)).fetchall()]
        _idem_remember(db, key, acts, 201)
    return jsonify(acts), 201

# ── Tasks (the "next action" engine) ──────────────────────────────────────────

@app.route('/api/myday')
@login_required
def myday():
    """The rep's own standing: counts, money, and the two lists worth acting on.

    My Day used to fetch /api/leads?limit=1000 and count the page in the
    browser. Two things were wrong with that and both get worse as the business
    grows: the totals were a count of the page rather than of the pipeline, so
    "Open leads" and "Pipeline $" quietly capped at 1,000 — and a rep carrying
    imported prospects passes 1,000 on the first batch — and every load of the
    first screen of the morning pulled a thousand rows over a phone connection
    in a driveway to compute five numbers SQL can return.
    """
    rep = request.args.get('rep') if is_manager() else current_rep()
    where, params = [], []
    if rep:
        where.append('rep=?'); params.append(rep)
    openq = ' AND '.join(where + ['stage IN (%s)' % ','.join('?' * len(OPEN_STAGES))])
    openp = params + OPEN_STAGES

    with get_db() as db:
        agg = db.execute(f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v '
                         f'FROM leads WHERE {openq}', openp).fetchone()
        hot_rows = db.execute(
            f'SELECT * FROM leads WHERE {openq} AND temperature=? '
            f'ORDER BY updated_at DESC LIMIT 25', openp + ['hot']).fetchall()
        # Stalled is a date rule, so it is applied in SQL rather than by
        # filtering a page: the stalled lead a rep most needs to see is an old
        # one, which is exactly what a recency-ordered page drops first.
        cutoff = _iso(_now_dt() - timedelta(days=STALL_DAYS))
        stalled_rows = db.execute(
            f"SELECT * FROM leads WHERE {openq} "
            f"AND (CASE WHEN last_activity_at != '' THEN last_activity_at "
            f"          ELSE created_at END) < ? "
            f"ORDER BY (CASE WHEN last_activity_at != '' THEN last_activity_at "
            f"               ELSE created_at END) LIMIT 25", openp + [cutoff]).fetchall()
        stalled_total = db.execute(
            f"SELECT COUNT(*) c FROM leads WHERE {openq} "
            f"AND (CASE WHEN last_activity_at != '' THEN last_activity_at "
            f"          ELSE created_at END) < ?", openp + [cutoff]).fetchone()['c']
        hot_total = db.execute(f'SELECT COUNT(*) c FROM leads WHERE {openq} '
                               f'AND temperature=?', openp + ['hot']).fetchone()['c']
        # Sidebar stage counts, for the same reason: they read the cached page
        # and so reported "New 1,000" for a rep holding 36,000.
        stage_counts = {k: 0 for k in STAGE_KEYS}
        sw = ('WHERE ' + ' AND '.join(where)) if where else ''
        for r in db.execute(f'SELECT stage, COUNT(*) c FROM leads {sw} GROUP BY stage',
                            params):
            stage_counts[r['stage']] = r['c']

    return jsonify({
        'open_count': agg['c'], 'pipeline_value': agg['v'],
        'hot_count': hot_total, 'stalled_count': stalled_total,
        'hot': [_lead_row(r) for r in hot_rows],
        'stalled': [_lead_row(r) for r in stalled_rows],
        'stage_counts': stage_counts,
    })


@app.route('/api/appointments')
@login_required
def appointments():
    """The rep's booked appointments in a window — the day's actual schedule.

    This is its own query rather than a filter over /api/leads on the client,
    for the reason the pipeline search already learned the hard way: the lead
    list is capped, and once prospecting has imported partners by the thousand
    the cap is most of the table. An appointment that falls outside the most
    recently updated page is the one a rep misses.

    Terminal leads drop out — a won or lost deal's appointment is history — but
    everything still in play stays, including leads already walked on to
    `inspected`, so a rep can still see what their day was.
    """
    # Always somebody's schedule, never everybody's: My Day asks this question
    # about the person reading it. A manager can name another rep; passing no
    # rep at all used to mean "the entire company", which put twenty other
    # people's appointments on their own morning screen.
    rep = (request.args.get('rep') or current_rep()) if is_manager() else current_rep()
    days = max(0, min(int(request.args.get('days') or 0), 60))
    start = _start_of_today()
    end = _iso((_now_dt() + timedelta(days=days)).replace(hour=23, minute=59, second=59))

    clauses = ["appt_at != ''", 'appt_at >= ?', 'appt_at <= ?',
               'stage NOT IN (%s)' % ','.join('?' * len(TERMINAL_STAGES))]
    params = [start, end] + list(TERMINAL_STAGES)
    if rep:
        clauses.append('rep=?'); params.append(rep)
    with get_db() as db:
        rows = db.execute('SELECT * FROM leads WHERE %s ORDER BY appt_at'
                          % ' AND '.join(clauses), params).fetchall()
        # The other half of the same question: booked, but nobody knows when.
        # Counted rather than listed, because the fix is per-lead on the board.
        mclauses = ["stage='appt_set'", "appt_at=''"]
        mparams = []
        if rep:
            mclauses.append('rep=?'); mparams.append(rep)
        missing = db.execute('SELECT COUNT(*) c FROM leads WHERE %s'
                             % ' AND '.join(mclauses), mparams).fetchone()['c']
    out = []
    for r in rows:
        d = _lead_row(r)
        d['appt_label'] = _appt_label(d['appt_at'])
        d['appt_past'] = d['appt_at'] < _now()
        out.append(d)
    return jsonify({'appointments': out, 'missing_time': missing, 'days': days})


@app.route('/api/tasks', methods=['GET'])
@login_required
def list_tasks():
    _reconcile_funnel()
    rep   = request.args.get('rep') if is_manager() else current_rep()
    scope = request.args.get('scope', 'open')     # open | today | overdue | all
    clauses, params = ['t.done=0'], []
    if scope == 'all':
        clauses = []
    if rep:
        clauses.append('t.rep=?'); params.append(rep)
    if scope == 'today':
        clauses.append('t.due_at <= ?'); params.append(_iso(_now_dt().replace(hour=23, minute=59, second=59)))
    elif scope == 'overdue':
        clauses.append('t.due_at <= ?'); params.append(_now())
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with get_db() as db:
        rows = db.execute(f'''
            SELECT t.*, l.first_name, l.last_name, l.company, l.phone, l.stage, l.lead_type
            FROM tasks t JOIN leads l ON l.id = t.lead_id
            {where} ORDER BY t.done, t.due_at LIMIT 500''', params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['lead_name'] = (f"{r['first_name']} {r['last_name']}".strip() or r['company'] or '(no name)')
        d['overdue'] = (not d['done']) and d['due_at'] <= _now()
        out.append(d)
    return jsonify(out)

@app.route('/api/leads/<lead_id>/tasks', methods=['POST'])
@login_required
def add_task(lead_id):
    data = request.get_json(force=True)
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        due = data.get('due_at') or _iso(_now_dt() + timedelta(days=1))
        tid = str(uuid.uuid4())
        db.execute('INSERT INTO tasks (id, lead_id, rep, kind, title, due_at, created_at) '
                   'VALUES (?,?,?,?,?,?,?)',
                   (tid, lead_id, row['rep'], data.get('kind', 'call'),
                    data.get('title', ''), due, _now()))
        _refresh_next_action(db, lead_id)
    return jsonify({'ok': True, 'id': tid}), 201

@app.route('/api/tasks/<task_id>', methods=['PATCH'])
@login_required
def update_task(task_id):
    data = request.get_json(force=True)
    with get_db() as db:
        t = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not t:
            return jsonify({'error': 'Not found'}), 404
        if not is_manager() and t['rep'] != current_rep():
            return jsonify({'error': 'Forbidden'}), 403
        if 'done' in data:
            done = 1 if data['done'] else 0
            db.execute('UPDATE tasks SET done=?, done_at=? WHERE id=?',
                       (done, _now() if done else '', task_id))
            # Only on the not-done -> done edge. Re-checking an already-finished
            # task must not log the call twice.
            if done and not t['done']:
                # Completing a task logs the WORK, not a note about the work.
                #
                # This logged kind='note' for every task, and `note` is not in
                # OUTREACH_KINDS -- so ticking off "Call #2" from My Day did not
                # touch last_activity_at, did not count toward the daily target,
                # and did not reach the leaderboard. The lead then went on
                # showing as stalled. The same call logged from the Outreach tab
                # counted fully: one behaviour, two sets of books, and the rep
                # working the follow-up engine we built was the one who looked
                # idle. Every cadence step's kind is already a real outreach
                # kind; a task that is genuinely just a reminder still logs a
                # note, which is what it is.
                kind = t['kind'] if t['kind'] in OUTREACH_KINDS else 'note'
                _log_activity(db, t['lead_id'], kind,
                              body=f'✓ Completed: {t["title"] or t["kind"]}', rep=t['rep'])
                if t['enrollment_id']:
                    _advance_cadence(db, t['enrollment_id'])
        if 'due_at' in data:
            db.execute('UPDATE tasks SET due_at=? WHERE id=?', (data['due_at'], task_id))
        _refresh_next_action(db, t['lead_id'])
    return jsonify({'ok': True})

# ── Cadences ──────────────────────────────────────────────────────────────────

@app.route('/api/cadences')
@login_required
def list_cadences():
    return jsonify(CADENCES)

def _create_step_task(db, lead_id, rep, enrollment_id, started_at, step):
    """Materialize one cadence step as a task, due started_at + offset_days."""
    try:
        start_dt = datetime.strptime(started_at, '%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        start_dt = _now_dt()
    due = _iso(start_dt + timedelta(days=int(step.get('offset_days', 0))))
    db.execute('INSERT INTO tasks (id, lead_id, rep, kind, title, due_at, enrollment_id, created_at) '
               'VALUES (?,?,?,?,?,?,?,?)',
               (str(uuid.uuid4()), lead_id, rep, step.get('kind', 'call'),
                step.get('title', ''), due, enrollment_id, _now()))

def _enroll(db, lead_id, rep, cadence_id):
    """Start a cadence and materialize its first task. Returns the enrollment
    id, or '' if the cadence is unknown or the lead is already in it.

    Shared by the manual Enroll button and the automatic enrollment that fires
    on a stage change. One active enrollment per cadence per lead, which is
    what makes the automatic path safe to re-run.
    """
    cad = CADENCE_BY_ID.get(cadence_id)
    if not cad:
        return ''
    exists = db.execute(
        'SELECT id FROM cadence_enrollments WHERE lead_id=? AND cadence_id=? AND active=1',
        (lead_id, cadence_id)).fetchone()
    if exists:
        return ''
    eid = str(uuid.uuid4())
    started = _now()
    db.execute('INSERT INTO cadence_enrollments (id, lead_id, cadence_id, step_idx, started_at, active) '
               'VALUES (?,?,?,0,?,1)', (eid, lead_id, cadence_id, started))
    if cad['steps']:
        _create_step_task(db, lead_id, rep, eid, started, cad['steps'][0])
    _log_activity(db, lead_id, 'system', body=f'Enrolled in cadence: {cad["name"]}')
    _refresh_next_action(db, lead_id)
    return eid

@app.route('/api/leads/<lead_id>/enroll', methods=['POST'])
@login_required
def enroll_cadence(lead_id):
    cadence_id = request.get_json(force=True).get('cadence_id')
    if cadence_id not in CADENCE_BY_ID:
        return jsonify({'error': 'Unknown cadence'}), 400
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        eid = _enroll(db, lead_id, row['rep'], cadence_id)
        if not eid:
            return jsonify({'error': 'Already enrolled in this cadence'}), 409
    return jsonify({'ok': True, 'enrollment_id': eid}), 201

def _advance_cadence(db, enrollment_id):
    e = db.execute('SELECT * FROM cadence_enrollments WHERE id=? AND active=1', (enrollment_id,)).fetchone()
    if not e:
        return
    cad = CADENCE_BY_ID.get(e['cadence_id'])
    if not cad:
        return
    next_idx = e['step_idx'] + 1
    if next_idx >= len(cad['steps']):
        db.execute('UPDATE cadence_enrollments SET active=0, step_idx=? WHERE id=?', (next_idx, enrollment_id))
        _log_activity(db, e['lead_id'], 'system', body=f'Completed cadence: {cad["name"]}')
        return
    db.execute('UPDATE cadence_enrollments SET step_idx=? WHERE id=?', (next_idx, enrollment_id))
    # Enrollment has no rep column; the task owner is the lead's rep.
    lead = db.execute('SELECT rep FROM leads WHERE id=?', (e['lead_id'],)).fetchone()
    _create_step_task(db, e['lead_id'], lead['rep'] if lead else '', enrollment_id,
                      e['started_at'], cad['steps'][next_idx])

# ── Funnel reconciliation — the estimator's events land here ─────────────────
#
# Stage used to be a card a rep remembered to drag, which meant the leaderboard
# and every close-rate number downstream measured *bookkeeping discipline*
# rather than selling. These four rules move a lead on the events that actually
# happened, so the numbers describe the business instead of the paperwork.

# Reaching a stage starts the cadence that belongs to it. Nothing enrolled
# leads automatically before this: four well-written cadences existed and only
# ever ran when someone clicked Enroll — which is precisely the moment a busy
# rep does not.
STAGE_CADENCE = {
    'new':                'new_lead_7touch',
    'estimate_presented': 'estimate_followup',
}

# A realtor is not a homeowner and must not get the homeowner's seven touches:
# partner development is a relationship on a much longer clock, which is what
# `partner_nurture` encodes (call, coffee at two weeks, recap at six).
PARTNER_CADENCE = 'partner_nurture'


def _cadence_for(stage, lead_type):
    """Which cadence a lead entering `stage` should start, or '' for none."""
    if stage == 'new' and lead_type in PARTNER_TYPES:
        return PARTNER_CADENCE
    return STAGE_CADENCE.get(stage, '')

TERMINAL_STAGES = ('won', 'lost')

# The progression a deal makes, in order, ending in the sale. Two stages are
# deliberately NOT rungs on it:
#
#   `lost` is an exit. It sits last in STAGES so the board reads left to right,
#   which makes its raw index 7 -- above `won`'s 6. Any rank comparison over raw
#   STAGE_KEYS scores every dead deal as having got further than a signed one.
#
#   `follow_up` is a holding state, not a step forward: it is where a quoted
#   deal waits. On the ladder it sat between "quoted" and "won", so every deal
#   that closed straight off the estimate was credited with a follow-up that
#   never happened, and the row became "whichever is larger". A lead sitting in
#   follow-up HAS been quoted, so it ranks as `estimate_presented` -- which is
#   the true statement about how far it got.
LADDER = [k for k in OPEN_STAGES if k != 'follow_up'] + ['won']
LADDER_RANK = {k: i for i, k in enumerate(LADDER)}
# Every stage -> the rung it counts as. Off the ladder is -1.
STAGE_RUNG = dict(LADDER_RANK)
STAGE_RUNG['follow_up'] = LADDER_RANK['estimate_presented']


def _stage_rank(stage):
    """Position on the ladder. Unknown stages rank first so they never block."""
    try:
        return STAGE_KEYS.index(stage)
    except ValueError:
        return 0


def _auto_advance(db, lead_id, target, reason):
    """Move a lead forward on a real event. Returns True if the stage changed.

    **This is the manual override.** A rep is always allowed to be ahead of the
    automation: a lead already at or past `target` is left exactly where the
    rep put it. The one exception is a signature, which is ground truth — if a
    lead was marked lost and the customer then signs, the signature wins, and
    the activity log says so rather than silently rewriting history.
    """
    row = db.execute('SELECT stage, rep, lead_type FROM leads WHERE id=?',
                     (lead_id,)).fetchone()
    if not row:
        return False
    old = row['stage']
    if old == target:
        return False
    if target == 'won':
        if old == 'won':
            return False
    elif old in TERMINAL_STAGES or _stage_rank(target) <= _stage_rank(old):
        return False

    won_at = _now() if target == 'won' else ''
    db.execute('UPDATE leads SET stage=?, won_at=?, updated_at=? WHERE id=?',
               (target, won_at, _now(), lead_id))
    _log_stage_change(db, lead_id, old, target, rep=row['rep'], reason=reason)
    if target in TERMINAL_STAGES:
        db.execute('UPDATE tasks SET done=1, done_at=? WHERE lead_id=? AND done=0',
                   (_now(), lead_id))
        db.execute('UPDATE cadence_enrollments SET active=0 WHERE lead_id=?', (lead_id,))
    else:
        auto = _cadence_for(target, row['lead_type'])
        if auto:
            _enroll(db, lead_id, row['rep'], auto)
    _refresh_next_action(db, lead_id)
    return True


def _lead_for_event(db, ev):
    """Which lead an estimate belongs to — by lead id, else by Den contact."""
    if ev.get('lead_id'):
        r = db.execute('SELECT id FROM leads WHERE id=?', (ev['lead_id'],)).fetchone()
        if r:
            return r['id']
    if ev.get('contact_id'):
        r = db.execute('SELECT id FROM leads WHERE crm_contact_id=? '
                       'ORDER BY updated_at DESC LIMIT 1', (ev['contact_id'],)).fetchone()
        if r:
            return r['id']
    return ''


# state the estimate reached → the stage that implies
_FUNNEL_STAGE = {
    'sent':   'estimate_presented',
    'viewed': 'estimate_presented',
    'signed': 'won',
    # A lost estimate is deliberately NOT auto-lost as a lead. Plenty get
    # re-quoted, and marking the lead dead would close the tasks that win it
    # back. The rep decides; the activity log makes sure they know.
    'lost': '',
    'declined': '',          # the old name, still arriving from older records
}


def _apply_quoted_value(db, lead_id, ev):
    """Put the estimate's real total on the lead, and say so on the timeline.

    Logged rather than silently swapped: a rep who guessed $30k and quoted $12k
    should see their own number move and know why. The note is also the only
    record that the two ever differed, which is the raw material for finding out
    whether this team forecasts high.
    """
    value = ev.get('value') or 0
    if not value:
        return
    row = db.execute('SELECT est_value FROM leads WHERE id=?', (lead_id,)).fetchone()
    if not row or round(row['est_value'] or 0, 2) == round(value, 2):
        return
    was = row['est_value'] or 0
    db.execute('UPDATE leads SET est_value=?, updated_at=? WHERE id=?',
               (value, _now(), lead_id))
    _log_activity(db, lead_id, 'system',
                  body=f'Value updated from the estimate: ${was:,.0f} → ${value:,.0f}')


def _reconcile_funnel():
    """Apply the estimator's funnel events to their leads. Safe to call often.

    Called on the reads a rep or manager actually makes, rather than from a
    background thread: the events are already durable in the shared table, so
    the only thing a sweep adds is latency between the signature and the board
    catching up — and every screen that would show the difference triggers one.
    Costs a single indexed SELECT when nothing is pending.
    """
    try:
        events = pfunnel.claim_pending()
    except Exception as exc:
        print(f'[funnel] could not read events: {exc}')
        return []
    if not events:
        return []
    signed = []
    with get_db() as db:
        for ev in events:
            lead_id = _lead_for_event(db, ev)
            if not lead_id:
                continue
            db.execute('UPDATE leads SET estimate_id=?, updated_at=? WHERE id=?',
                       (ev['estimate_id'], _now(), lead_id))
            # The quoted number lands as soon as it EXISTS, not at signature.
            #
            # est_value is a rep's guess typed before anyone measured anything,
            # and it used to stay that guess right up to the moment a contract
            # was signed -- so "Pipeline $", the forecast the whole company is
            # run against, was a column of estimates about estimates while the
            # real figure sat in the estimator the entire time. Once a customer
            # has been quoted, the quote is what the deal is worth.
            _apply_quoted_value(db, lead_id, ev)
            if ev['state'] in ('lost', 'declined'):
                _log_activity(db, lead_id, 'system',
                              body='Estimate marked lost — the lead is still open')
                continue
            target = _FUNNEL_STAGE.get(ev['state'], '')
            if not target:
                continue
            reason = {'sent': 'estimate sent', 'viewed': 'customer opened the estimate',
                      'signed': 'contract signed'}[ev['state']]
            if _auto_advance(db, lead_id, target, reason) and target == 'won':
                signed.append((lead_id, ev))
    # The Den handoff runs outside the DB block — it is a network call, and
    # holding a write transaction open across Base44's latency is how the
    # whole CRM ends up waiting on someone else's API.
    for lead_id, ev in signed:
        _push_to_den(lead_id, estimate=ev)
    return events


# ── The customer channel ─────────────────────────────────────────────────────
#
# The CRM owned a homeowner from the door knock to the signature and sent them
# NOTHING in that whole window. Not a decision -- the only mailer in the repo
# lived inside estimator/app.py, so the estimator could email a customer and
# this app could not. It now shares portal/mail.py.
#
# The boundary is deliberate and narrow. The estimator already covers
# estimate -> signature (it sends the estimate, notifies on first view, chases
# unsigned ones, mails the signed copy). The Den owns everything after the
# signature. What nobody covered is the middle: an appointment gets booked and
# the customer hears nothing until somebody knocks on their door. In home
# services that is the single largest cause of a no-show, and a no-show is a
# wasted drive plus a dead lead.
#
# These SEND rather than draft, and that does not weaken the draft-only rule in
# the Outreach queue. That rule is about cold outreach at volume, where 1:1 mail
# from a rep's own Gmail is what avoids needing a sending domain, SPF/DKIM and
# warmup. A confirmation for an appointment the customer just booked is
# transactional: expected, one recipient, no volume. The estimator has always
# sent this class of mail through the same infrastructure.

APPT_REMIND_HOURS = int(os.environ.get('SALESCRM_APPT_REMIND_HOURS', '24'))


def _appt_email(lead, rep, kind):
    """(subject, html) for a customer's appointment mail, or None if unsendable.

    Plain, short and useful: when, who, where, and how to move it. A homeowner
    reading this on a phone wants four facts, not a brochure.
    """
    to = (lead.get('email') or '').strip()
    if not to or not lead.get('appt_at'):
        return None
    when = _appt_label(lead['appt_at'])
    who = pusers.display_name(rep)
    reply = pusers.email_of(rep)
    first = (lead.get('first_name') or '').strip()
    greeting = f'Hi {first},' if first else 'Hi,'
    where = ', '.join(x for x in (lead.get('address'), lead.get('city')) if x)
    head = ('Your roof inspection is confirmed' if kind == 'confirm'
            else 'Reminder: your roof inspection')
    lead_in = ("Thanks for setting this up — here are the details."
               if kind == 'confirm' else "Just so it is on your radar:")
    html = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
        font-size:15px;line-height:1.55;color:#1f2937;max-width:520px">
      <p>{_esc_html(greeting)}</p>
      <p>{_esc_html(lead_in)}</p>
      <table style="border-collapse:collapse;margin:16px 0">
        <tr><td style="padding:4px 14px 4px 0;color:#6b7280">When</td>
            <td style="padding:4px 0"><b>{_esc_html(when)}</b></td></tr>
        <tr><td style="padding:4px 14px 4px 0;color:#6b7280">Who</td>
            <td style="padding:4px 0">{_esc_html(who)}, Project One Roofing</td></tr>
        {f'<tr><td style="padding:4px 14px 4px 0;color:#6b7280">Where</td><td style="padding:4px 0">{_esc_html(where)}</td></tr>' if where else ''}
      </table>
      <p>It takes about 45 minutes. You do not need to be home for the roof
         itself, but it helps if we can talk through what we find afterwards.</p>
      <p>If that time no longer works, just reply to this email and we will
         move it — no problem at all.</p>
      <p style="margin-top:22px">{_esc_html(who)}<br>
         Project One Roofing<br>
         <a href="https://projectoneroofingcolorado.com">projectoneroofingcolorado.com</a></p>
    </div>"""
    return (f'{head} — {when}', html, to, reply)


def _esc_html(text):
    return (str(text or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _send_appt_mail(db, lead_id, kind):
    """Send a confirmation or reminder, once per appointment TIME. Never raises.

    The claim is a conditional UPDATE rather than a read-then-write: two
    gunicorn workers run this loop and both would otherwise pass the same check
    and mail the customer twice. Whoever's UPDATE changes a row owns the send.
    """
    col = 'appt_confirmed_for' if kind == 'confirm' else 'appt_reminded_for'
    row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    if not row:
        return False
    lead = dict(row)
    appt = lead.get('appt_at') or ''
    if not appt or appt <= _now() or lead.get('dnc'):
        return False
    if lead.get(col) == appt or not pmail.configured():
        return False
    built = _appt_email(lead, lead['rep'], kind)
    if not built:
        return False

    claimed = db.execute(f'UPDATE leads SET {col}=? WHERE id=? AND {col} IS NOT ?',
                         (appt, lead_id, appt)).rowcount
    if not claimed:
        return False

    subject, html, to, reply = built
    try:
        sent = pmail.send(subject, html, to, bcc=reply)
    except Exception as exc:          # pmail.send does not raise; belt and braces
        print(f'[appt] send failed for {lead_id}: {exc}')
        sent = False
    if not sent:
        # Release the claim so the next pass retries. A customer who never got
        # the confirmation must not be recorded as having had one.
        db.execute(f'UPDATE leads SET {col}=? WHERE id=?', ('', lead_id))
        return False
    word = 'Confirmation' if kind == 'confirm' else 'Reminder'
    _log_activity(db, lead_id, 'email', rep=lead['rep'],
                  body=f'✉️ {word} emailed to {to} — {_appt_label(appt)}')
    return True


def _check_appt_reminders():
    """Day-before reminders. Runs on the hourly loop; safe to call often."""
    if not pmail.configured():
        return 0
    until = _iso(_now_dt() + timedelta(hours=APPT_REMIND_HOURS))
    sent = 0
    with get_db() as db:
        due = db.execute(
            "SELECT id FROM leads WHERE appt_at != '' AND appt_at > ? AND appt_at <= ? "
            "AND email != '' AND dnc = 0 AND appt_reminded_for != appt_at "
            "AND stage NOT IN (%s)" % ','.join('?' * len(TERMINAL_STAGES)),
            [_now(), until] + list(TERMINAL_STAGES)).fetchall()
        for r in due:
            if _send_appt_mail(db, r['id'], 'remind'):
                sent += 1
    return sent


def _appt_loop():
    time.sleep(45)                    # let the app finish booting
    while True:
        try:
            _check_appt_reminders()
        except Exception as exc:
            print(f'[appt] reminder check failed: {exc}')
        try:
            _check_storm_alerts()
        except Exception as exc:
            print(f'[storm] alert check failed: {exc}')
        time.sleep(1800)


# ── Storms: which of OUR people are under it ─────────────────────────────────
#
# `hail/join.affected()` is the module CLAUDE.md calls the reason for building
# any of the storm work -- "a hail map is a commodity; what no vendor can sell
# us is the swath against our own customers" -- and until now it had NO CALLER.
# Nor did portal/geo.py, outside its own backfill. Three finished, tested pieces
# with no wire between them.
#
# This is the wire. It is deliberately the only part of it that knows what a
# lead is: `join` refuses to learn about stages on purpose, because coupling the
# storm archive to this schema guarantees it breaks the next time one is
# renamed. So the tier rule lives here and the geometry lives there.
#
# NOTE ON DATA: hail.db is filled by an ingest that is not written yet (the dev
# sandbox cannot reach NOAA, and untested network code is worse than none). This
# endpoint therefore returns an empty affected list until that lands -- which is
# the honest answer, and is why it reports `geocoded` and `skipped` rather than
# just a count that would read the same whether the storm missed us or the data
# never arrived.


def _lead_tier(lead):
    """Which priority band a lead belongs to, in `hail.join.TIERS` terms.

    Order is a business rule that lives in `join.TIERS`: a Roof Care Plan
    subscriber is a contractual obligation and is contacted first, always, even
    when a past customer took bigger hail.
    """
    stage = lead.get('stage')
    if stage == 'won':
        return 'rcp' if (lead.get('plan') and lead.get('billing')) else 'past_customer'
    if stage == 'lost':
        return 'lost_estimate'
    if stage in OPEN_STAGES:
        # A bulk-imported row nobody has ever spoken to is not an "open lead"
        # in any sense a rep would recognise -- it is a cold address.
        return 'cold' if lead.get('import_batch') and not lead.get('last_activity_at') \
            else 'open_lead'
    return 'cold'


def _storm_records(db, rep=None):
    """Every lead we could place on the map, plus the count we could not.

    Coordinates come from the shared geocode cache and NEVER from the network:
    this runs inside a request, and a storm brief that waits on a geocoder is a
    storm brief nobody reads.
    """
    clauses, params = ["address != ''"], []
    if rep:
        clauses.append('rep=?'); params.append(rep)
    rows = db.execute(
        'SELECT id, first_name, last_name, company, address, city, state, zip, '
        'phone, email, stage, rep, plan, billing, customer_id, import_batch, '
        'last_activity_at FROM leads WHERE ' + ' AND '.join(clauses),
        params).fetchall()
    # Customers we have decided not to work for again drop out here rather than
    # at the email, so they are absent from every consumer of this join at once
    # -- the alert, the storm brief, anything built on it later. A `caution`
    # customer stays: that is a warning for the rep, not an exclusion.
    banned = {r['id'] for r in db.execute(
        "SELECT id FROM customers WHERE flag='do_not_serve'")}   # cf NOT_BANNED_SQL
    # The whole cache in one query. Asking `pgeo.lookup()` per lead is a round
    # trip each: measured at 40,000 leads that was 8.5 SECONDS to assemble the
    # records, against 11ms for the geometry they were being assembled for --
    # and this runs inside a request, on a box with two workers.
    points = pgeo.all_points()
    records, unplaced = [], 0
    for r in rows:
        if r['customer_id'] in banned:
            continue
        hit = points.get(pgeo.norm_address(r['address'], r['city'],
                                           r['state'], r['zip']))
        if not hit:
            unplaced += 1
            continue
        # Built straight from the row rather than through _lead_row(): that
        # parses timestamps for the stall detector and resolves plan and
        # service metadata, none of which a storm join reads, and it does it
        # once per customer we own.
        d = dict(r)
        records.append({
            'id': d['id'],
            'name': (f"{d['first_name']} {d['last_name']}").strip()
                    or d['company'] or '(no name)',
            'address': d['address'], 'city': d['city'],
            'phone': d['phone'], 'email': d['email'],
            'stage': d['stage'],
            'stage_label': STAGE_META.get(d['stage'], {}).get('label', d['stage']),
            'rep': d['rep'], 'tier': _lead_tier(d),
            'lat': hit[0], 'lng': hit[1]})
    return records, unplaced


@app.route('/api/storm/<path:event_id>')
@login_required
def storm_affected(event_id):
    """Who of ours sits under one stored storm, worst-hit first, by tier.

    Every number that could be understated is reported rather than implied.
    `skipped` and `unplaced` are the customers we could not place at all --
    dropping them silently is how a storm brief says "40 affected" when the
    truth is 400, which reads as a small storm and gets nobody out of bed.
    """
    swath = hstorms.load_swath(event_id)
    if swath is None:
        return jsonify({'error': 'Unknown storm'}), 404
    event = hstorms.get_event(event_id)
    rep = request.args.get('rep') if is_manager() else current_rep()
    min_size = request.args.get('min_size')

    with get_db() as db:
        records, unplaced = _storm_records(db, rep)
    hits, skipped = hjoin.affected(swath, records,
                                   min_size=float(min_size) if min_size else None)
    summary = hjoin.summarize(swath, hits, skipped)
    return jsonify({
        'event': event,
        # Carried out to the caller so nothing downstream has to guess whether
        # this is radar over the roof or somebody's phone call from down the
        # road. They are different claims and a customer must never be told the
        # weaker one as though it were the stronger.
        'source': (event or {}).get('source'),
        'summary': summary,
        'by_tier': hjoin.by_tier(hits),
        'placed': len(records), 'skipped': skipped, 'unplaced': unplaced,
    })


@app.route('/api/storms')
@login_required
def storm_list():
    """Stored storms, newest first — what /api/storm/<id> can be asked about."""
    return jsonify(hstorms.events(since=request.args.get('since'),
                                  min_size=float(request.args['min_size'])
                                  if request.args.get('min_size') else None,
                                  limit=min(int(request.args.get('limit') or 50), 200)))


# ── Storm alerts: to the REP, never to the customer ──────────────────────────
#
# The obvious version of this mails the homeowner: "hail hit your street, book
# an inspection". Deliberately not built, because Northern Colorado gets a lot
# of qualifying hail and a list that hears from you on every swath stops being
# a list by the third season -- and the people it burns first are the ones who
# already chose you. Worse, it makes the company sound like the storm chasers
# everyone is tired of.
#
# So the alert goes to the REP: here are YOUR people under this storm, worst-hit
# first, in the order the business works them. A human decides who is worth a
# call and what to say. That is slower and it is the point.

STORM_ALERT_MIN_IN = float(os.environ.get('SALESCRM_STORM_ALERT_MIN_IN', '1.0'))
# Tiers a rep is told about. A cold imported address is a canvassing lead, not
# somebody to phone -- it belongs on the map, not in an inbox.
STORM_ALERT_TIERS = ('rcp', 'past_customer', 'open_lead', 'lost_estimate')


def _storm_alert_html(rep, event, by_tier, total):
    who = pusers.display_name(rep)
    size = (event or {}).get('max_size_in') or 0
    date = (event or {}).get('event_date') or ''
    src = (event or {}).get('source') or ''
    # The source rides along because it changes what the rep may claim. Radar
    # over the roof and a spotter's phone call from down the road are different
    # facts, and only one of them survives a customer asking "how do you know?"
    src_note = ('Radar-estimated hail size over each address.'
                if src == 'mrms_mesh' else
                'From storm reports called in near these addresses — treat the '
                'size as nearby, not measured at the roof.')
    rows = []
    for tier in STORM_ALERT_TIERS:
        hits = by_tier.get(tier) or []
        if not hits:
            continue
        rows.append(f'<tr><td colspan="3" style="padding:14px 0 4px;font-size:11px;'
                    f'letter-spacing:1.2px;text-transform:uppercase;color:#6b7280">'
                    f'{_esc_html(TIER_LABELS[tier])} ({len(hits)})</td></tr>')
        for h in hits[:40]:
            where = ', '.join(x for x in (h.get('address'), h.get('city')) if x)
            rows.append(
                f'<tr>'
                f'<td style="padding:3px 12px 3px 0"><b>{_esc_html(h.get("name"))}</b><br>'
                f'<span style="color:#6b7280;font-size:12px">{_esc_html(where)}</span></td>'
                f'<td style="padding:3px 12px 3px 0;white-space:nowrap">'
                f'{h.get("hail_size_in", 0):.2f}"</td>'
                f'<td style="padding:3px 0;white-space:nowrap;color:#6b7280">'
                f'{_esc_html(h.get("phone") or "")}</td></tr>')
    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
        font-size:14px;line-height:1.5;color:#1f2937;max-width:640px">
      <p>{_esc_html(who)} — <b>{total} of your customers</b> are under the hail
         from {_esc_html(date)} (up to {size:.2f}").</p>
      <p style="font-size:12px;color:#6b7280">{_esc_html(src_note)}</p>
      <table style="border-collapse:collapse;width:100%">{''.join(rows)}</table>
      <p style="margin-top:20px;font-size:12px;color:#6b7280">
        Worst-hit first within each group. Roof Care Plan members come first
        regardless of hail size — we owe them the call.</p>
    </div>"""


TIER_LABELS = {
    'rcp':           'Roof Care Plan members',
    'past_customer': 'Past customers',
    'open_lead':     'Open leads',
    'lost_estimate': 'Estimates we lost',
    'cold':          'Cold addresses',
}


def _notify_storm(event_id):
    """Mail each rep the list of THEIR people under one storm. Returns count.

    Claimed per (storm, rep) with an INSERT that fails on the primary key, so
    two workers running this loop cannot both mail the same rep -- the same
    guard shape as the appointment confirmations.
    """
    swath = hstorms.load_swath(event_id)
    if swath is None or not swath.cells:
        return 0
    event = hstorms.get_event(event_id)
    sent = 0
    with get_db() as db:
        records, _unplaced = _storm_records(db)
        hits, _skipped = hjoin.affected(swath, records, min_size=STORM_ALERT_MIN_IN)
        by_rep = {}
        for h in hits:
            if h.get('tier') in STORM_ALERT_TIERS:
                by_rep.setdefault(h['rep'], []).append(h)

        for rep, rep_hits in by_rep.items():
            to = pusers.email_of(rep)
            if not to:
                continue
            try:
                db.execute('INSERT INTO storm_notices (event_id, rep, affected, sent_at) '
                           'VALUES (?,?,?,?)', (event_id, rep, len(rep_hits), _now()))
            except sqlite3.IntegrityError:
                continue                      # already told, or another worker won
            html = _storm_alert_html(rep, event, hjoin.by_tier(rep_hits), len(rep_hits))
            date = (event or {}).get('event_date') or ''
            if pmail.send(f'🌩 {len(rep_hits)} of your customers were under the '
                          f'{date} hail', html, to):
                sent += 1
            else:
                db.execute('DELETE FROM storm_notices WHERE event_id=? AND rep=?',
                           (event_id, rep))
    return sent


def _check_storm_alerts():
    """Alert on storms nobody has been told about yet. Safe to call often."""
    if not pmail.configured():
        return 0
    try:
        events = hstorms.events(min_size=STORM_ALERT_MIN_IN, limit=20)
    except Exception as exc:
        print(f'[storm] could not read the archive: {exc}')
        return 0
    if not events:
        return 0
    with get_db() as db:
        told = {r['event_id'] for r in db.execute('SELECT DISTINCT event_id FROM storm_notices')}
    sent = 0
    for ev in events:
        if ev['event_id'] not in told:
            sent += _notify_storm(ev['event_id'])
    return sent


# ── The Den (Base44) handoff ──────────────────────────────────────────────────

def _crm_headers():
    return {'Authorization': f'Bearer {BASE44_TOKEN}', 'Content-Type': 'application/json'}

def _den_payloads(lead):
    """Build the Contact + Project payloads (also used by the dry-run endpoint)."""
    name = (f"{lead['first_name']} {lead['last_name']}").strip() or lead['company'] or 'Unknown'
    assigned = f"{lead['rep']}@{EMAIL_DOMAIN}"
    contact = {
        'name': name, 'first_name': lead['first_name'], 'last_name': lead['last_name'],
        'phone': lead['phone'], 'email': lead['email'],
        'street_address': lead['address'], 'city': lead['city'],
        'state': lead['state'], 'zip_code': lead['zip'],
        'source': lead['source'] or 'referral', 'assigned_to': assigned,
        'location_id': CO_LOCATION_ID,
    }
    service_label = SERVICE_META.get(lead.get('service') or 'roofing', SERVICES[0])['label']
    # Recurring plans carry their plan name + billing cadence into the project name/notes
    # so The Den/production can see it's a maintenance agreement, not a one-time job.
    pname = f"{service_label} - {name}"
    notes = ''
    plan = PLAN_BY_ID.get(lead.get('plan') or '')
    if plan:
        pname = f"{plan['name']} ({service_label}) - {name}"
        suffix = BILLING_SUFFIX.get(lead.get('billing') or '', '')
        notes = f"Recurring maintenance plan: {plan['name']}"
        if lead.get('est_value'):
            notes += f" — {int(lead['est_value'])}{suffix}"
    project = {
        'name': pname, 'source': lead['source'] or 'referral',
        'assigned_to': assigned, 'notes': notes,
        'location_id': CO_LOCATION_ID,
        # 'lead' is not one of The Den's statuses and every job pushed with it
        # sat outside the pipeline reports. A job only reaches this function
        # once the customer has signed, so `contracted` is both valid and true.
        'status': 'contracted',
        'assigned_salesperson': assigned,
        'client_name': name, 'client_phone': lead['phone'],
        'client_email': lead['email'],
        'street_address': lead['address'], 'city': lead['city'],
        'state': lead['state'] or 'CO', 'zip_code': lead['zip'],
    }
    if lead.get('est_value'):
        project['contract_value'] = float(lead['est_value'])
    return contact, project

def _find_existing_contact(lead):
    """Dedup: reuse a Base44 contact matching phone/email before creating one."""
    if not http:
        return ''
    try:
        r = http.get(f'{BASE44_URL}/entities/Contact', headers=_crm_headers(), timeout=15)
        r.raise_for_status()
        phone = (lead['phone'] or '').strip()
        email = (lead['email'] or '').strip().lower()
        for c in r.json():
            if phone and (c.get('phone') or '').strip() == phone:
                return c.get('id', '')
            if email and (c.get('email') or '').strip().lower() == email:
                return c.get('id', '')
    except Exception as e:
        print(f'[Den] dedup lookup failed: {e}')
    return ''

def _den_document(project_id, lead, estimate):
    """File the signed contract on the job as a link to the signing page.

    Not an upload: Base44's external API refuses the UploadFile integration to
    our token (blanket 405), so the estimator's own push has always fallen back
    to linking its hosted page. Doing the same here means the contract is on
    the job the moment production picks it up, instead of never — which is
    where it landed while the handoff was broken.
    """
    token = (estimate or {}).get('share_token') or ''
    if not token or not project_id:
        return ''
    name = (f"{lead['first_name']} {lead['last_name']}").strip() or lead['company'] or 'Customer'
    base = (os.environ.get('PUBLIC_BASE_URL') or '').rstrip('/')
    if not base:
        base = request.url_root.rstrip('/') if request else ''
    doc = {
        'name': f'Signed Contract - {name}',
        'type': 'contract',
        'project_id': project_id,
        'file_url': f'{base}/sign/{token}',
        'file_type': 'text/html',
        'description': (f'Signed {(estimate.get("signed_at") or "")[:10]}. '
                        f'Linked automatically when the lead closed in The Pipeline.'),
        'share_with_client': False,
    }
    try:
        r = http.post(f'{BASE44_URL}/entities/Document', json=doc,
                      headers=_crm_headers(), timeout=20)
        r.raise_for_status()
        return (r.json() or {}).get('id', '')
    except Exception as e:
        print(f'[Den] contract document failed: {e}')
        return ''


def _push_to_den(lead_id, estimate=None):
    """Create (or reuse) a Base44 Contact + Project. Returns a result dict.

    Called at signature, which is the only moment a sale becomes production
    work. `estimate` is the funnel row for the signed estimate, when there is
    one — it carries the share token used to file the contract on the job.
    """
    if not BASE44_TOKEN:
        return {'ok': False, 'error': 'BASE44_TOKEN not configured'}
    if not http:
        return {'ok': False, 'error': 'requests library unavailable'}
    with get_db() as db:
        lead = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    if not lead:
        return {'ok': False, 'error': 'Lead not found'}
    lead = dict(lead)
    if lead.get('crm_project_id'):
        return {'ok': True, 'already': True,
                'crm_contact_id': lead['crm_contact_id'],
                'crm_project_id': lead['crm_project_id']}
    contact_payload, project_payload = _den_payloads(lead)

    contact_id = lead.get('crm_contact_id') or _find_existing_contact(lead)
    if not contact_id:
        if not contact_payload['name'] or contact_payload['name'] == 'Unknown':
            return {'ok': False, 'error': 'A name is required to push to The Den'}
        try:
            r = http.post(f'{BASE44_URL}/entities/Contact', json=contact_payload,
                          headers=_crm_headers(), timeout=15)
            r.raise_for_status()
            contact_id = r.json().get('id', '')
        except Exception as e:
            return {'ok': False, 'error': f'Contact create failed: {e}'}

    project_payload['contact_id'] = contact_id
    project_id = ''
    try:
        r = http.post(f'{BASE44_URL}/entities/Project', json=project_payload,
                      headers=_crm_headers(), timeout=15)
        r.raise_for_status()
        project_id = r.json().get('id', '')
    except Exception as e:
        print(f'[Den] project create failed: {e}')

    doc_id = _den_document(project_id, lead, estimate) if estimate else ''

    with get_db() as db:
        db.execute('UPDATE leads SET crm_contact_id=?, crm_project_id=?, updated_at=? WHERE id=?',
                   (contact_id, project_id, _now(), lead_id))
        note = f'Handed to The Den (contact {contact_id[:8]}…'
        note += f', job {project_id[:8]}…)' if project_id else ', job create failed)'
        if doc_id:
            note += ' — signed contract filed'
        # Worth saying out loud on the timeline: the location is a dropdown in
        # The Den's own form and may not be settable through the API, so the
        # job can land unassigned and drop out of every Colorado report.
        note += '. Check the location on the job.'
        _log_activity(db, lead_id, 'system', body=note, rep=lead['rep'])
    return {'ok': True, 'crm_contact_id': contact_id, 'crm_project_id': project_id,
            'document_id': doc_id}

@app.route('/api/leads/<lead_id>/convert', methods=['POST'])
@login_required
def convert_lead(lead_id):
    """Manual 'Push to Den'. dry_run=1 returns the payloads without writing."""
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
    if request.args.get('dry_run') == '1':
        contact, project = _den_payloads(dict(row))
        return jsonify({'dry_run': True, 'contact': contact, 'project': project})
    return jsonify(_push_to_den(lead_id))

# ── Estimator link ────────────────────────────────────────────────────────────

@app.route('/api/leads/<lead_id>/start-estimate', methods=['POST'])
@login_required
def start_estimate(lead_id):
    """Hand the rep to the estimator, carrying this lead's id.

    **Nothing is written to The Den here.** It used to create a Base44 Contact
    at this point, which put every lead that ever got a quote into the
    production system whether or not it closed — and, worse, set
    `crm_contact_id`, which the Won handoff read as "already pushed" and so
    never pushed anything at all. The Den now receives a job at exactly one
    moment: signature.

    An *existing* Den contact is still looked up, because a past customer
    already has one and reusing it keeps the estimator's contact search and
    bid-vs-actual working. A lookup is a read; it creates nothing.
    """
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
    lead = dict(row)
    contact_id = lead['crm_contact_id'] or _find_existing_contact(lead)
    with get_db() as db:
        if contact_id and contact_id != lead['crm_contact_id']:
            db.execute('UPDATE leads SET crm_contact_id=?, updated_at=? WHERE id=?',
                       (contact_id, _now(), lead_id))
        _log_activity(db, lead_id, 'system', body='Started an estimate', rep=lead['rep'])
    name = quote((f"{lead['first_name']} {lead['last_name']}").strip() or lead['company'])
    return jsonify({'ok': True, 'contact_id': contact_id, 'lead_id': lead_id,
                    'estimator_url': (f'{ESTIMATOR_URL}/?contact={contact_id}'
                                      f'&lead={quote(lead_id)}&name={name}')})

@app.route('/api/leads/<lead_id>/estimate', methods=['GET'])
@login_required
def lead_estimate(lead_id):
    """Estimate state for a lead: the funnel first, The Den second.

    The funnel is the live answer — it knows this lead's estimates were sent,
    opened and signed, with timestamps, and it knows it without a network call.
    The Den read stays for what only production has: permits pulled, documents
    filed, the job's status after we handed it over.
    """
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
    estimates = pfunnel.for_lead(lead_id)
    if not estimates and row['crm_contact_id']:
        estimates = pfunnel.for_contact(row['crm_contact_id'])
    contact_id = row['crm_contact_id']
    if not contact_id or not (BASE44_TOKEN and http):
        return jsonify({'linked': bool(estimates), 'estimates': estimates,
                        'documents': [], 'projects': []})
    docs, projects = [], []
    try:
        rd = http.get(f'{BASE44_URL}/entities/Document', headers=_crm_headers(), timeout=15)
        if rd.ok:
            docs = [d for d in rd.json() if d.get('contact_id') == contact_id]
    except Exception as e:
        print(f'[Den] document read failed: {e}')
    try:
        rp = http.get(f'{BASE44_URL}/entities/Project', headers=_crm_headers(), timeout=15)
        if rp.ok:
            projects = [p for p in rp.json() if p.get('contact_id') == contact_id]
    except Exception as e:
        print(f'[Den] project read failed: {e}')
    return jsonify({'linked': True, 'contact_id': contact_id,
                    'estimates': estimates,
                    'documents': docs[:20], 'projects': projects[:20]})

# ── Partners ──────────────────────────────────────────────────────────────────

PARTNER_PAGE = 200


@app.route('/api/partners')
@login_required
def list_partners():
    """The partner BOOK — the people who send you business, not every record.

    This returned every partner-type lead with no limit and no search, which was
    right for a book of twenty realtors and became a way to hang a phone the
    moment prospecting started importing HOAs and brokerages by the thousand:
    one DOM card per row, tens of thousands of rows. The Pipeline board already
    learned this lesson and moved its search to the server; this tab was left
    rendering the whole table.

    So the default is a relationship, not a lead type: somebody you have
    actually touched, or who has sent you something. A cold open-data row nobody
    has ever called is a *prospect*, and prospects are already worked in the ⚡
    Outreach queue — listing them here as "partners" both drowns the real book
    and overstates it. They are counted (`cold_prospects`) so they are visibly
    excluded rather than missing, and `?q=` searches every partner record,
    relationship or not, so nothing is unreachable.
    """
    q = (request.args.get('q') or '').strip().lower()
    limit = min(int(request.args.get('limit') or PARTNER_PAGE), 1000)

    clauses = ['lead_type IN (%s)' % ','.join('?' * len(PARTNER_TYPES))]
    params = list(PARTNER_TYPES)
    if not is_manager():
        clauses.append('rep=?'); params.append(current_rep())
    if q:
        esc = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        clauses.append(
            "LOWER(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'') || ' ' ||"
            "      COALESCE(company,'')    || ' ' || COALESCE(email,'')     || ' ' ||"
            "      COALESCE(phone,'')      || ' ' || COALESCE(city,''))"
            " LIKE ? ESCAPE '\\'")
        params.append(f'%{esc}%')
    else:
        # The relationship test. `referred_by` is indexed by leads_rep_idx only
        # incidentally, but this subquery is over a table of partners' children,
        # which is small even when the partner list is not.
        clauses.append("(last_activity_at != '' OR "
                       " id IN (SELECT referred_by FROM leads WHERE referred_by != ''))")
    where = 'WHERE ' + ' AND '.join(clauses)

    with get_db() as db:
        counts = {r['referred_by']: r['c'] for r in db.execute(
            "SELECT referred_by, COUNT(*) c FROM leads WHERE referred_by != '' GROUP BY referred_by"
        ).fetchall()}
        won = {r['referred_by']: r['c'] for r in db.execute(
            "SELECT referred_by, COUNT(*) c FROM leads WHERE referred_by != '' "
            "AND stage='won' GROUP BY referred_by").fetchall()}
        rows = db.execute(
            f'SELECT * FROM leads {where} ORDER BY last_activity_at DESC, updated_at DESC '
            f'LIMIT ?', params + [limit]).fetchall()
        total = db.execute(f'SELECT COUNT(*) c FROM leads {where}', params).fetchone()['c']

        cold_clauses = ['lead_type IN (%s)' % ','.join('?' * len(PARTNER_TYPES)),
                        "last_activity_at = ''",
                        "id NOT IN (SELECT referred_by FROM leads WHERE referred_by != '')"]
        cold_params = list(PARTNER_TYPES)
        if not is_manager():
            cold_clauses.append('rep=?'); cold_params.append(current_rep())
        cold = db.execute('SELECT COUNT(*) c FROM leads WHERE ' + ' AND '.join(cold_clauses),
                          cold_params).fetchone()['c']

    # Projected, not the whole lead row. A book of 200 shipped 204 KB of
    # research notes, citations and storm summaries to draw a card showing a
    # name, a type and three numbers -- over a phone connection, on a screen
    # whose whole job is "who should I call".
    out = []
    for r in rows:
        d = _lead_row(r)
        out.append({k: d[k] for k in ('id', 'name', 'lead_type', 'company',
                                      'phone', 'email', 'city', 'stage',
                                      'stage_label', 'stage_color')}
                   | {'referrals_total': counts.get(r['id'], 0),
                      'referrals_won': won.get(r['id'], 0)})
    # Most referrals first: the book is read to decide who to call, and the
    # partner who has sent four jobs is not the one to scroll past.
    out.sort(key=lambda d: (-d['referrals_total'], -d['referrals_won']))
    return jsonify({'partners': out, 'total': total, 'cold_prospects': cold,
                    'limit': limit, 'searching': bool(q)})

# ── Prospecting: bulk import, dedupe, suppression ─────────────────────────────
#
# Partner prospects arrive in bulk from prospector/ (Colorado open data) or from
# a browser harvest dropped into prospector/inbox. Two rules govern every row:
#
#   • Suppression beats everything. A partner who asked to be left alone must
#     not resurface tomorrow in a batch sourced from a different dataset.
#   • Import is idempotent. Re-running a batch inserts nothing, so a run that
#     died halfway is always safe to retry.
#
# Dedupe lives HERE and nowhere else. `POST /api/leads` stays duplicate-friendly
# on purpose: the cross-sell "Pitch" button deliberately creates a second lead
# for the same person as a separate deal, and matching on contact details there
# would break it.

# Text keys read off an incoming prospect row. Anything else in the row is
# ignored rather than rejected, so a source can carry extra provenance fields
# without this needing to know about them.
PROSPECT_TEXT_FIELDS = ['first_name', 'last_name', 'company', 'phone', 'email',
                        'address', 'city', 'state', 'zip', 'website', 'license_no',
                        'source_ref', 'hook']

# What makes two rows the same partner. `source_ref` carries the weight for
# open-data rows that have no contact details at all — a DORA HOA record is a
# name, a city and a licence number, and without a stable key every re-import
# would duplicate it.
_DEDUPE_KEYS = ('phone_norm', 'email_norm', 'license_no', 'source_ref')

def _host_of(url):
    """Bare hostname from a URL or domain, lowercased, no scheme and no www."""
    s = (url or '').strip().lower()
    if '://' in s:
        s = s.split('://', 1)[1]
    s = s.split('/', 1)[0].split('?', 1)[0]
    return s[4:] if s.startswith('www.') else s

def _email_domain(email_norm):
    return email_norm.rsplit('@', 1)[1] if '@' in email_norm else ''

def _suppression_index(db):
    """{kind: set(values)} — one query, rather than one per imported row."""
    idx = {'email': set(), 'phone': set(), 'domain': set()}
    for r in db.execute('SELECT kind, value FROM suppressions'):
        idx[r['kind']].add(r['value'])
    return idx

def _suppressed_by(supp, phone_norm, email_norm, website):
    """Which suppression rule blocks this contact, or '' if none does."""
    if email_norm and email_norm in supp['email']:
        return 'email'
    if phone_norm and phone_norm in supp['phone']:
        return 'phone'
    domain = _email_domain(email_norm) or _host_of(website)
    if domain and domain in supp['domain']:
        return 'domain'
    return ''

def _dedupe_index(db):
    """Existing lead ids keyed by each dedupe value, e.g. {'phone_norm': {...}}.

    Held in memory for the length of one import so rows are also checked against
    *each other* — a single batch routinely lists the same brokerage twice.
    """
    idx = {k: {} for k in _DEDUPE_KEYS}
    cols = ', '.join(_DEDUPE_KEYS)
    for r in db.execute(f'SELECT id, {cols} FROM leads'):
        for k in _DEDUPE_KEYS:
            if r[k]:
                idx[k].setdefault(r[k], r['id'])
    return idx

def _assignment_pool(assign):
    """Usernames to spread a batch across. Returns (pool, error_message)."""
    if assign == 'round_robin':
        users = pusers.all_users()
        pool = [u['username'] for u in users if u['role'] == 'rep']
        # A one-person shop has no 'rep'-role accounts; fall back to everyone
        # rather than silently importing nothing.
        return (pool or [u['username'] for u in users], '')
    if assign:
        if not pusers.get(assign):
            return ([], f'Unknown rep "{assign}"')
        return ([assign], '')
    return ([current_rep()], '')

@app.route('/api/prospects/import', methods=['POST'])
@admin_required
def import_prospects():
    """Bulk-create partner leads from a sourced list. Manager-only.

    Body: {rows[], lead_type, source, assign, dry_run}
      assign  — 'round_robin', a username, or omitted (assigns to the caller)
      dry_run — classify every row and write nothing
    """
    data = request.get_json(force=True)
    rows = data.get('rows')
    if not isinstance(rows, list):
        return jsonify({'error': 'rows must be a list'}), 400
    if len(rows) > 5000:
        return jsonify({'error': 'Batch too large (max 5000 rows)'}), 400

    lead_type = data.get('lead_type', 'referral_partner')
    if lead_type not in LEAD_TYPE_KEYS:
        return jsonify({'error': 'Invalid lead type'}), 400
    service = data.get('service', 'roofing')
    if service not in SERVICE_KEYS:
        return jsonify({'error': 'Invalid service'}), 400

    pool, err = _assignment_pool((data.get('assign') or '').strip())
    if err:
        return jsonify({'error': err}), 400

    dry_run = bool(data.get('dry_run'))
    source  = (data.get('source') or 'prospecting').strip()
    batch   = (data.get('batch') or f'{source}-{_now()}').strip()

    counts  = {'inserted': 0, 'duplicate': 0, 'suppressed': 0, 'invalid': 0}
    details = []

    with get_db() as db:
        supp = _suppression_index(db)
        seen = _dedupe_index(db)

        for i, raw in enumerate(rows):
            if not isinstance(raw, dict):
                counts['invalid'] += 1
                details.append({'row': i, 'status': 'invalid', 'reason': 'not an object'})
                continue

            row = {f: str(raw.get(f) or '').strip() for f in PROSPECT_TEXT_FIELDS}
            row['phone_norm'] = _norm_phone(row['phone'])
            row['email_norm'] = _norm_email(row['email'])
            label = (f"{row['first_name']} {row['last_name']}").strip() or row['company']

            if not label:
                counts['invalid'] += 1
                details.append({'row': i, 'status': 'invalid', 'reason': 'no name or company'})
                continue
            if not any(row[k] for k in _DEDUPE_KEYS):
                # Nothing stable to match on: importing it would duplicate on
                # every future run. Reject rather than poison the list.
                counts['invalid'] += 1
                details.append({'row': i, 'status': 'invalid', 'name': label,
                                'reason': 'no phone, email, licence or source_ref to dedupe on'})
                continue

            blocked = _suppressed_by(supp, row['phone_norm'], row['email_norm'], row['website'])
            if blocked:
                counts['suppressed'] += 1
                details.append({'row': i, 'status': 'suppressed', 'name': label,
                                'reason': f'{blocked} on the suppression list'})
                continue

            hit = next((seen[k][row[k]] for k in _DEDUPE_KEYS
                        if row[k] and row[k] in seen[k]), None)
            if hit:
                counts['duplicate'] += 1
                details.append({'row': i, 'status': 'duplicate', 'name': label,
                                'lead_id': hit})
                continue

            lid = str(uuid.uuid4())
            rep = pool[counts['inserted'] % len(pool)]
            fields = dict(row)
            fields.update({
                'id': lid, 'lead_type': lead_type, 'service': service,
                'source': 'prospecting', 'temperature': 'cold', 'stage': 'new',
                'rep': rep, 'import_batch': batch,
                'est_value': float(raw.get('est_value') or 0),
                'icp_score': int(raw.get('icp_score') or 0),
                'created_at': _now(), 'updated_at': _now(),
            })
            if not dry_run:
                cols = ','.join(fields.keys())
                ph   = ','.join('?' * len(fields))
                db.execute(f'INSERT INTO leads ({cols}) VALUES ({ph})', list(fields.values()))
                _log_activity(db, lid, 'system', rep=rep,
                              body=f'Imported from {source} (batch {batch})')
            # Index it either way, so a dry run reports intra-batch duplicates
            # exactly as the real run would.
            for k in _DEDUPE_KEYS:
                if row[k]:
                    seen[k].setdefault(row[k], lid)
            counts['inserted'] += 1
            details.append({'row': i, 'status': 'inserted', 'name': label,
                            'lead_id': lid, 'rep': rep})

        if dry_run:
            db.rollback()

    return jsonify({'batch': batch, 'source': source, 'lead_type': lead_type,
                    'dry_run': dry_run, 'assigned_to': pool, 'total': len(rows),
                    'counts': counts, 'details': details[:500],
                    'details_truncated': len(details) > 500}), 200 if dry_run else 201

@app.route('/api/prospects/batches')
@admin_required
def list_batches():
    """Import history, derived from leads.import_batch — no separate table."""
    with get_db() as db:
        rows = db.execute(
            "SELECT import_batch AS batch, COUNT(*) AS leads, MIN(created_at) AS first_at, "
            "       MAX(created_at) AS last_at, "
            "       SUM(CASE WHEN stage='won' THEN 1 ELSE 0 END) AS won, "
            "       SUM(CASE WHEN last_activity_at != '' THEN 1 ELSE 0 END) AS touched "
            "FROM leads WHERE import_batch != '' "
            "GROUP BY import_batch ORDER BY first_at DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

# ── Suppressions (opt-outs) ───────────────────────────────────────────────────

SUPPRESSION_KINDS = ('email', 'phone', 'domain')

def _norm_suppression(kind, value):
    """Store suppressions in the same shape the lead columns are matched in."""
    if kind == 'email':
        return _norm_email(value)
    if kind == 'phone':
        return _norm_phone(value)
    return _host_of(value)

@app.route('/api/suppressions', methods=['GET'])
@login_required
def list_suppressions():
    with get_db() as db:
        rows = db.execute('SELECT * FROM suppressions ORDER BY created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/suppressions', methods=['POST'])
@login_required
def add_suppression():
    """Any rep can opt a contact out — asking twice is the thing to prevent."""
    data  = request.get_json(force=True)
    kind  = (data.get('kind') or '').strip()
    if kind not in SUPPRESSION_KINDS:
        return jsonify({'error': f'kind must be one of {", ".join(SUPPRESSION_KINDS)}'}), 400
    value = _norm_suppression(kind, data.get('value'))
    if not value:
        return jsonify({'error': f'Not a usable {kind}'}), 400
    sid = str(uuid.uuid4())
    with get_db() as db:
        existing = db.execute('SELECT * FROM suppressions WHERE kind=? AND value=?',
                              (kind, value)).fetchone()
        if existing:
            return jsonify(dict(existing)), 200      # already opted out; not an error
        db.execute('INSERT INTO suppressions (id, kind, value, reason, created_by, created_at) '
                   'VALUES (?,?,?,?,?,?)',
                   (sid, kind, value, (data.get('reason') or '').strip(),
                    current_rep(), _now()))
        # Flag the matching leads so they drop out of any queue built from here
        # on, not just out of future imports.
        if kind == 'email':
            db.execute('UPDATE leads SET dnc=1, updated_at=? WHERE email_norm=?', (_now(), value))
        elif kind == 'phone':
            db.execute('UPDATE leads SET dnc=1, updated_at=? WHERE phone_norm=?', (_now(), value))
        row = db.execute('SELECT * FROM suppressions WHERE id=?', (sid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/suppressions/<sid>', methods=['DELETE'])
@admin_required
def delete_suppression(sid):
    """Manager-only: undoing an opt-out is not a rep's call."""
    with get_db() as db:
        cur = db.execute('DELETE FROM suppressions WHERE id=?', (sid,))
        if not cur.rowcount:
            return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})

# ── Outreach drafts ───────────────────────────────────────────────────────────
#
# Rendered server-side and handed to the rep as a *draft* — the UI opens it in
# their own Gmail compose window. Nothing here ever sends. That is what keeps
# this 1:1 mail from a real person rather than bulk mail, which in turn is why
# the whole thing needs no sending subdomain, no DKIM setup and no warmup.

# 0 prior touches opens, 1-2 follows up, 3+ closes the loop. Partners who have
# ignored three emails are not persuaded by a fourth.
def _draft_step(touches):
    if touches <= 0:
        return 'first'
    return 'followup' if touches < 3 else 'breakup'

def _fill(text, ctx):
    """Substitute slots, then drop paragraphs an empty slot left blank.

    `{hook}` sits in its own paragraph precisely so an unresearched lead gets a
    shorter email rather than a visible gap where the personal line should be.
    Same applies to Nimbus's `{research_hook}` and `{storm_hook}`.
    """
    for key, val in ctx.items():
        text = text.replace('{' + key + '}', val)
    return '\n\n'.join(p for p in (p.strip() for p in text.split('\n\n')) if p)


def _first_line(text):
    """First non-empty line of a possibly-JSON research blob, for the draft.

    research_notes stores a JSON body from Perplexity; the first line/summary
    is usually the highest-value fact and fits into an outreach template.
    Falls back to the raw string when it isn't JSON.
    """
    s = (text or '').strip()
    if not s:
        return ''
    if s.startswith('{'):
        try:
            data = json.loads(s)
            for key in ('summary', 'decision_maker', 'headline', 'news', 'note'):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip().split('\n', 1)[0][:200]
                if isinstance(v, dict):
                    for k2 in ('name', 'title', 'text'):
                        vv = v.get(k2)
                        if isinstance(vv, str) and vv.strip():
                            return vv.strip().split('\n', 1)[0][:200]
        except (ValueError, TypeError):
            pass
    return s.split('\n', 1)[0][:200]

def _render_draft(lead, step, rep_name):
    """{'subject','body','step'} for a lead, or None if no template applies."""
    tpls = TEMPLATES.get('templates', {})
    tpl = tpls.get(lead.get('lead_type')) or tpls.get('referral_partner')
    if not tpl or step not in tpl:
        return None
    first = (lead.get('first_name') or '').strip()
    # research_hook / storm_hook are optional and safe to leave blank; _fill()
    # drops paragraphs an empty slot left blank, matching the {hook} pattern.
    ctx = {
        'greeting':      f'Hi {first},' if first else 'Hi there,',
        'first_name':    first,
        'company':       (lead.get('company') or '').strip(),
        'city':          (lead.get('city') or '').strip() or 'the Front Range',
        'hook':          (lead.get('hook') or '').strip(),
        'research_hook': _first_line(lead.get('research_notes') or ''),
        'storm_hook':    _first_line(lead.get('recent_storm') or ''),
        'rep_name':      rep_name,
        'rep_first':     rep_name.split(' ')[0] if rep_name else '',
    }
    sig = TEMPLATES.get('signature', '')
    body = _fill(tpl[step]['body'], ctx)
    if sig:
        body += '\n\n' + _fill(sig, ctx)
    return {'subject': _fill(tpl[step]['subject'], ctx), 'body': body, 'step': step}

@app.route('/api/leads/<lead_id>/draft')
@login_required
def lead_draft(lead_id):
    """The email a rep would send this partner right now."""
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        touches = db.execute(
            'SELECT COUNT(*) c FROM activities WHERE lead_id = ? AND kind IN (%s)'
            % ','.join('?' * len(OUTREACH_KINDS)),
            [lead_id] + list(OUTREACH_KINDS)).fetchone()['c']
    step = request.args.get('step') or _draft_step(touches)
    draft = _render_draft(dict(row), step, pusers.display_name(current_rep()))
    if not draft:
        return jsonify({'error': 'No template for this lead type'}), 404
    return jsonify(draft)

# ── Outreach queue ────────────────────────────────────────────────────────────
#
# What a rep actually works. Two halves, and the split is the point:
#
#   due  — tasks already scheduled, i.e. cadence re-touches on partners they've
#          met. For partner development this is the half that converts; a
#          realtor who sees you five times refers, a hundred who see you once
#          don't.
#   new  — net-new cold cards, only enough to top the day up to the target.
#
# Because re-touches count toward the number, sourcing demand is roughly half
# the daily target, which is what keeps the free data lasting.

DAILY_TARGET  = int(os.environ.get('SALESCRM_DAILY_TARGET', '40'))
# Never put the same partner back in front of a rep inside this window. Mirrors
# the non-negotiable 7-day cooldown the outreach skills already enforce.
COOLDOWN_DAYS = int(os.environ.get('SALESCRM_COOLDOWN_DAYS', '7'))

def _end_of_today():
    return _iso(_now_dt().replace(hour=23, minute=59, second=59))

def _start_of_today():
    return _iso(_now_dt().replace(hour=0, minute=0, second=0))

@app.route('/api/queue/today')
@login_required
def queue_today():
    """Today's touch list for one rep, capped at the daily target."""
    rep = request.args.get('rep') or current_rep()
    if rep != current_rep() and not is_manager():
        return jsonify({'error': 'Forbidden'}), 403
    target = max(1, min(int(request.args.get('target') or DAILY_TARGET), 200))
    cooldown = _iso(_now_dt() - timedelta(days=COOLDOWN_DAYS))

    with get_db() as db:
        supp = _suppression_index(db)

        # Already-scheduled work: cadence steps and manual follow-ups due by
        # end of day. dnc=0 keeps opted-out partners out even mid-cadence.
        due = [dict(r) for r in db.execute(
            'SELECT t.id, t.kind, t.title, t.due_at, t.lead_id, '
            '       l.first_name, l.last_name, l.company, l.phone, l.email, '
            '       l.website, l.city, l.stage, l.lead_type, l.icp_score, l.hook '
            'FROM tasks t JOIN leads l ON l.id = t.lead_id '
            'WHERE t.rep = ? AND t.done = 0 AND t.due_at <= ? AND l.dnc = 0 '
            '  AND l.' + NOT_BANNED_SQL + ' '
            'ORDER BY t.due_at LIMIT ?', (rep, _end_of_today(), target)).fetchall()]
        for d in due:
            d['name'] = (f"{d['first_name']} {d['last_name']}").strip() or d['company']
            d['overdue'] = d['due_at'] < _now()

        done_today = db.execute(
            'SELECT COUNT(*) c FROM activities WHERE rep = ? AND created_at >= ? '
            'AND kind IN (%s)' % ','.join('?' * len(OUTREACH_KINDS)),
            [rep, _start_of_today()] + list(OUTREACH_KINDS)).fetchone()['c']

        # Top up with net-new. Anything with an open task is already in `due`,
        # and anything touched inside the cooldown is deliberately left alone.
        room = max(0, target - len(due) - done_today)
        fresh = []
        if room:
            rows = db.execute(
                "SELECT * FROM leads "
                "WHERE rep = ? AND stage = 'new' AND dnc = 0 "
                "  AND " + NOT_BANNED_SQL + " "
                "  AND (last_activity_at = '' OR last_activity_at < ?) "
                "  AND id NOT IN (SELECT lead_id FROM tasks WHERE done = 0) "
                "ORDER BY icp_score DESC, created_at ASC LIMIT ?",
                (rep, cooldown, room * 3)).fetchall()
            for r in rows:
                # Re-check suppression here, not just at import: a domain added
                # to the list this morning has to drop rows imported last week.
                if _suppressed_by(supp, r['phone_norm'], r['email_norm'], r['website']):
                    continue
                fresh.append(_lead_row(r))
                if len(fresh) >= room:
                    break

        # Attach the email each card would send. One grouped query for the
        # touch counts rather than one per card.
        ids = [d['lead_id'] for d in due] + [f['id'] for f in fresh]
        touches = {}
        if ids:
            rows = db.execute(
                'SELECT lead_id, COUNT(*) c FROM activities '
                'WHERE lead_id IN (%s) AND kind IN (%s) GROUP BY lead_id'
                % (','.join('?' * len(ids)), ','.join('?' * len(OUTREACH_KINDS))),
                ids + list(OUTREACH_KINDS)).fetchall()
            touches = {r['lead_id']: r['c'] for r in rows}

    rep_name = pusers.display_name(rep)
    for item, lid in ([(d, d['lead_id']) for d in due] + [(f, f['id']) for f in fresh]):
        item['draft'] = _render_draft(item, _draft_step(touches.get(lid, 0)), rep_name)

    return jsonify({
        'rep': rep, 'target': target, 'done_today': done_today,
        'cooldown_days': COOLDOWN_DAYS,
        'due': due, 'new': fresh,
        'remaining': max(0, target - done_today),
    })

@app.route('/api/queue/assign', methods=['POST'])
@admin_required
def queue_assign():
    """Spread a manager's unworked prospects across the reps who'll call them.

    Import parks a batch on whoever ran it unless told otherwise; this is the
    "now hand it out" step. Only untouched `new` leads move, so a rep never
    loses a partner they've already spoken to.
    """
    data = request.get_json(force=True)
    from_rep = (data.get('from_rep') or current_rep()).strip()
    limit    = max(1, min(int(data.get('limit') or 1000), 5000))

    reps = data.get('reps')
    if reps:
        unknown = [r for r in reps if not pusers.get(r)]
        if unknown:
            return jsonify({'error': f'Unknown rep(s): {", ".join(unknown)}'}), 400
    else:
        pool, err = _assignment_pool('round_robin')
        if err:
            return jsonify({'error': err}), 400
        reps = pool
    if not reps:
        return jsonify({'error': 'No reps to assign to'}), 400

    with get_db() as db:
        rows = db.execute(
            "SELECT id FROM leads WHERE rep = ? AND stage = 'new' AND dnc = 0 "
            "AND last_activity_at = '' AND import_batch != '' "
            "ORDER BY icp_score DESC, created_at ASC LIMIT ?",
            (from_rep, limit)).fetchall()
        if not data.get('dry_run'):
            for i, r in enumerate(rows):
                to = reps[i % len(reps)]
                db.execute('UPDATE leads SET rep = ?, updated_at = ? WHERE id = ?',
                           (to, _now(), r['id']))
                # No activity logged here, unlike the manual path: these are
                # untouched imported rows with no history to annotate, and a
                # 5,000-row batch would write 5,000 timeline entries nobody
                # reads. The tasks still have to follow the lead.
                _move_open_tasks(db, r['id'], to)

    per = {}
    for i in range(len(rows)):
        rep = reps[i % len(reps)]
        per[rep] = per.get(rep, 0) + 1
    return jsonify({'from_rep': from_rep, 'moved': len(rows), 'per_rep': per,
                    'dry_run': bool(data.get('dry_run'))})

# ── Dashboard / scorecards / coaching ─────────────────────────────────────────

def _date_bounds(days):
    start = _iso((_now_dt() - timedelta(days=days)).replace(hour=0, minute=0, second=0))
    return start

def _ladder_case(col):
    """SQL CASE mapping a stage column to its rung index, -1 off the ladder.

    Built from LADDER, which is our own constant -- no request data reaches
    this string.
    """
    whens = ' '.join(f"WHEN '{k}' THEN {i}" for k, i in STAGE_RUNG.items())
    return f'CASE {col} {whens} ELSE -1 END'


def _cohort_funnel(db, since, rep):
    """Of the leads picked up since `since`, how far did each one get?

    THIS is the question the dashboard's "Funnel" was pretending to answer. It
    showed current stage counts, so a lead that went new -> won appeared only
    under Won and the ladder above it read as empty. "Of the doors we knocked,
    where do we lose people" had no answer in the tool, even though every
    stage_change has been on the timeline the whole time.

    A lead's furthest rung is the highest of: where it entered, where it is
    now, and every stage it was ever moved to. Reaching a rung implies every
    rung below it, so the counts are monotonic and read as a funnel.

    Leads that ended `lost` stay in the cohort at whatever rung they reached --
    dropping them would flatter every conversion rate on the screen.
    """
    clauses, params = ['created_at >= ?'], [since]
    if rep:
        clauses.append('rep = ?'); params.append(rep)
    # Bulk-imported prospects are excluded, and counted separately rather than
    # silently dropped. One open-data pull adds tens of thousands of rows that
    # nobody sourced and most of which will never be worked; mixed into the
    # cohort they drown the few hundred real doorstep and referral leads, and
    # the panel reports on the size of the last import instead of on the sales
    # process. Measured at 36k imported against 400 worked: every conversion
    # rate on the screen read about 1%.
    clauses.append("import_batch = ''")
    where = ' AND '.join(clauses)

    rows = db.execute(f"""
        SELECT MAX({_ladder_case('l.entry_stage')},
                   {_ladder_case('l.stage')},
                   COALESCE((SELECT MAX({_ladder_case('a.outcome')})
                             FROM activities a
                             WHERE a.lead_id = l.id AND a.kind = 'stage_change'), -1)
               ) AS furthest,
               COUNT(*) AS c
        FROM leads l WHERE {where} GROUP BY furthest""", params).fetchall()

    bclauses, bparams = ['created_at >= ?', "import_batch != ''"], [since]
    if rep:
        bclauses.append('rep = ?'); bparams.append(rep)
    bulk = db.execute('SELECT COUNT(*) c FROM leads WHERE ' + ' AND '.join(bclauses),
                      bparams).fetchone()['c']

    reached = {k: 0 for k in LADDER}
    cohort = 0
    for r in rows:
        cohort += r['c']
        if r['furthest'] < 0:
            continue
        for k in LADDER[:r['furthest'] + 1]:
            reached[k] += r['c']

    out, prev = [], None
    for k in LADDER:
        n = reached[k]
        out.append({
            'key': k, 'label': STAGE_META[k]['label'], 'color': STAGE_META[k]['color'],
            'reached': n,
            # Share of the whole cohort, and of the rung immediately before --
            # the second is where a leak actually shows up.
            'pct_of_cohort': round(100 * n / cohort, 1) if cohort else 0.0,
            'pct_of_prev': round(100 * n / prev, 1) if prev else None,
        })
        prev = n
    return {'cohort': cohort, 'bulk_imported': bulk, 'rungs': out}


@app.route('/api/dashboard')
@login_required
def dashboard():
    days = min(int(request.args.get('days', 30)), 365)
    since = _date_bounds(days)
    rep_filter = request.args.get('rep')
    # Reps only ever see their own numbers.
    rep = current_rep() if not is_manager() else rep_filter

    lead_where, lead_params = [], []
    if rep:
        lead_where.append('rep=?'); lead_params.append(rep)
    lw = ('WHERE ' + ' AND '.join(lead_where)) if lead_where else ''

    with get_db() as db:
        # Funnel: count of leads that have EVER reached each stage would need history;
        # here we use current stage counts + won/lost for conversion snapshot.
        stage_counts = {s: 0 for s in STAGE_KEYS}
        for r in db.execute(f'SELECT stage, COUNT(*) c FROM leads {lw} GROUP BY stage', lead_params):
            stage_counts[r['stage']] = r['c']

        new_where = lead_where + ['created_at >= ?']
        np = lead_params + [since]
        new_leads = db.execute(f'SELECT COUNT(*) c FROM leads WHERE {" AND ".join(new_where)}', np).fetchone()['c']

        won_where = lead_where + ["stage='won'", 'won_at >= ?']
        wp = lead_params + [since]
        won_row = db.execute(f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v '
                             f'FROM leads WHERE {" AND ".join(won_where)}', wp).fetchone()
        won_count, won_value = won_row['c'], won_row['v']

        lost_where = lead_where + ["stage='lost'", 'updated_at >= ?']
        lost_count = db.execute(f'SELECT COUNT(*) c FROM leads WHERE {" AND ".join(lost_where)}',
                                lead_params + [since]).fetchone()['c']

        # Open pipeline value (all open stages, regardless of date).
        open_where = lead_where + ['stage IN (%s)' % ','.join('?' * len(OPEN_STAGES))]
        pipe = db.execute(f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads '
                          f'WHERE {" AND ".join(open_where)}', lead_params + OPEN_STAGES).fetchone()

        # Activity volume in window.
        act_where, act_params = ['created_at >= ?'], [since]
        if rep:
            act_where.append('rep=?'); act_params.append(rep)
        act_rows = {r['kind']: r['c'] for r in db.execute(
            f'SELECT kind, COUNT(*) c FROM activities WHERE {" AND ".join(act_where)} GROUP BY kind',
            act_params)}

        # Source attribution + location split.
        # Windowed, like every other figure on this screen. These read
        # `new_where` rather than `lw`: they answer "where did the leads we
        # picked up in this window come from", and an all-time answer sitting
        # beside a 7-day KPI is the kind of number that quietly misdirects a
        # marketing budget. Stage counts below stay current-state on purpose --
        # they are a snapshot of the board, not a flow.
        nw_sql = ' AND '.join(new_where)
        by_source = {r['source'] or 'unknown': r['c'] for r in db.execute(
            f'SELECT source, COUNT(*) c FROM leads WHERE {nw_sql} GROUP BY source', np)}
        funnel = _cohort_funnel(db, since, rep)
        # Service-line split: open pipeline + won revenue per service.
        by_service = {}
        for s in SERVICES:
            row = db.execute(
                f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads '
                f'WHERE {" AND ".join(lead_where + ["service=?", "stage IN (%s)" % ",".join("?"*len(OPEN_STAGES))])}',
                lead_params + [s['key']] + OPEN_STAGES).fetchone()
            wrow = db.execute(
                f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads '
                f'WHERE {" AND ".join(lead_where + ["service=?", "stage=?", "won_at >= ?"])}',
                lead_params + [s['key'], 'won', since]).fetchone()
            by_service[s['key']] = {'label': s['label'], 'icon': s['icon'],
                                    'open': row['c'], 'open_value': row['v'],
                                    'won': wrow['c'], 'won_value': wrow['v']}

        # Recurring revenue: every WON deal with a billing cadence is an ACTIVE PLAN.
        # MRR normalizes each to a monthly figure (a $300/qtr plan = $100 MRR).
        # This is current-state (running book of business), not date-windowed.
        mrr = 0.0
        active_plans = 0
        plan_mix = {}
        recur_where = lead_where + ["stage='won'", "billing != ''"]
        for r in db.execute(f'SELECT est_value, billing, plan FROM leads '
                            f'WHERE {" AND ".join(recur_where)}', lead_params):
            months = BILLING_MONTHS.get(r['billing'], 1)
            mrr += (r['est_value'] or 0) / months
            active_plans += 1
            pname = (PLAN_BY_ID.get(r['plan']) or {}).get('name', 'Custom / no plan')
            plan_mix[pname] = plan_mix.get(pname, 0) + 1
        mrr = round(mrr, 0)

    decided = won_count + lost_count
    win_rate = round(100 * won_count / decided, 1) if decided else 0.0
    avg_deal = round(won_value / won_count, 0) if won_count else 0.0
    outreach = sum(act_rows.get(k, 0) for k in OUTREACH_KINDS)

    return jsonify({
        'days': days, 'rep': rep,
        'stage_counts': stage_counts,
        'stages': STAGES,
        'new_leads': new_leads,
        'won_count': won_count, 'won_value': won_value,
        'lost_count': lost_count, 'win_rate': win_rate, 'avg_deal': avg_deal,
        'pipeline_count': pipe['c'], 'pipeline_value': pipe['v'],
        'activity': act_rows, 'outreach_total': outreach,
        'by_source': by_source, 'by_service': by_service,
        # Two different questions, deliberately both here and named apart:
        # `funnel` is the cohort's progression (a flow), `stage_counts` is
        # where the board stands right now (a snapshot). They were one number
        # doing both jobs badly.
        'funnel': funnel,
        'mrr': mrr, 'arr': round(mrr * 12, 0), 'active_plans': active_plans, 'plan_mix': plan_mix,
    })

@app.route('/api/leaderboard')
@login_required
def leaderboard():
    _reconcile_funnel()
    days = min(int(request.args.get('days', 30)), 365)
    since = _date_bounds(days)
    with get_db() as db:
        reps = [r['rep'] for r in db.execute('SELECT DISTINCT rep FROM leads').fetchall()]
        board = []
        for rep in reps:
            acts = db.execute('SELECT COUNT(*) c FROM activities WHERE rep=? AND created_at >= ? '
                              'AND kind IN (%s)' % ','.join('?' * len(OUTREACH_KINDS)),
                              [rep, since] + list(OUTREACH_KINDS)).fetchone()['c']
            won = db.execute("SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads "
                             "WHERE rep=? AND stage='won' AND won_at >= ?", (rep, since)).fetchone()
            appts = db.execute("SELECT COUNT(*) c FROM activities WHERE rep=? AND created_at >= ? "
                               "AND kind='stage_change' AND outcome='appt_set'",
                               (rep, since)).fetchone()['c']
            board.append({'rep': rep, 'outreach': acts, 'appts_set': appts,
                          'won': won['c'], 'won_value': won['v']})
    board.sort(key=lambda x: (x['won'], x['won_value'], x['outreach']), reverse=True)
    return jsonify(board)

@app.route('/api/scorecard/<rep>')
@login_required
def scorecard(rep):
    if not is_manager() and rep != current_rep():
        return jsonify({'error': 'Forbidden'}), 403
    days = min(int(request.args.get('days', 30)), 365)
    since = _date_bounds(days)
    with get_db() as db:
        activity = {r['kind']: r['c'] for r in db.execute(
            'SELECT kind, COUNT(*) c FROM activities WHERE rep=? AND created_at >= ? GROUP BY kind',
            (rep, since))}
        won = db.execute("SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads "
                         "WHERE rep=? AND stage='won' AND won_at >= ?", (rep, since)).fetchone()
        lost = db.execute("SELECT COUNT(*) c FROM leads WHERE rep=? AND stage='lost' AND updated_at >= ?",
                          (rep, since)).fetchone()['c']
        new_leads = db.execute('SELECT COUNT(*) c FROM leads WHERE rep=? AND created_at >= ?',
                               (rep, since)).fetchone()['c']
        open_pipe = db.execute('SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads '
                               'WHERE rep=? AND stage IN (%s)' % ','.join('?' * len(OPEN_STAGES)),
                               [rep] + OPEN_STAGES).fetchone()
        estimates = db.execute("SELECT COUNT(*) c FROM activities WHERE rep=? AND created_at >= ? "
                               "AND kind='stage_change' AND outcome='estimate_presented'",
                               (rep, since)).fetchone()['c']
        # Avg sales cycle (days) for won deals in window.
        cyc = db.execute("SELECT created_at, won_at FROM leads WHERE rep=? AND stage='won' AND won_at >= ?",
                         (rep, since)).fetchall()
        goals = [dict(g) for g in db.execute('SELECT * FROM goals WHERE rep=?', (rep,)).fetchall()]
    cycles = []
    for c in cyc:
        try:
            a = datetime.strptime(c['created_at'], '%Y-%m-%dT%H:%M:%SZ')
            b = datetime.strptime(c['won_at'], '%Y-%m-%dT%H:%M:%SZ')
            cycles.append((b - a).days)
        except Exception:
            pass
    avg_cycle = round(sum(cycles) / len(cycles), 1) if cycles else None
    decided = won['c'] + lost
    return jsonify({
        'rep': rep, 'days': days,
        'activity': activity,
        'outreach_total': sum(activity.get(k, 0) for k in OUTREACH_KINDS),
        'new_leads': new_leads, 'estimates_presented': estimates,
        'won': won['c'], 'won_value': won['v'], 'lost': lost,
        'win_rate': round(100 * won['c'] / decided, 1) if decided else 0.0,
        'avg_deal': round(won['v'] / won['c'], 0) if won['c'] else 0.0,
        'avg_cycle_days': avg_cycle,
        'open_pipeline_count': open_pipe['c'], 'open_pipeline_value': open_pipe['v'],
        'goals': goals,
    })

@app.route('/api/stalled')
@login_required
def stalled_leads():
    """Open leads with no activity in STALL_DAYS — coaching cues."""
    clauses, params = ['stage IN (%s)' % ','.join('?' * len(OPEN_STAGES))], list(OPEN_STAGES)
    if not is_manager():
        clauses.append('rep=?'); params.append(current_rep())
    elif request.args.get('rep'):
        clauses.append('rep=?'); params.append(request.args.get('rep'))
    where = 'WHERE ' + ' AND '.join(clauses)
    with get_db() as db:
        rows = db.execute(f'SELECT * FROM leads {where}', params).fetchall()
    return jsonify([_lead_row(r) for r in rows if _is_stalled(dict(r))])

# ── Coaching notes + goals (manager) ──────────────────────────────────────────

@app.route('/api/coaching/<rep>', methods=['GET', 'POST'])
@login_required
def coaching_notes(rep):
    if not is_manager():
        return jsonify({'error': 'Forbidden'}), 403
    if request.method == 'POST':
        body = (request.get_json(force=True).get('body') or '').strip()
        if not body:
            return jsonify({'error': 'Empty note'}), 400
        with get_db() as db:
            db.execute('INSERT INTO coaching_notes (id, subject_rep, author, body, created_at) '
                       'VALUES (?,?,?,?,?)',
                       (str(uuid.uuid4()), rep, current_rep(), body, _now()))
        return jsonify({'ok': True}), 201
    with get_db() as db:
        rows = db.execute('SELECT * FROM coaching_notes WHERE subject_rep=? ORDER BY created_at DESC',
                          (rep,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/goals', methods=['GET', 'POST'])
@login_required
def goals():
    if request.method == 'POST':
        if not is_manager():
            return jsonify({'error': 'Forbidden'}), 403
        d = request.get_json(force=True)
        gid = str(uuid.uuid4())
        with get_db() as db:
            db.execute('INSERT INTO goals (id, rep, period, metric, target) VALUES (?,?,?,?,?)',
                       (gid, d.get('rep'), d.get('period'), d.get('metric'), float(d.get('target') or 0)))
        return jsonify({'ok': True, 'id': gid}), 201
    rep = request.args.get('rep') if is_manager() else current_rep()
    with get_db() as db:
        rows = db.execute('SELECT * FROM goals WHERE rep=?' if rep else 'SELECT * FROM goals',
                          (rep,) if rep else ()).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/goals/<goal_id>', methods=['DELETE'])
@admin_required
def delete_goal(goal_id):
    with get_db() as db:
        db.execute('DELETE FROM goals WHERE id=?', (goal_id,))
    return jsonify({'ok': True})

# ── Documents (per-lead files on the persistent volume) ───────────────────────

# Absolute so send_from_directory resolves regardless of the process CWD.
@app.route('/api/customers/<customer_id>')
@login_required
def get_customer(customer_id):
    """Everything we have ever done for one person.

    The question `leads` alone could not answer. A homeowner is rarely one deal
    -- the roof in spring, the siding in autumn, the re-quote after the adjuster
    comes back -- and each of those was an unrelated row with its own documents
    and its own half of the story.

    Visibility follows the LEADS, not the customer: a rep sees this person only
    if they own at least one of their deals, and then sees only their own.
    Otherwise the record becomes a way to read another rep's pipeline sideways.
    """
    with get_db() as db:
        cust = db.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
        if not cust:
            return jsonify({'error': 'Not found'}), 404
        q = 'SELECT * FROM leads WHERE customer_id=?'
        params = [customer_id]
        if not is_manager():
            q += ' AND rep=?'
            params.append(current_rep())
        leads = db.execute(q + ' ORDER BY created_at DESC', params).fetchall()
        # A customer with no leads is normally invisible -- a rep reaches people
        # through their own deals. But the Den import creates exactly this: a
        # contact we hold who never became a job here. Managers can open those,
        # or they would show up in search and 404 when clicked.
        if not leads and not is_manager():
            return jsonify({'error': 'Not found'}), 404
        lead_ids = [l['id'] for l in leads]
        marks = ','.join('?' * len(lead_ids)) or "''"
        docs = db.execute(
            'SELECT * FROM documents WHERE customer_id=? OR lead_id IN (%s) '
            'ORDER BY created_at DESC' % marks, [customer_id] + lead_ids).fetchall()
        acts = db.execute(
            'SELECT * FROM activities WHERE lead_id IN (%s) '
            'ORDER BY created_at DESC LIMIT 200' % marks, lead_ids).fetchall()

    d = dict(cust)
    d['name'] = (f"{d['first_name']} {d['last_name']}").strip() or d['company'] or '(no name)'
    d['leads'] = [_lead_row(l) for l in leads]
    d['documents'] = [_doc_row(x) for x in docs]
    # One timeline across every deal, which is the point: "we quoted them in
    # March, lost it on price, and they called back in October" is a single
    # story that lived in two places and could not be read as one.
    d['activities'] = [dict(a) for a in acts]
    d['lifetime_value'] = sum(l['est_value'] or 0 for l in leads if l['stage'] == 'won')
    d['won_count'] = sum(1 for l in leads if l['stage'] == 'won')
    d['open_count'] = sum(1 for l in leads if l['stage'] in OPEN_STAGES)
    return jsonify(d)


@app.route('/api/customers/<customer_id>', methods=['PUT'])
@login_required
def update_customer(customer_id):
    """Correct the person's details. Never re-keys them onto somebody else."""
    data = request.get_json(force=True)
    with get_db() as db:
        cust = db.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
        if not cust:
            return jsonify({'error': 'Not found'}), 404
        owned_q = 'SELECT COUNT(*) c FROM leads WHERE customer_id=?'
        owned_p = [customer_id]
        if not is_manager():
            owned_q += ' AND rep=?'
            owned_p.append(current_rep())
        if not db.execute(owned_q, owned_p).fetchone()['c']:
            return jsonify({'error': 'Not found'}), 404
        sets, params = [], []
        for f in CUSTOMER_FIELDS + ('notes',):
            if f in data:
                sets.append(f'{f}=?')
                params.append(data[f])
        if 'flag' in data:
            if data['flag'] not in CUSTOMER_FLAGS:
                return jsonify({'error': 'Invalid flag'}), 400
            # Who and when, because this is a judgement about a person that
            # other reps will act on. An unattributed "difficult customer" is
            # a rumour; one with a name and a date is information.
            sets += ['flag=?', 'flag_reason=?', 'flag_by=?', 'flag_at=?']
            params += [data['flag'], data.get('flag_reason', ''),
                       current_rep() if data['flag'] else '',
                       _now() if data['flag'] else '']
        if not sets:
            return jsonify({'error': 'Nothing to update'}), 400
        merged = dict(cust)
        merged.update({f: data[f] for f in data if f in CUSTOMER_FIELDS})
        sets += ['phone_norm=?', 'email_norm=?', 'addr_key=?', 'updated_at=?']
        params += [_norm_phone(merged.get('phone')), _norm_email(merged.get('email')),
                   _addr_key(merged), _now(), customer_id]
        db.execute('UPDATE customers SET %s WHERE id=?' % ', '.join(sets), params)
        cust = db.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
    return jsonify(dict(cust))


# ── Importing the history that lives in The Den ──────────────────────────────
#
# "Past customer" meant "past customer of THIS CRM" -- a fraction of the real
# history, because anyone who bought before this tool existed, or was entered
# straight into Base44, has no `won` lead here. Every consumer of that idea was
# quietly understating: the storm alert, lifetime value, past-customer mining.
#
# Shaped like `prospector/`: pull to a file where there is network, push the
# file in here. That keeps the fetch out of the request path, makes the import
# testable without a token, and means a run that died halfway is safe to retry.
#
# `crm_contact_id` is the dedupe key, not contact details. It is the one stable
# identifier Base44 gives us, and it survives a customer changing their phone
# number -- which contact-detail matching would read as a different person.

IMPORT_MAX_ROWS = 20000
# Base44 project statuses that mean work actually happened for this person.
# Anything else is a deal that never became a job, and importing those as `won`
# would inflate every close rate and revenue figure on the board.
DEN_DONE_STATUSES = {'contracted', 'in_progress', 'completed', 'installed', 'paid',
                     'invoiced', 'closed'}


def _den_row_to_lead(row, rep):
    """One Base44 Contact (+ its projects) as CRM fields."""
    first = (row.get('first_name') or '').strip()
    last = (row.get('last_name') or '').strip()
    if not (first or last) and (row.get('name') or '').strip():
        parts = row['name'].strip().split()
        first, last = parts[0], ' '.join(parts[1:])
    phone = (row.get('phone') or '').strip()
    email = (row.get('email') or '').strip()
    return {
        'first_name': first, 'last_name': last,
        'company': (row.get('company') or '').strip(),
        'phone': phone, 'email': email,
        'address': (row.get('street_address') or row.get('address') or '').strip(),
        'city': (row.get('city') or '').strip(),
        'state': (row.get('state') or '').strip(),
        'zip': (row.get('zip_code') or row.get('zip') or '').strip(),
        'phone_norm': _norm_phone(phone), 'email_norm': _norm_email(email),
        'rep': rep,
    }


@app.route('/api/customers/import', methods=['POST'])
@admin_required
def import_customers():
    """Load Base44 contacts and their completed jobs as customers + won leads.

    Idempotent on `crm_contact_id`: re-running inserts nothing and re-flags
    nothing, so a half-finished run is always safe to repeat.

    `is_red_flag_customer` comes across as a `caution` flag rather than
    `do_not_serve`. The two are different decisions and only one of them is
    recorded in Base44 -- promoting a red flag straight to "never work with
    them again" would silently make a call nobody made, on people we may well
    still want. A human upgrades it.
    """
    data = request.get_json(force=True)
    rows = data.get('contacts') or []
    if len(rows) > IMPORT_MAX_ROWS:
        return jsonify({'error': f'Too many rows (max {IMPORT_MAX_ROWS})'}), 400
    dry = bool(data.get('dry_run'))
    default_rep = (data.get('rep') or current_rep()).strip()

    created = linked = skipped = flagged = jobs = 0
    problems = []
    with get_db() as db:
        known = {r['crm_contact_id'] for r in db.execute(
            "SELECT DISTINCT crm_contact_id FROM customers WHERE crm_contact_id != ''")}
        for row in rows:
            cid = (row.get('id') or row.get('crm_contact_id') or '').strip()
            if not cid:
                problems.append('contact with no id skipped')
                continue
            if cid in known:
                skipped += 1
                continue
            fields = _den_row_to_lead(row, default_rep)
            if not _identifiable(fields):
                problems.append(f'{cid}: not enough detail to identify a person')
                continue
            known.add(cid)
            if dry:
                created += 1
                continue

            # Every completed job becomes a `won` lead, so the history reads as
            # what it was: three roofs over nine years, not one row.
            projects = [p for p in (row.get('projects') or [])
                        if (p.get('status') or '').lower() in DEN_DONE_STATUSES]
            made_lead = False
            for proj in projects or [None]:
                if proj is None and not row.get('import_as_customer_only'):
                    break
                lid = str(uuid.uuid4())
                fields2 = dict(fields)
                won_at = (proj or {}).get('completed_date') or \
                         (proj or {}).get('created_date') or ''
                value = float((proj or {}).get('contract_value') or 0)
                db.execute(
                    'INSERT INTO leads (id, lead_type, service, stage, entry_stage, '
                    'rep, source, temperature, est_value, won_at, crm_contact_id, '
                    'crm_project_id, import_batch, created_at, updated_at, '
                    'first_name, last_name, company, phone, email, address, city, '
                    'state, zip, phone_norm, email_norm) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (lid, 'homeowner', 'roofing', 'won', 'new', default_rep,
                     'existing_customer', 'cold', value, won_at or _now(), cid,
                     (proj or {}).get('id') or '', '',
                     won_at or _now(), _now(),
                     fields2['first_name'], fields2['last_name'], fields2['company'],
                     fields2['phone'], fields2['email'], fields2['address'],
                     fields2['city'], fields2['state'], fields2['zip'],
                     fields2['phone_norm'], fields2['email_norm']))
                _link_customer(db, lid, fields2)
                made_lead = True
                jobs += 1

            if not made_lead:
                # A contact with no completed job is still a person worth
                # holding -- they just are not a past customer, and inventing a
                # `won` lead for them would put work on the board that never
                # happened.
                cust_id = str(uuid.uuid4())
                db.execute(
                    'INSERT INTO customers (id, first_name, last_name, company, '
                    'phone, email, address, city, state, zip, phone_norm, '
                    'email_norm, addr_key, crm_contact_id, created_at, updated_at) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (cust_id, fields['first_name'], fields['last_name'],
                     fields['company'], fields['phone'], fields['email'],
                     fields['address'], fields['city'], fields['state'],
                     fields['zip'], fields['phone_norm'], fields['email_norm'],
                     _addr_key(fields), cid, _now(), _now()))
            created += 1

            # Stamp the Den id onto whichever customer this landed on, so the
            # next run recognises them.
            db.execute(
                "UPDATE customers SET crm_contact_id=? WHERE crm_contact_id='' AND id IN "
                "(SELECT customer_id FROM leads WHERE crm_contact_id=?)", (cid, cid))
            if row.get('is_red_flag_customer'):
                db.execute(
                    "UPDATE customers SET flag='caution', flag_reason=?, flag_by=?, "
                    "flag_at=? WHERE crm_contact_id=? AND flag=''",
                    ('Flagged in The Den', 'import', _now(), cid))
                flagged += 1

    return jsonify({'created': created, 'skipped_already_here': skipped,
                    'jobs': jobs, 'flagged': flagged, 'problems': problems[:50],
                    'dry_run': dry})


@app.route('/api/customers')
@login_required
def list_customers():
    """Search people, not deals. `?q=` over name, company, phone and email."""
    q = (request.args.get('q') or '').strip().lower()
    limit = min(int(request.args.get('limit') or 100), 500)
    clauses, params = [], []
    if not is_manager():
        clauses.append("c.id IN (SELECT customer_id FROM leads WHERE rep=?)")
        params.append(current_rep())
    if q:
        esc = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        clauses.append(
            "LOWER(COALESCE(c.first_name,'') || ' ' || COALESCE(c.last_name,'') || ' ' ||"
            "      COALESCE(c.company,'')    || ' ' || COALESCE(c.phone,'')     || ' ' ||"
            "      COALESCE(c.email,'')      || ' ' || COALESCE(c.address,''))"
            " LIKE ? ESCAPE '\\'")
        params.append('%' + esc + '%')
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    sql = ('SELECT c.*, COUNT(l.id) deals, '
           "SUM(CASE WHEN l.stage='won' THEN 1 ELSE 0 END) won "
           'FROM customers c LEFT JOIN leads l ON l.customer_id = c.id '
           + where + ' GROUP BY c.id ORDER BY c.updated_at DESC LIMIT ?')
    with get_db() as db:
        rows = db.execute(sql, params + [limit]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['name'] = (f"{d['first_name']} {d['last_name']}").strip() or d['company'] or '(no name)'
        out.append(d)
    return jsonify(out)


DOCS_DIR = os.path.abspath(os.path.join(DATA_DIR, 'documents'))
ALLOWED_DOC_EXT = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'heic', 'webp', 'doc', 'docx',
                   'xls', 'xlsx', 'csv', 'txt', 'zip'}
MAX_DOC_BYTES = 25 * 1024 * 1024   # 25 MB per file

def _doc_row(r):
    d = dict(r)
    d.pop('filename', None)                      # never expose the on-disk name
    d['url'] = f"/api/documents/{d['id']}/download"
    return d

@app.route('/api/leads/<lead_id>/documents', methods=['GET', 'POST'])
@login_required
def lead_documents(lead_id):
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            return jsonify({'error': 'No file'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_DOC_EXT:
            return jsonify({'error': f'File type .{ext} not allowed'}), 400
        blob = f.read()
        if len(blob) > MAX_DOC_BYTES:
            return jsonify({'error': 'File too large (25 MB max)'}), 400
        os.makedirs(DOCS_DIR, exist_ok=True)
        did = str(uuid.uuid4())
        stored = f'{did}.{ext}'
        with open(os.path.join(DOCS_DIR, stored), 'wb') as out:
            out.write(blob)
        orig = os.path.basename(f.filename)
        with get_db() as db:
            # Filed against the PERSON as well as the deal. An insurance letter
            # uploaded on the roof lead is the same customer's letter when they
            # come back for siding, and the deal it arrived on is the wrong
            # thing for it to live and die with.
            cust = db.execute('SELECT customer_id FROM leads WHERE id=?',
                              (lead_id,)).fetchone()
            db.execute('INSERT INTO documents (id, lead_id, customer_id, filename, '
                       'orig_name, size, uploaded_by, created_at) VALUES (?,?,?,?,?,?,?,?)',
                       (did, lead_id, (cust['customer_id'] if cust else ''), stored,
                        orig, len(blob), current_rep(), _now()))
            _log_activity(db, lead_id, 'system', body=f'📎 Uploaded document: {orig}')
        return jsonify({'ok': True, 'id': did}), 201
    with get_db() as db:
        rows = db.execute('SELECT * FROM documents WHERE lead_id=? ORDER BY created_at DESC',
                          (lead_id,)).fetchall()
    return jsonify([_doc_row(r) for r in rows])

@app.route('/api/documents/<doc_id>/download')
@login_required
def download_document(doc_id):
    with get_db() as db:
        doc = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not doc:
            return jsonify({'error': 'Not found'}), 404
        if not _lead_visible(db, doc['lead_id']):     # inherit the lead's visibility
            return jsonify({'error': 'Forbidden'}), 403
    return send_from_directory(DOCS_DIR, doc['filename'], as_attachment=True,
                               download_name=doc['orig_name'])

@app.route('/api/documents/<doc_id>', methods=['DELETE'])
@login_required
def delete_document(doc_id):
    with get_db() as db:
        doc = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not doc:
            return jsonify({'error': 'Not found'}), 404
        if not _lead_visible(db, doc['lead_id']):
            return jsonify({'error': 'Forbidden'}), 403
        db.execute('DELETE FROM documents WHERE id=?', (doc_id,))
    try:
        os.remove(os.path.join(DOCS_DIR, doc['filename']))
    except OSError:
        pass
    return jsonify({'ok': True})

# ── Playbook / plans / config / health ────────────────────────────────────────

@app.route('/api/playbook')
@login_required
def playbook():
    return jsonify(PLAYBOOK)

@app.route('/api/plans')
@login_required
def plans():
    return jsonify(PLANS)

@app.route('/api/config')
def config():
    return jsonify({
        'stages': STAGES, 'lead_types': LEAD_TYPES, 'sources': SOURCES,
        'temperature': TEMPERATURE, 'partner_types': PARTNER_TYPES,
        'services': SERVICES, 'plans': PLANS,
        'billing_options': [
            {'key': '',          'label': 'One-time'},
            {'key': 'monthly',   'label': 'Monthly'},
            {'key': 'quarterly', 'label': 'Quarterly'},
            {'key': 'annual',    'label': 'Annual'},
        ],
        # Served rather than mirrored in the front end, so the picker and the
        # validator that accepts its value cannot drift apart.
        'lost_reasons': sorted(plost.REASONS.items()),
        'stall_days': STALL_DAYS,
        'daily_target': DAILY_TARGET,
        'cooldown_days': COOLDOWN_DAYS,
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'db': DB_PATH, 'den': bool(BASE44_TOKEN),
                    'mail': pmail.configured(), 'plans': len(PLANS)})


# Its OWN loop and its own interval, not a branch inside the estimator's --
# same rule as the two nightly backups: if one job fails the other still runs.
# Reminders are idempotent per appointment time, so a missed pass costs nothing
# but lateness and a double-started thread cannot double-send.
if os.environ.get('SALESCRM_DISABLE_JOBS') != '1':
    threading.Thread(target=_appt_loop, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=True, port=5002)
