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
import re
import sys
import json
import uuid
import sqlite3
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

# ── Cold outreach: who a message is for, and where each contact stands ───────
#
# The pipeline STAGE answers "how far is this deal"; it has nothing to say about
# the eleven calls before a deal exists. OUTREACH STATUS is that missing half —
# no answer, left a voicemail, call back Tuesday, wrong number — and it is set
# by the OUTCOME a rep taps after a touch, never typed. Each outcome also books
# the follow-up that belongs to it, so "who do I call back, and when" is a list
# the CRM keeps rather than a thing a rep remembers.

# Templates are written for an audience, not for all twelve lead types.
AUDIENCES = [
    {'key': 'homeowner',     'label': 'Homeowners'},
    {'key': 'partner',       'label': 'Partners'},
    {'key': 'commercial',    'label': 'Commercial'},
    {'key': 'past_customer', 'label': 'Past customers & old leads'},
]
AUDIENCE_KEYS = [a['key'] for a in AUDIENCES]
COMMERCIAL_TYPES = ('commercial', 'church', 'school', 'school_district')
# 'call' is a call script: read live, so no length rule and no signature.
TEMPLATE_CHANNELS = ('email', 'text', 'voicemail', 'call')
TEMPLATE_STEPS = ('first', 'followup', 'breakup', 'any')
# Slots the renderer fills. A template naming anything else is refused on save:
# an unknown slot would reach a customer as a literal "{rep_phone}".
TEMPLATE_SLOTS = ('greeting', 'first_name', 'company', 'city', 'hook',
                  'research_hook', 'storm_hook', 'rep_name', 'rep_first')
# Two SMS segments. Past that some phones split the message and deliver the
# halves out of order.
TEXT_MAX_CHARS = 320
EMAIL_MAX_WORDS = 100


def _audience_for(lead):
    """Which audience's templates fit this lead.

    A partner stays a partner even when lost. A homeowner or building owner who
    bought from us, or got a quote and went quiet, is a past customer: the right
    message is a review, a referral or a refreshed number, not a cold opener to
    someone who already knows us."""
    ltype = lead.get('lead_type') or 'homeowner'
    if ltype in PARTNER_TYPES:
        return 'partner'
    if lead.get('source') == 'existing_customer' or lead.get('stage') in ('won', 'lost'):
        return 'past_customer'
    if ltype in COMMERCIAL_TYPES:
        return 'commercial'
    return 'homeowner'


# Where a contact stands. `open` = still worth another touch.
OUTREACH_STATUSES = [
    {'key': 'not_contacted',  'label': 'Not contacted',      'color': '#6B7280', 'open': True},
    {'key': 'attempted',      'label': 'No answer',          'color': '#94A3B8', 'open': True},
    {'key': 'left_vm',        'label': 'Left voicemail',     'color': '#60A5FA', 'open': True},
    {'key': 'messaged',       'label': 'Texted / emailed',   'color': '#3B82F6', 'open': True},
    {'key': 'visited',        'label': 'Dropped by',         'color': '#0EA5E9', 'open': True},
    {'key': 'connected',      'label': 'Talked',             'color': '#6366F1', 'open': True},
    {'key': 'callback',       'label': 'Call back',          'color': '#F59E0B', 'open': True},
    {'key': 'interested',     'label': 'Interested',         'color': '#10B981', 'open': True},
    {'key': 'appt_set',       'label': 'Appointment set',    'color': '#059669', 'open': False},
    {'key': 'nurture',        'label': 'Not now',            'color': '#A78BFA', 'open': True},
    {'key': 'not_interested', 'label': 'Not interested',     'color': '#EF4444', 'open': False},
    {'key': 'bad_contact',    'label': 'Bad contact info',   'color': '#F97316', 'open': False},
    {'key': 'dnc',            'label': 'Do not contact',     'color': '#7F1D1D', 'open': False},
]
OUTREACH_STATUS_KEYS = [s['key'] for s in OUTREACH_STATUSES]
OUTREACH_STATUS_META = {s['key']: s for s in OUTREACH_STATUSES}

# What a rep taps after a touch. Each one sets a status and books (or cancels)
# the follow-up that belongs to it:
#   follow  (days, task kind, title) — the next touch; None = no new task
#   stop    close open tasks and cadences: nobody should call this person on
#           autopilot any more
#   ask_date  the rep picks the follow-up date (a callback they agreed to)
#   stage   the pipeline stage this outcome proves, applied only forward
#   kind    the activity logged when the rep did not say which channel
#   cadence True = a lead in a running cadence keeps it; the cadence's next step
#           IS the follow-up, and booking another task would double-book them
OUTCOMES = [
    {'key': 'no_answer',      'label': 'No answer',         'icon': '📵', 'kind': 'call',
     'status': 'attempted', 'follow': (2, 'call', 'Try again - a different time of day'),
     'cadence': True},
    {'key': 'left_vm',        'label': 'Left voicemail',    'icon': '📼', 'kind': 'call',
     'status': 'left_vm', 'follow': (1, 'text', 'Text after the voicemail'), 'cadence': True},
    {'key': 'texted',         'label': 'Texted',            'icon': '💬', 'kind': 'text',
     'status': 'messaged', 'follow': (3, 'call', 'Call - did they see the text?'), 'cadence': True},
    {'key': 'emailed',        'label': 'Emailed',           'icon': '✉️', 'kind': 'email',
     'status': 'messaged', 'follow': (3, 'call', 'Call - follow up on the email'), 'cadence': True},
    # Churches, schools and businesses are often worked in person: walk in,
    # leave a card or a sample report, call a few days later.
    {'key': 'dropped_by',     'label': 'Dropped by',        'icon': '🚪', 'kind': 'door',
     'status': 'visited', 'follow': (3, 'call', 'Call - after the drop-by'), 'cadence': True},
    {'key': 'talked',         'label': 'Talked',            'icon': '🗣', 'kind': 'call',
     'status': 'connected', 'follow': (3, 'call', 'Follow up on the conversation'),
     'cadence': True, 'stage': 'contacted'},
    {'key': 'callback',       'label': 'Call back…',        'icon': '📅', 'kind': 'call',
     'status': 'callback', 'follow': (1, 'call', 'Call back - they asked for this time'),
     'ask_date': True, 'stop': True, 'stage': 'contacted'},
    {'key': 'interested',     'label': 'Interested',        'icon': '👍', 'kind': 'call',
     'status': 'interested', 'follow': (0, 'call', 'Book the appointment'),
     'stop': True, 'stage': 'contacted'},
    {'key': 'appt_set',       'label': 'Appointment set',   'icon': '📆', 'kind': 'call',
     'status': 'appt_set', 'follow': None, 'stop': True, 'stage': 'appt_set'},
    {'key': 'not_now',        'label': 'Not now',           'icon': '⏸', 'kind': 'call',
     'status': 'nurture', 'follow': (90, 'call', 'Check back in - they said not now'),
     'stop': True},
    {'key': 'not_interested', 'label': 'Not interested',    'icon': '👎', 'kind': 'call',
     'status': 'not_interested', 'follow': None, 'stop': True, 'lose': 'Not interested'},
    {'key': 'wrong_number',   'label': 'Wrong number',      'icon': '❌', 'kind': 'call',
     'status': 'bad_contact', 'follow': (1, 'research', 'Find the right phone or email'),
     'stop': True},
]
OUTCOME_BY_KEY = {o['key']: o for o in OUTCOMES}

# ── National Do Not Call Registry ─────────────────────────────────────────────
# The registry covers residential numbers, so it governs HOMEOWNER leads only;
# business lines (churches, schools, companies, partners) are generally exempt.
# An existing business relationship exempts a number too: a customer for 18
# months after they bought, an inquirer for 3 months after they contacted us.
# This is a guard rail, not legal advice - the thresholds come from the FTC's
# Telemarketing Sales Rule and should be confirmed with counsel.
DNC_TYPES          = ('homeowner',)
DNC_REFRESH_DAYS   = 31
DNC_EBR_SALE_DAYS  = 548
DNC_EBR_INQ_DAYS   = 90
DNC_INQUIRY_SOURCES = ('website', 'phone_call')


def _dnc_clause(alias=''):
    """SQL (and params) that is TRUE for a lead we may not cold call or text
    because its number is on the registry and no exemption applies."""
    a = alias + '.' if alias else ''
    now = _now_dt()
    sale = _iso(now - timedelta(days=DNC_EBR_SALE_DAYS))
    inq = _iso(now - timedelta(days=DNC_EBR_INQ_DAYS))
    sql = (f"({a}lead_type IN ({','.join('?' * len(DNC_TYPES))}) "
           f"AND {a}phone_norm != '' "
           f"AND {a}phone_norm IN (SELECT phone_norm FROM dnc_registry) "
           f"AND NOT ({a}won_at != '' AND {a}won_at >= ?) "
           f"AND NOT ({a}source IN ({','.join('?' * len(DNC_INQUIRY_SOURCES))}) "
           f"AND {a}created_at >= ?))")
    return sql, list(DNC_TYPES) + [sale] + list(DNC_INQUIRY_SOURCES) + [inq]
# The outcomes that mean a template worked: somebody engaged.
GOOD_OUTCOMES = ('talked', 'callback', 'interested', 'appt_set')
# Four unanswered touches in a row and the fifth is not the one that lands.
# Past this, "no answer" parks the lead for a month instead of two days.
NO_ANSWER_PARK_AFTER = 4
NO_ANSWER_PARK_DAYS  = 30

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

# ── Contact quality: how good a way in do we have? ────────────────────────────
#
# A rep handed "Grace Church, (970) 555-0100" is calling a front desk. Handed
# "Pastor John Smith, john@grace.org, confirmed by Casey" they are calling the
# person who decides. The grade is what lets the queue put the second kind in
# front of reps first, and it is STORED (leads.contact_quality) so the queue
# can order by it in SQL. _refresh_contact_quality() is the only writer.
CONTACT_QUALITY = [
    {'key': 3, 'label': 'Verified',      'hint': 'A rep confirmed this is the right person'},
    {'key': 2, 'label': 'Named contact', 'hint': 'A named person with a direct way to reach them'},
    {'key': 1, 'label': 'General line',  'hint': 'Only a main number or a shared inbox'},
    {'key': 0, 'label': 'No contact',    'hint': 'No phone or email yet'},
]
CONTACT_QUALITY_LABEL = {q['key']: q['label'] for q in CONTACT_QUALITY}
# Shared inboxes. Mail to info@ reaches whoever checks it this week, which for
# a church is often a volunteer; it is a way in, not a contact.
ROLE_MAILBOXES = {'info', 'office', 'admin', 'administrator', 'contact', 'hello', 'church',
                  'frontdesk', 'front.desk', 'reception', 'mail', 'support', 'sales',
                  'secretary', 'welcome', 'parish', 'general', 'team', 'inquiries',
                  'enquiries', 'connect', 'communications', 'media', 'webmaster', 'events'}


def _contact_quality(lead):
    """0-3, per CONTACT_QUALITY. Pure, so the import path can grade a row
    before it exists."""
    if (lead.get('contact_verified_at') or '').strip():
        return 3
    email = (lead.get('email') or '').strip().lower()
    phone = (lead.get('phone') or '').strip()
    if not (email or phone):
        return 0
    named = bool((lead.get('first_name') or '').strip())
    personal_email = bool(email) and email.split('@')[0] not in ROLE_MAILBOXES
    # A homeowner's phone is their own; an organisation's listed number is a
    # switchboard. So a named homeowner with any contact is a direct line.
    if named and (personal_email or (lead.get('lead_type') == 'homeowner' and phone)):
        return 2
    return 1


def _refresh_contact_quality(db, lead_id):
    row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    if row:
        db.execute('UPDATE leads SET contact_quality=? WHERE id=?',
                   (_contact_quality(dict(row)), lead_id))


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
        acols = [r['name'] for r in db.execute('PRAGMA table_info(activities)')]
        if 'template_id' not in acols:
            # Which library template a touch used, so a manager can see which
            # HOA text books meetings and which one gets ignored.
            db.execute("ALTER TABLE activities ADD COLUMN template_id TEXT DEFAULT ''")
        if 'site_checked_at' not in cols:
            # When agents/b2b/site_contacts last read this lead's website.
            db.execute("ALTER TABLE leads ADD COLUMN site_checked_at TEXT DEFAULT ''")
        if 'contact_quality' not in cols:
            db.execute('ALTER TABLE leads ADD COLUMN contact_quality INTEGER DEFAULT 0')
            db.execute("ALTER TABLE leads ADD COLUMN contact_source TEXT DEFAULT ''")
            db.execute("ALTER TABLE leads ADD COLUMN contact_verified_at TEXT DEFAULT ''")
            db.execute("ALTER TABLE leads ADD COLUMN contact_verified_by TEXT DEFAULT ''")
            # Contacts filled by the research run before this column existed.
            # Only where the timeline says research found them, so a rep's own
            # typing is never marked as clearable.
            db.execute("UPDATE leads SET contact_source='research' WHERE enriched_at != '' "
                       "AND (first_name != '' OR email != '') AND id IN (SELECT lead_id FROM "
                       "activities WHERE kind='system' AND body LIKE 'Researched: found%')")
            for r in db.execute('SELECT * FROM leads').fetchall():
                db.execute('UPDATE leads SET contact_quality=? WHERE id=?',
                           (_contact_quality(dict(r)), r['id']))
        if 'outreach_status' not in cols:
            db.execute("ALTER TABLE leads ADD COLUMN outreach_status TEXT DEFAULT 'not_contacted'")
            db.execute("ALTER TABLE leads ADD COLUMN outreach_status_at TEXT DEFAULT ''")
            _backfill_outreach_status(db)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS documents (
                id          TEXT PRIMARY KEY,
                lead_id     TEXT NOT NULL,
                filename    TEXT NOT NULL,
                orig_name   TEXT NOT NULL,
                size        INTEGER DEFAULT 0,
                uploaded_by TEXT NOT NULL,
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

            CREATE INDEX IF NOT EXISTS leads_outreach_idx ON leads(rep, outreach_status);
            -- The cold queue works the best contacts first. Same shape and
            -- reason as leads_queue_idx, with the grade in front of the score.
            CREATE INDEX IF NOT EXISTS leads_queue_cq_idx
                ON leads(rep, stage, contact_quality DESC, icp_score DESC, created_at);

            -- The template library. Seeded from outreach_library.json and
            -- outreach_templates.json by seed_templates(), then owned by the
            -- managers who edit it. `seed_key` is how a seed is recognised on
            -- the next start, and an archived row keeps it, so archiving a
            -- seeded template is permanent rather than undone by a restart.
            CREATE TABLE IF NOT EXISTS templates (
                id          TEXT PRIMARY KEY,
                seed_key    TEXT DEFAULT '',
                name        TEXT NOT NULL,
                channel     TEXT NOT NULL,
                audience    TEXT NOT NULL,
                lead_type   TEXT DEFAULT '',
                step        TEXT DEFAULT 'any',
                stage       TEXT DEFAULT '',
                subject     TEXT DEFAULT '',
                body        TEXT NOT NULL,
                archived    INTEGER DEFAULT 0,
                sort        INTEGER DEFAULT 0,
                updated_by  TEXT DEFAULT '',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS tpl_chan_idx ON templates(channel, audience);

            -- The National Do Not Call Registry, as downloaded by area code.
            -- NOT the suppression list: that is people who asked US to stop,
            -- forever. This is a residential registry that must be re-loaded
            -- at least every 31 days and only governs cold calls and texts to
            -- homes. Kept apart so a refresh can replace an area code whole.
            CREATE TABLE IF NOT EXISTS dnc_registry (
                phone_norm TEXT PRIMARY KEY,
                area       TEXT NOT NULL,
                loaded_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS dnc_area_idx ON dnc_registry(area);
        ''')
        _backfill_norms(db)


def _backfill_outreach_status(db):
    """Give leads that existed before outreach status a status that is true.

    Runs once, when the column is added. Every lead defaulted to
    'not_contacted', which would put people a rep has already spoken to back
    at the top of the cold queue and tell the status board nobody had been
    reached. Runs at import, before _now() is defined further down."""
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    db.execute("UPDATE leads SET outreach_status='dnc', outreach_status_at=? WHERE dnc=1", (now,))
    db.execute("UPDATE leads SET outreach_status='not_interested', outreach_status_at=? "
               "WHERE dnc=0 AND stage='lost'", (now,))
    db.execute("UPDATE leads SET outreach_status='appt_set', outreach_status_at=? "
               "WHERE dnc=0 AND stage IN ('appt_set','inspected','estimate_presented','won')",
               (now,))
    db.execute("UPDATE leads SET outreach_status='connected', outreach_status_at=? "
               "WHERE dnc=0 AND stage IN ('contacted','follow_up')", (now,))
    db.execute("UPDATE leads SET outreach_status='attempted', outreach_status_at=? "
               "WHERE dnc=0 AND stage='new' AND last_activity_at != ''", (now,))

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
LIBRARY  = _load_json('outreach_library.json', {'templates': []})
CADENCE_BY_ID = {c['id']: c for c in CADENCES}
PLAN_BY_ID    = {p['id']: p for p in PLANS}


def _seed_rows():
    """Every starter template as a row dict, keyed for idempotent seeding.

    The partner/commercial emails still live in outreach_templates.json (their
    voice tests read that file), so they are converted here rather than copied
    into the library file a second time."""
    rows = []
    for ltype, steps in (TEMPLATES.get('templates') or {}).items():
        aud = 'partner' if ltype in PARTNER_TYPES else (
              'commercial' if ltype in COMMERCIAL_TYPES else 'homeowner')
        label = next((t['label'] for t in LEAD_TYPES if t['key'] == ltype), ltype)
        for i, (step, tpl) in enumerate(steps.items()):
            rows.append({'seed_key': f'email:{ltype}:{step}',
                         'name': f'{label} - {step}', 'channel': 'email',
                         'audience': aud, 'lead_type': ltype, 'step': step, 'stage': '',
                         'subject': tpl.get('subject', ''), 'body': tpl.get('body', ''),
                         'sort': i})
    for i, t in enumerate(LIBRARY.get('templates') or []):
        rows.append({'seed_key': t['key'], 'name': t['name'], 'channel': t['channel'],
                     'audience': t['audience'], 'lead_type': t.get('lead_type', ''),
                     'step': t.get('step', 'any'), 'stage': t.get('stage', ''),
                     'subject': t.get('subject', ''), 'body': t['body'], 'sort': 100 + i})
    return rows


def _dedupe_seeded_templates(db):
    """Collapse seed rows that were inserted twice.

    Two gunicorn workers import this module at the same moment on every
    deploy, and both used to read "not seeded yet" before either had written,
    so every new starter template landed twice. This keeps ONE row per
    seed_key: an edited copy if a manager has touched one (never throw away
    their words), otherwise the oldest. Only untouched copies are removed; if
    two copies were both edited, both stay and the unique index below is
    simply not created until a manager archives one.
    """
    dup_keys = [r['seed_key'] for r in db.execute(
        "SELECT seed_key FROM templates WHERE seed_key != '' "
        "GROUP BY seed_key HAVING COUNT(*) > 1")]
    removed = 0
    for key in dup_keys:
        rows = [dict(r) for r in db.execute(
            'SELECT id, updated_by, created_at FROM templates WHERE seed_key=? '
            'ORDER BY created_at, id', (key,))]
        edited = [r for r in rows if r['updated_by'] != 'seed']
        keep = {r['id'] for r in edited} or {rows[0]['id']}
        for r in rows:
            if r['id'] not in keep and r['updated_by'] == 'seed':
                db.execute('DELETE FROM templates WHERE id=?', (r['id'],))
                removed += 1
    if removed:
        print(f'[templates] removed {removed} duplicate starter template(s)')


def seed_templates():
    """Insert any starter template whose seed_key has never been seeded.

    Never updates an existing row: once a template is in the database it is
    the managers', and a restart must not put the old wording back over their
    edit. Same rule, and the same reason, as the estimator's price-book seeds.

    Safe under two workers starting at once: a UNIQUE index on seed_key plus
    INSERT OR IGNORE, rather than a read-then-insert that both workers can
    pass before either writes."""
    with get_db() as db:
        _dedupe_seeded_templates(db)
        try:
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS tpl_seed_idx "
                       "ON templates(seed_key) WHERE seed_key != ''")
        except sqlite3.IntegrityError:
            print('[templates] two edited copies share a seed_key; '
                  'archive one to restore the unique index')
        now = _now()
        for r in _seed_rows():
            db.execute('INSERT OR IGNORE INTO templates (id, seed_key, name, channel, '
                       'audience, lead_type, step, stage, subject, body, sort, updated_by, '
                       'created_at, updated_at) '
                       'SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,? '
                       'WHERE NOT EXISTS (SELECT 1 FROM templates WHERE seed_key=?)',
                       (str(uuid.uuid4()), r['seed_key'], r['name'], r['channel'],
                        r['audience'], r['lead_type'], r['step'], r['stage'],
                        r['subject'], r['body'], r['sort'], 'seed', now, now,
                        r['seed_key']))

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

def _now_dt():
    return datetime.utcnow()

def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

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
    ometa = OUTREACH_STATUS_META.get(d.get('outreach_status') or 'not_contacted',
                                     OUTREACH_STATUSES[0])
    d['outreach_label'] = ometa['label']
    d['outreach_color'] = ometa['color']
    d['audience'] = _audience_for(d)
    d['contact_quality_label'] = CONTACT_QUALITY_LABEL.get(d.get('contact_quality') or 0, '')
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

def _log_activity(db, lead_id, kind, body='', outcome='', rep=None, template_id=''):
    db.execute('INSERT INTO activities (id, lead_id, rep, kind, outcome, body, template_id, created_at) '
               'VALUES (?,?,?,?,?,?,?,?)',
               (str(uuid.uuid4()), lead_id, rep or current_rep(), kind, outcome, body,
                template_id or '', _now()))
    if kind in OUTREACH_KINDS:
        db.execute('UPDATE leads SET last_activity_at=?, updated_at=? WHERE id=?',
                   (_now(), _now(), lead_id))

def _refresh_next_action(db, lead_id):
    """leads.next_action_at = the soonest incomplete task's due date (or '')."""
    row = db.execute('SELECT MIN(due_at) m FROM tasks WHERE lead_id=? AND done=0', (lead_id,)).fetchone()
    db.execute('UPDATE leads SET next_action_at=?, updated_at=? WHERE id=?',
               (row['m'] or '', _now(), lead_id))

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
    limit   = max(1, min(request.args.get('limit', 1000, type=int), 5000))
    offset  = max(0, request.args.get('offset', 0, type=int))

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
    cq = request.args.get('contact_quality')
    if cq in ('0', '1', '2', '3'):
        clauses.append('contact_quality=?'); params.append(int(cq))
    outreach = request.args.get('outreach')
    if outreach in OUTREACH_STATUS_KEYS:
        clauses.append('outreach_status=?'); params.append(outreach)
    contact = request.args.get('contact')
    if contact in ('ready', 'research'):
        clauses.append(_contact_clause(contact))
    attention = request.args.get('attention')
    if attention in ('hot', 'needs_step'):
        clauses.append("stage NOT IN ('won','lost') AND dnc=0")
        if attention == 'hot':
            clauses.append("temperature='hot'")
        else:
            clauses.append("next_action_at='' AND last_activity_at!=''")
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
            "      COALESCE(address,'')    || ' ' || COALESCE(company,'') || ' ' || COALESCE(city,''))"
            " LIKE ? ESCAPE '\\'")
        params.append(f'%{esc}%')
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with get_db() as db:
        order = "CASE temperature WHEN 'hot' THEN 0 WHEN 'warm' THEN 1 ELSE 2 END, last_activity_at, id" if attention else 'updated_at DESC, id'
        rows = db.execute(f'SELECT * FROM leads {where} ORDER BY {order} LIMIT ? OFFSET ?',
                          params + [limit, offset]).fetchall()
    return jsonify([_lead_row(r) for r in rows])

@app.route('/api/pipeline/summary')
@login_required
def pipeline_summary():
    """Whole-pipeline totals, independent of the paginated lead list."""
    _reconcile_funnel()
    where, params = '', []
    if not is_manager():
        where, params = 'WHERE rep=?', [current_rep()]
    elif request.args.get('rep'):
        where, params = 'WHERE rep=?', [request.args['rep']]
    with get_db() as db:
        rows = db.execute(
            f'SELECT stage, COUNT(*) AS n, COALESCE(SUM(est_value),0) AS value '
            f'FROM leads {where} GROUP BY stage', params).fetchall()
    counts = {r['stage']: r['n'] for r in rows}
    values = {r['stage']: r['value'] for r in rows}
    return jsonify({
        'stage_counts': counts,
        'open_leads': sum(n for stage, n in counts.items() if stage not in ('won', 'lost')),
        'open_value': sum(v for stage, v in values.items() if stage not in ('won', 'lost')),
        'won_this_period': counts.get('won', 0),
        'won_value': values.get('won', 0),
        'period': 'all_time',
    })


def _contact_clause(mode):
    """Contact availability, shared by list filters and the outreach queue."""
    present = "(TRIM(COALESCE(phone,''))!='' OR TRIM(COALESCE(email,''))!='')"
    return present if mode == 'ready' else 'NOT ' + present


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
        'stage': stage, 'rep': rep, 'est_value': float(data.get('est_value') or 0),
        'referred_by': data.get('referred_by', ''),
        'phone_norm': _norm_phone(data.get('phone', '')),
        'email_norm': _norm_email(data.get('email', '')),
        'created_at': _now(), 'updated_at': _now(),
    }
    cols = ','.join(fields.keys())
    ph   = ','.join('?' * len(fields))
    with get_db() as db:
        db.execute(f'INSERT INTO leads ({cols}) VALUES ({ph})', list(fields.values()))
        _refresh_contact_quality(db, lid)
        _log_activity(db, lid, 'system', body=f'Lead created in stage "{STAGE_META[stage]["label"]}"')
        # A new lead starts following itself up. The cadence engine and its four
        # cadences already existed; nothing ever enrolled anyone, so the whole
        # follow-up apparatus only ran for reps who remembered to ask for it.
        auto = _cadence_for(stage, lead_type)
        if auto:
            _enroll(db, lid, rep, auto)
        row = db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone()
    return jsonify(_lead_row(row)), 201

@app.route('/api/leads/<lead_id>', methods=['GET'])
@login_required
def get_lead(lead_id):
    _reconcile_funnel()
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        d = _lead_row(row)
        clause, cparams = _dnc_clause()
        d['dnc_registry'] = bool(db.execute(
            f'SELECT 1 FROM leads WHERE id=? AND {clause}', [lead_id] + cparams).fetchone())
        d['activities'] = [dict(a) for a in db.execute(
            'SELECT * FROM activities WHERE lead_id=? ORDER BY created_at DESC LIMIT 200',
            (lead_id,)).fetchall()]
        d['tasks'] = [dict(t) for t in db.execute(
            'SELECT * FROM tasks WHERE lead_id=? ORDER BY done, due_at', (lead_id,)).fetchall()]
        d['enrollments'] = [dict(e) for e in db.execute(
            'SELECT * FROM cadence_enrollments WHERE lead_id=? AND active=1', (lead_id,)).fetchall()]
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
        db.execute(f'UPDATE leads SET {", ".join(sets)} WHERE id=?', params)
        _refresh_contact_quality(db, lead_id)
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    if any(k in data for k in ('address', 'city', 'state', 'zip')):
        _relocate(dict(row))
    return jsonify(_lead_row(row))


def _relocate(lead):
    """Geocode one lead's address now, so a fixed address is back in the
    storm join immediately instead of waiting for the next backfill. One
    Census call, free; a failure is logged and never fails the save."""
    if os.environ.get('SALESCRM_GEOCODE_ON_EDIT', '1').strip() in ('0', 'false', 'no'):
        return None
    if not (lead.get('address') or '').strip():
        return None
    try:
        from portal import geo
        geo.geocode([(lead.get('address', ''), lead.get('city', ''),
                      lead.get('state', ''), lead.get('zip', ''))])
        return geo.lookup(lead.get('address', ''), lead.get('city', ''),
                          lead.get('state', ''), lead.get('zip', ''))
    except Exception as e:
        print(f'[geo] relocate failed for {lead.get("id")}: {e}')
        return None

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
        db.execute('UPDATE leads SET stage=?, won_at=?, lost_reason=?, updated_at=? WHERE id=?',
                   (new_stage, won_at, lost_reason, _now(), lead_id))
        _log_activity(db, lead_id, 'stage_change',
                      body=f'{STAGE_META[old]["label"]} → {STAGE_META[new_stage]["label"]}')
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
        db.execute('DELETE FROM leads WHERE id=?', (lead_id,))
        db.execute('DELETE FROM activities WHERE lead_id=?', (lead_id,))
        db.execute('DELETE FROM tasks WHERE lead_id=?', (lead_id,))
        db.execute('DELETE FROM cadence_enrollments WHERE lead_id=?', (lead_id,))
    return jsonify({'ok': True})

# ── Activities ────────────────────────────────────────────────────────────────

@app.route('/api/leads/<lead_id>/activities', methods=['POST'])
@login_required
def add_activity(lead_id):
    data = request.get_json(force=True)
    kind = data.get('kind', 'note')
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        _log_activity(db, lead_id, kind, body=data.get('body', ''), outcome=data.get('outcome', ''))
        acts = [dict(a) for a in db.execute(
            'SELECT * FROM activities WHERE lead_id=? ORDER BY created_at DESC LIMIT 200',
            (lead_id,)).fetchall()]
    return jsonify(acts), 201

# ── Tasks (the "next action" engine) ──────────────────────────────────────────

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
            if done:
                # Completing a task logs it and advances any cadence it belongs to.
                _log_activity(db, t['lead_id'], 'note',
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
    _log_activity(db, lead_id, 'stage_change', rep=row['rep'],
                  body=f'{STAGE_META[old]["label"]} → {STAGE_META[target]["label"]} ({reason})')
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
            if ev['state'] in ('lost', 'declined'):
                _log_activity(db, lead_id, 'system',
                              body='Estimate marked lost — the lead is still open')
                continue
            target = _FUNNEL_STAGE.get(ev['state'], '')
            if not target:
                continue
            if ev['state'] == 'signed' and ev['value']:
                db.execute('UPDATE leads SET est_value=? WHERE id=?',
                           (ev['value'], lead_id))
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

@app.route('/api/partners')
@login_required
def list_partners():
    """Referral sources (realtors/HOAs/etc.) with how many leads they've sent."""
    clauses = ['lead_type IN (%s)' % ','.join('?' * len(PARTNER_TYPES))]
    params = list(PARTNER_TYPES)
    if not is_manager():
        clauses.append('rep=?'); params.append(current_rep())
    where = 'WHERE ' + ' AND '.join(clauses)
    with get_db() as db:
        rows = db.execute(f'SELECT * FROM leads {where} ORDER BY updated_at DESC', params).fetchall()
        counts = {r['referred_by']: r['c'] for r in db.execute(
            "SELECT referred_by, COUNT(*) c FROM leads WHERE referred_by != '' GROUP BY referred_by"
        ).fetchall()}
        won = {r['referred_by']: r['c'] for r in db.execute(
            "SELECT referred_by, COUNT(*) c FROM leads WHERE referred_by != '' AND stage='won' GROUP BY referred_by"
        ).fetchall()}
    out = []
    for r in rows:
        d = _lead_row(r)
        d['referrals_total'] = counts.get(r['id'], 0)
        d['referrals_won']   = won.get(r['id'], 0)
        out.append(d)
    return jsonify(out)

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
                _refresh_contact_quality(db, lid)
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
        # The status says so too, so the follow-up board never lists someone
        # who asked to be left alone as "no answer".
        if kind == 'email':
            db.execute("UPDATE leads SET dnc=1, outreach_status='dnc', outreach_status_at=?, "
                       "updated_at=? WHERE email_norm=?", (_now(), _now(), value))
        elif kind == 'phone':
            db.execute("UPDATE leads SET dnc=1, outreach_status='dnc', outreach_status_at=?, "
                       "updated_at=? WHERE phone_norm=?", (_now(), _now(), value))
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

def _template_ctx(lead, rep_name):
    first = (lead.get('first_name') or '').strip()
    # research_hook / storm_hook are optional and safe to leave blank; _fill()
    # drops paragraphs an empty slot left blank, matching the {hook} pattern.
    return {
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


def _render_template(tpl, lead, rep_name):
    """One library row rendered for one lead. Emails get the signature; texts
    and voicemails do not — a text signed with a web address reads as bulk, and
    a voicemail script is read aloud."""
    ctx = _template_ctx(lead, rep_name)
    body = _fill(tpl['body'], ctx)
    if tpl['channel'] == 'email':
        sig = TEMPLATES.get('signature', '')
        if sig:
            body += '\n\n' + _fill(sig, ctx)
    elif tpl['channel'] == 'text':
        # _fill joins paragraphs with blank lines; a text is one paragraph.
        body = ' '.join(body.split())
    return {'id': tpl['id'], 'name': tpl['name'], 'channel': tpl['channel'],
            'audience': tpl['audience'], 'step': tpl['step'], 'stage': tpl['stage'],
            'subject': _fill(tpl['subject'] or '', ctx), 'body': body}


def _templates_for(db, lead, channel):
    """This lead's templates on one channel, best fit first: its own lead
    type, then its audience, then (email only) the generic partner opener that
    has always been the fallback for an unmapped type."""
    rows = [dict(r) for r in db.execute(
        'SELECT * FROM templates WHERE channel=? AND archived=0 ORDER BY sort, name',
        (channel,))]
    ltype, aud, stage = lead.get('lead_type') or '', _audience_for(lead), lead.get('stage') or ''
    fit = [r for r in rows if r['lead_type'] == ltype and ltype]
    fit += [r for r in rows if not r['lead_type'] and r['audience'] == aud]
    if aud == 'past_customer':
        # A review ask to someone who never bought is worse than no template.
        fit = [r for r in fit if not r['stage'] or r['stage'] == stage]
    if not fit and channel == 'email' and aud != 'past_customer':
        fit = [r for r in rows if r['lead_type'] == 'referral_partner']
    return fit


def _pick(fit, step):
    """The recommended template for this touch.

    A template written for this lead TYPE beats one written for its whole
    audience, even when the audience-wide one names this exact touch: an HOA
    voicemail for "any touch" is a better first voicemail to an HOA than the
    generic partner one marked "first". So: the type's own for this step, the
    type's own for any step, the audience's for this step, the audience's for
    any step, and only then whatever fits.
    """
    own = [r for r in fit if r['lead_type']]
    gen = [r for r in fit if not r['lead_type']]
    for group, want in ((own, step), (own, 'any'), (gen, step), (gen, 'any')):
        hit = next((r for r in group if r['step'] == want), None)
        if hit:
            return hit
    return fit[0] if fit else None


def _render_draft(lead, step, rep_name, db=None):
    """{'subject','body','step'} for a lead's recommended email, or None."""
    def _go(conn):
        tpl = _pick(_templates_for(conn, lead, 'email'), step)
        if not tpl:
            return None
        out = _render_template(tpl, lead, rep_name)
        return {'subject': out['subject'], 'body': out['body'], 'step': step,
                'template_id': tpl['id']}
    if db is not None:
        return _go(db)
    with get_db() as conn:
        return _go(conn)

@app.route('/api/leads/<lead_id>/draft')
@login_required
def lead_draft(lead_id):
    """The email a rep would send this partner right now."""
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        touches = _touch_count(db, lead_id)
        step = request.args.get('step') or _draft_step(touches)
        draft = _render_draft(dict(row), step, pusers.display_name(current_rep()), db=db)
    if not draft:
        return jsonify({'error': 'No template for this lead type'}), 404
    return jsonify(draft)

@app.route('/api/dnc-registry', methods=['GET'])
@login_required
def dnc_registry_status():
    """Which area codes are loaded, when, and whether they are past the 31-day
    refresh the rule requires."""
    cutoff = _iso(_now_dt() - timedelta(days=DNC_REFRESH_DAYS))
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            'SELECT area, COUNT(*) numbers, MIN(loaded_at) loaded_at FROM dnc_registry '
            'GROUP BY area ORDER BY area')]
        homeowners = db.execute(
            f"SELECT COUNT(*) FROM leads WHERE lead_type IN ({','.join('?' * len(DNC_TYPES))}) "
            "AND stage NOT IN ('won','lost')", list(DNC_TYPES)).fetchone()[0]
    for r in rows:
        r['stale'] = r['loaded_at'] < cutoff
    return jsonify({'areas': rows, 'refresh_days': DNC_REFRESH_DAYS,
                    'open_homeowner_leads': homeowners,
                    'stale': any(r['stale'] for r in rows)})


@app.route('/api/dnc-registry', methods=['POST'])
@admin_required
def load_dnc_registry():
    """Load a registry download. Any text works: every 10-digit number in it is
    read, so the FTC's 'area,number' lines and plain lists both parse. Each
    area code present is REPLACED whole, since a refresh must drop numbers
    that have left the registry as well as add new ones."""
    text = (request.get_json(force=True) or {}).get('text') or ''
    nums = set()
    for line in text.splitlines():
        n = _norm_phone(line)
        if n:
            nums.add(n)
    if not nums:
        return jsonify({'error': 'No 10-digit phone numbers found in that file.'}), 400
    areas = sorted({n[:3] for n in nums})
    now = _now()
    with get_db() as db:
        db.execute(f"DELETE FROM dnc_registry WHERE area IN ({','.join('?' * len(areas))})", areas)
        db.executemany('INSERT OR REPLACE INTO dnc_registry (phone_norm, area, loaded_at) '
                       'VALUES (?,?,?)', [(n, n[:3], now) for n in nums])
    return jsonify({'loaded': len(nums), 'areas': areas})


# ── Storms: the hail archive, joined to the pipeline ─────────────────────────
#
# The hail package (hail/) holds NOAA's radar hail estimate (MRMS MESH) for
# every ~1 km cell of Colorado, day by day. Two things come out of it here:
#
#   * storm_tag_leads() writes each lead's worst hail of the past year into
#     leads.recent_storm, which is what the {storm_hook} fill-in reads. That
#     is the strongest opening line there is, and until now it was always
#     blank.
#   * storm_queue(event_id) books a follow-up for every open lead under one
#     storm's swath: past customers first, then open deals, then lost quotes,
#     then cold prospects - the order hail/join.TIERS fixes as a business rule.
#
# A lead with no coordinate is SKIPPED and COUNTED, never treated as "no hail":
# silently dropping un-geocoded leads is how a storm report reads as a small
# storm (CLAUDE.md, hail section).

STORM_MIN_IN = 1.0            # hail a roofer can sell on; below it, no follow-up
STORM_LOOKBACK_DAYS = 365
STORM_CLOSED_STATUSES = ('not_interested', 'dnc', 'bad_contact')
# Due times inside the day, by tier, so the queue (ordered by due_at) works
# past customers first. Minutes after the start of the Colorado morning.
_STORM_TIER_OFFSET = {'past_customer': 0, 'open_lead': 5, 'lost_estimate': 10, 'cold': 15}


def _storm_tier(lead):
    if lead.get('stage') == 'won' or lead.get('source') == 'existing_customer':
        return 'past_customer'
    if lead.get('stage') == 'lost':
        return 'lost_estimate'
    if lead.get('stage') != 'new':
        return 'open_lead'
    return 'cold'


def _lead_point(lead):
    from portal import geo
    hit = geo.lookup(lead.get('address', ''), lead.get('city', ''),
                     lead.get('state', ''), lead.get('zip', ''))
    return (hit['lat'], hit['lng']) if hit else None


def _storm_line(size_in, event_date):
    """The {storm_hook} sentence. Says what it is — a radar estimate — because a
    homeowner will repeat it, and an adjuster will check it."""
    try:
        d = datetime.strptime(event_date, '%Y-%m-%d')
        when = f'{d.strftime("%B")} {d.day}, {d.year}'
    except ValueError:
        when = event_date
    return (f'NOAA radar estimated {size_in:.2f}-inch hail at your address on {when}, '
            f'which is large enough to damage a roof.')


def storm_tag_leads(since_days=STORM_LOOKBACK_DAYS):
    """Write each lead's worst hail (>= STORM_MIN_IN) of the past year into
    recent_storm. Returns {'checked', 'tagged', 'no_coords'}."""
    from hail import storms
    since = (_now_dt() - timedelta(days=since_days)).strftime('%Y-%m-%d')
    checked = tagged = no_coords = 0
    with get_db() as db:
        leads = [dict(r) for r in db.execute(
            "SELECT id, address, city, state, zip, recent_storm FROM leads "
            "WHERE dnc = 0 AND address != ''")]
        for lead in leads:
            pt = _lead_point(lead)
            if not pt:
                no_coords += 1
                continue
            checked += 1
            hits = [h for h in storms.history_at(pt[0], pt[1], since=since)
                    if (h.get('size_in') or 0) >= STORM_MIN_IN]
            line = ''
            if hits:
                worst = max(hits, key=lambda h: (h['size_in'], h['event_date']))
                line = _storm_line(worst['size_in'], worst['event_date'])
                tagged += 1
            if line != (lead.get('recent_storm') or ''):
                db.execute('UPDATE leads SET recent_storm=? WHERE id=?', (line, lead['id']))
    return {'checked': checked, 'tagged': tagged, 'no_coords': no_coords}


def _storm_marker(event_id):
    return f'[storm:{event_id}]'


def storm_queue(event_id, min_size=STORM_MIN_IN):
    """Book a follow-up for every open lead under one storm. Idempotent: a lead
    already queued for this event is not queued again."""
    from hail import join as hjoin, storms
    ev = storms.get_event(event_id)
    if not ev:
        return {'error': 'Unknown storm'}
    swath = storms.load_swath(event_id)
    dnc_sql, dnc_params = _dnc_clause()
    marker = _storm_marker(event_id)
    with get_db() as db:
        leads = [dict(r) for r in db.execute(
            f"SELECT * FROM leads WHERE dnc = 0 "
            f"AND outreach_status NOT IN ({','.join('?' * len(STORM_CLOSED_STATUSES))}) "
            f"AND NOT {dnc_sql}", list(STORM_CLOSED_STATUSES) + dnc_params)]
        for lead in leads:
            lead['tier'] = _storm_tier(lead)
        hits, skipped = hjoin.affected(swath, leads, resolve=_lead_point, min_size=min_size)
        done = {r['lead_id'] for r in db.execute(
            'SELECT lead_id FROM activities WHERE body LIKE ?', (f'%{marker}%',))}
        start = _now_dt().replace(hour=13, minute=0, second=0, microsecond=0)  # ~7am Denver
        queued, already = 0, 0
        by_tier = {}
        for h in hits:
            if h['id'] in done:
                already += 1
                continue
            size = h['hail_size_in']
            when = ev['event_date']
            due = _iso(start + timedelta(minutes=_STORM_TIER_OFFSET.get(h['tier'], 15)))
            db.execute('INSERT INTO tasks (id, lead_id, rep, kind, title, due_at, created_at) '
                       'VALUES (?,?,?,?,?,?,?)',
                       (str(uuid.uuid4()), h['id'], h['rep'], 'call',
                        f'Storm: {size:.2f}" hail on {when}', due, _now()))
            _log_activity(db, h['id'], 'system', rep=h['rep'],
                          body=f'Storm follow-up queued: radar estimated {size:.2f}" hail '
                               f'here on {when}. {marker}')
            db.execute('UPDATE leads SET recent_storm=? WHERE id=?',
                       (_storm_line(size, when), h['id']))
            _refresh_next_action(db, h['id'])
            queued += 1
            by_tier[h['tier']] = by_tier.get(h['tier'], 0) + 1
    return {'event_id': event_id, 'date': ev['event_date'], 'max_size_in': ev.get('max_size_in'),
            'queued': queued, 'already_queued': already, 'no_coords': skipped,
            'by_tier': by_tier}


def storm_nightly():
    """What the nightly job runs after the hail ingest: queue every new storm
    of the last few days that reached STORM_MIN_IN, then re-tag leads."""
    from hail import storms
    since = (_now_dt() - timedelta(days=3)).strftime('%Y-%m-%d')
    out = [storm_queue(e['event_id']) for e in storms.events(since=since, min_size=STORM_MIN_IN)]
    out.append(storm_tag_leads())
    return out


@app.route('/api/storms')
@login_required
def list_storms():
    """Recent 1"+ storms that actually put hail over one of our leads.

    The archive covers all of Colorado, where somewhere gets 1"+ hail most days
    of the season - fifty "storms" in sixty days, mostly on the eastern plains.
    Listing them all buried the few that matter, so each is joined to the open
    leads first and only storms over at least one lead are shown, with how
    many. Lead coordinates are resolved once, not once per storm.
    """
    days = max(1, min(request.args.get('days', 60, type=int), 400))
    try:
        from hail import join as hjoin, storms
        since = (_now_dt() - timedelta(days=days)).strftime('%Y-%m-%d')
        evs = storms.events(since=since, min_size=STORM_MIN_IN, limit=100)
    except Exception as e:
        return jsonify({'events': [], 'error': f'Hail archive unavailable: {e}'})
    with get_db() as db:
        leads = [dict(r) for r in db.execute(
            "SELECT id, address, city, state, zip FROM leads WHERE dnc = 0 AND address != '' "
            f"AND outreach_status NOT IN ({','.join('?' * len(STORM_CLOSED_STATUSES))})",
            list(STORM_CLOSED_STATUSES))]
        placed = []
        for l in leads:
            pt = _lead_point(l)
            if pt:
                l['lat'], l['lng'] = pt
                placed.append(l)
        shown = []
        for ev in evs:
            hits, _ = hjoin.affected(storms.load_swath(ev['event_id']), placed,
                                     min_size=STORM_MIN_IN)
            ev['affected'] = len(hits)
            ev['queued'] = db.execute(
                'SELECT COUNT(DISTINCT lead_id) FROM activities WHERE body LIKE ?',
                (f'%{_storm_marker(ev["event_id"])}%',)).fetchone()[0]
            if ev['affected'] or ev['queued']:
                shown.append(ev)
        tagged = db.execute("SELECT COUNT(*) FROM leads WHERE recent_storm != ''").fetchone()[0]
    return jsonify({'events': shown, 'statewide': len(evs), 'min_size_in': STORM_MIN_IN,
                    'leads_tagged': tagged, 'leads_unplaced': len(leads) - len(placed)})


@app.route('/api/leads/unplaced')
@login_required
def unplaced_leads():
    """Leads whose address could not be located, so no storm can be checked
    against them. Fixing the address on the lead re-locates it on save."""
    clause, params = ("rep=? AND ", [current_rep()]) if not is_manager() else ('', [])
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            f"SELECT * FROM leads WHERE {clause}dnc = 0 AND address != '' "
            "ORDER BY company, last_name", params)]
    out = [_lead_row(r) for r in rows if not _lead_point(r)]
    return jsonify(out)


@app.route('/api/storms/<event_id>/queue', methods=['POST'])
@admin_required
def queue_storm(event_id):
    r = storm_queue(event_id)
    return jsonify(r), (404 if r.get('error') else 200)


@app.route('/api/storms/tag', methods=['POST'])
@admin_required
def tag_storms():
    return jsonify(storm_tag_leads())


# ── Template library ──────────────────────────────────────────────────────────
#
# Emails, texts and voicemail scripts, editable by managers on the Playbook tab
# and picked from on every outreach card. Everything stays a DRAFT: an email
# opens in the rep's own Gmail and a text opens in the phone's own Messages app
# with the words filled in. Nothing here sends, which is what keeps this 1:1
# outreach from a real person rather than a bulk sender that would need carrier
# registration and opt-out plumbing.

_SLOT_RE = re.compile(r'\{([a-z_]+)\}')


def _template_problems(t):
    """What would stop this template from being saved, as sentences a manager
    can act on. The same rules the seed file is tested against."""
    out = []
    if t.get('channel') not in TEMPLATE_CHANNELS:
        out.append('Pick a channel: email, text or voicemail.')
    if t.get('audience') not in AUDIENCE_KEYS:
        out.append('Pick who the template is for.')
    if t.get('step') not in TEMPLATE_STEPS:
        out.append('Pick which touch it is for.')
    if t.get('lead_type') and t['lead_type'] not in LEAD_TYPE_KEYS:
        out.append('Unknown lead type.')
    if not (t.get('name') or '').strip():
        out.append('Give it a name.')
    body = t.get('body') or ''
    if not body.strip():
        out.append('The message is empty.')
    if t.get('channel') == 'email' and not (t.get('subject') or '').strip():
        out.append('An email needs a subject line.')
    text = (t.get('subject') or '') + ' ' + body
    unknown = sorted(set(_SLOT_RE.findall(text)) - set(TEMPLATE_SLOTS))
    if unknown:
        out.append('Unknown fill-in field: ' + ', '.join('{' + u + '}' for u in unknown)
                   + '. Allowed: ' + ', '.join('{' + s + '}' for s in TEMPLATE_SLOTS) + '.')
    low = text.lower()
    banned = [p for p in (TEMPLATES.get('banned_phrases') or []) if p in low]
    if banned:
        out.append('Reads like bulk mail: "' + '", "'.join(banned) + '". Say the thing instead.')
    if t.get('channel') == 'text':
        sample = ' '.join(_fill(body, _template_ctx(
            {'first_name': 'Alexandra', 'city': 'Fort Collins', 'company': ''},
            'Firstname Lastname')).split())
        if len(sample) > TEXT_MAX_CHARS:
            out.append(f'Too long for a text ({len(sample)} characters filled in; '
                       f'keep it under {TEXT_MAX_CHARS}).')
    if t.get('channel') == 'email' and len(body.split()) > EMAIL_MAX_WORDS:
        out.append(f'Keep emails under {EMAIL_MAX_WORDS} words - it has {len(body.split())}.')
    return out


def _template_payload(data, existing=None):
    base = dict(existing or {})
    for k in ('name', 'channel', 'audience', 'lead_type', 'step', 'stage', 'subject', 'body'):
        if k in data:
            base[k] = str(data.get(k) or '').strip() if k != 'body' else str(data.get(k) or '').strip('\n ')
    base.setdefault('lead_type', ''); base.setdefault('stage', '')
    base.setdefault('step', 'any'); base.setdefault('subject', '')
    if base.get('stage') not in ('', 'won', 'lost'):
        base['stage'] = ''
    return base


@app.route('/api/templates', methods=['GET'])
@login_required
def list_templates():
    """The library. Archived rows only for a manager who asks for them."""
    show_archived = request.args.get('archived') == '1' and is_manager()
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM templates ' + ('' if show_archived else 'WHERE archived=0 ')
            + 'ORDER BY audience, channel, sort, name')]
        # How each one is doing: times used, and how many of those touches
        # ended in a real conversation. "Won" is too far downstream to credit
        # to one text; these are the outcomes a template can actually earn.
        stats = {r['template_id']: dict(r) for r in db.execute(
            "SELECT template_id, COUNT(*) used, "
            "SUM(CASE WHEN outcome IN (%s) THEN 1 ELSE 0 END) good "
            "FROM activities WHERE template_id != '' GROUP BY template_id"
            % ','.join('?' * len(GOOD_OUTCOMES)), list(GOOD_OUTCOMES))}
    for r in rows:
        st = stats.get(r['id']) or {}
        r['used'] = st.get('used', 0)
        r['good'] = st.get('good', 0) or 0
    return jsonify(rows)


@app.route('/api/templates', methods=['POST'])
@admin_required
def create_template():
    t = _template_payload(request.get_json(force=True) or {})
    problems = _template_problems(t)
    if problems:
        return jsonify({'error': ' '.join(problems), 'problems': problems}), 400
    tid, now = str(uuid.uuid4()), _now()
    with get_db() as db:
        db.execute('INSERT INTO templates (id, seed_key, name, channel, audience, lead_type, '
                   'step, stage, subject, body, sort, updated_by, created_at, updated_at) '
                   "VALUES (?,'',?,?,?,?,?,?,?,?,1000,?,?,?)",
                   (tid, t['name'], t['channel'], t['audience'], t['lead_type'], t['step'],
                    t['stage'], t['subject'], t['body'], current_rep(), now, now))
        row = db.execute('SELECT * FROM templates WHERE id=?', (tid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route('/api/templates/<tid>', methods=['PUT'])
@admin_required
def update_template(tid):
    with get_db() as db:
        row = db.execute('SELECT * FROM templates WHERE id=?', (tid,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        data = request.get_json(force=True) or {}
        t = _template_payload(data, dict(row))
        if 'archived' in data:
            t['archived'] = 1 if data.get('archived') else 0
        problems = [] if t.get('archived') else _template_problems(t)
        if problems:
            return jsonify({'error': ' '.join(problems), 'problems': problems}), 400
        db.execute('UPDATE templates SET name=?, channel=?, audience=?, lead_type=?, step=?, '
                   'stage=?, subject=?, body=?, archived=?, updated_by=?, updated_at=? WHERE id=?',
                   (t['name'], t['channel'], t['audience'], t['lead_type'], t['step'],
                    t['stage'], t['subject'], t['body'], int(t.get('archived') or 0),
                    current_rep(), _now(), tid))
        row = db.execute('SELECT * FROM templates WHERE id=?', (tid,)).fetchone()
    return jsonify(dict(row))


@app.route('/api/templates/<tid>', methods=['DELETE'])
@admin_required
def archive_template(tid):
    """Archive, never delete. A seeded row keeps its seed_key, which is what
    stops the next restart from seeding it straight back."""
    with get_db() as db:
        cur = db.execute('UPDATE templates SET archived=1, updated_by=?, updated_at=? WHERE id=?',
                         (current_rep(), _now(), tid))
        if not cur.rowcount:
            return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})


@app.route('/api/templates/preview', methods=['POST'])
@login_required
def preview_template():
    """Render an unsaved template against a real lead (or a sample), and say
    what would stop it saving — the editor shows both as the manager types."""
    data = request.get_json(force=True) or {}
    t = _template_payload(data)
    t.setdefault('id', ''); t.setdefault('name', '')
    lead = {'first_name': 'Dana', 'company': 'Sycamore Court HOA', 'city': 'Loveland',
            'lead_type': 'homeowner', 'hook': '', 'stage': 'new'}
    if data.get('lead_id'):
        with get_db() as db:
            row = _lead_visible(db, data['lead_id'])
            if row:
                lead = dict(row)
    t.setdefault('channel', 'text')
    out = _render_template(t, lead, pusers.display_name(current_rep()))
    return jsonify({'rendered': out, 'problems': _template_problems(t),
                    'chars': len(out['body']), 'words': len(out['body'].split())})


def _touch_count(db, lead_id):
    return db.execute(
        'SELECT COUNT(*) c FROM activities WHERE lead_id = ? AND kind IN (%s)'
        % ','.join('?' * len(OUTREACH_KINDS)),
        [lead_id] + list(OUTREACH_KINDS)).fetchone()['c']


@app.route('/api/leads/<lead_id>/messages')
@login_required
def lead_messages(lead_id):
    """Every template that fits this lead, rendered, per channel — with the
    one recommended for this touch named. What an outreach card offers."""
    rep_name = pusers.display_name(current_rep())
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        lead = dict(row)
        touches = _touch_count(db, lead_id)
        step = _draft_step(touches)
        out = {'step': step, 'touches': touches, 'audience': _audience_for(lead)}
        for ch in TEMPLATE_CHANNELS:
            fit = _templates_for(db, lead, ch)
            best = _pick(fit, step)
            out[ch] = {'recommended': best['id'] if best else '',
                       'templates': [_render_template(t, lead, rep_name) for t in fit]}
    return jsonify(out)


# ── Outcomes: what happened on a touch, and what that books next ─────────────

@app.route('/api/leads/<lead_id>/outcome', methods=['POST'])
@login_required
def log_outcome(lead_id):
    """Record a touch and what came of it, in one step.

    Logs the activity (with its outcome), completes the task being worked,
    sets the outreach status, and books the follow-up that outcome calls for —
    or, for the final outcomes, closes the lead's open tasks and cadences so
    nobody is dialled on autopilot after asking us to stop.
    """
    data = request.get_json(force=True) or {}
    o = OUTCOME_BY_KEY.get(data.get('outcome'))
    if not o:
        return jsonify({'error': 'Unknown outcome'}), 400
    kind = data.get('kind') if data.get('kind') in OUTREACH_KINDS else o['kind']
    follow_at = None
    if o.get('ask_date'):
        raw = (data.get('follow_up_at') or '').strip()
        try:
            follow_at = _iso(datetime.strptime(raw[:16], '%Y-%m-%dT%H:%M'))
        except ValueError:
            try:
                follow_at = _iso(datetime.strptime(raw[:10], '%Y-%m-%d').replace(hour=15))
            except ValueError:
                return jsonify({'error': 'Pick the day they asked you to call back.'}), 400
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        lead = dict(row)
        now = _now()
        tpl = (data.get('template_id') or '').strip()
        if tpl and not db.execute('SELECT 1 FROM templates WHERE id=?', (tpl,)).fetchone():
            tpl = ''
        _log_activity(db, lead_id, kind, body=(data.get('body') or '').strip(),
                      outcome=o['key'], template_id=tpl)

        # The task being worked (from the queue card) is done. Completing it the
        # normal way advances its cadence, whose next step may be the follow-up.
        task_id = data.get('task_id')
        advanced = False
        if task_id:
            t = db.execute('SELECT * FROM tasks WHERE id=? AND lead_id=? AND done=0',
                           (task_id, lead_id)).fetchone()
            if t:
                db.execute('UPDATE tasks SET done=1, done_at=? WHERE id=?', (now, task_id))
                if t['enrollment_id'] and o.get('cadence') and not o.get('stop'):
                    _advance_cadence(db, t['enrollment_id'])
                    advanced = True

        if o.get('stop'):
            db.execute('UPDATE tasks SET done=1, done_at=? WHERE lead_id=? AND done=0',
                       (now, lead_id))
            db.execute('UPDATE cadence_enrollments SET active=0 WHERE lead_id=? AND active=1',
                       (lead_id,))

        status = o['status']
        follow = o.get('follow')
        if o['key'] == 'no_answer':
            misses = db.execute(
                "SELECT outcome FROM activities WHERE lead_id=? AND outcome != '' "
                "ORDER BY created_at DESC LIMIT ?", (lead_id, NO_ANSWER_PARK_AFTER)).fetchall()
            if (len(misses) >= NO_ANSWER_PARK_AFTER
                    and all(m['outcome'] == 'no_answer' for m in misses)):
                status = 'nurture'
                follow = (NO_ANSWER_PARK_DAYS, 'call',
                          f'Try again - {NO_ANSWER_PARK_AFTER} unanswered in a row')
                db.execute('UPDATE tasks SET done=1, done_at=? WHERE lead_id=? AND done=0',
                           (now, lead_id))
                db.execute('UPDATE cadence_enrollments SET active=0 WHERE lead_id=? AND active=1',
                           (lead_id,))
                advanced = False

        db.execute('UPDATE leads SET outreach_status=?, outreach_status_at=?, updated_at=? '
                   'WHERE id=?', (status, now, now, lead_id))

        # A running cadence's next step already IS the follow-up. Booking a
        # second task would put the lead in front of the rep twice.
        in_cadence = db.execute('SELECT 1 FROM cadence_enrollments WHERE lead_id=? AND active=1',
                                (lead_id,)).fetchone()
        new_task = None
        if follow and not (advanced or (o.get('cadence') and in_cadence
                                        and status != 'nurture')):
            days, tkind, title = follow
            due = follow_at or _iso(_now_dt() + timedelta(days=days))
            new_task = str(uuid.uuid4())
            db.execute('INSERT INTO tasks (id, lead_id, rep, kind, title, due_at, created_at) '
                       'VALUES (?,?,?,?,?,?,?)',
                       (new_task, lead_id, lead['rep'], tkind, title, due, now))

        # The pipeline stage this outcome proves — forward only; the rep is
        # always allowed to be ahead. 'Not interested' closes a cold lead.
        target = o.get('stage')
        if target and lead['stage'] not in TERMINAL_STAGES \
                and _stage_rank(target) > _stage_rank(lead['stage']):
            db.execute('UPDATE leads SET stage=?, updated_at=? WHERE id=?', (target, now, lead_id))
            _log_activity(db, lead_id, 'stage_change',
                          body=f'{STAGE_META[lead["stage"]]["label"]} → {STAGE_META[target]["label"]}')
        if o.get('lose') and lead['stage'] not in TERMINAL_STAGES:
            db.execute("UPDATE leads SET stage='lost', lost_reason=?, updated_at=? WHERE id=?",
                       (o['lose'], now, lead_id))
            _log_activity(db, lead_id, 'stage_change',
                          body=f'{STAGE_META[lead["stage"]]["label"]} → Lost ({o["lose"]})')
        _refresh_next_action(db, lead_id)
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
        task = (dict(db.execute('SELECT * FROM tasks WHERE id=?', (new_task,)).fetchone())
                if new_task else None)
    return jsonify({'lead': _lead_row(row), 'follow_up': task})


@app.route('/api/leads/<lead_id>/contact/verify', methods=['POST'])
@login_required
def verify_contact(lead_id):
    """A rep confirms the contact on file is the right person. The strongest
    signal there is, and what puts the lead at the top of everyone's queue."""
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if not ((row['email'] or '').strip() or (row['phone'] or '').strip()):
            return jsonify({'error': 'Add a phone or email before confirming a contact.'}), 400
        db.execute('UPDATE leads SET contact_verified_at=?, contact_verified_by=?, updated_at=? '
                   'WHERE id=?', (_now(), current_rep(), _now(), lead_id))
        _refresh_contact_quality(db, lead_id)
        name = f"{row['first_name']} {row['last_name']}".strip() or 'the contact'
        _log_activity(db, lead_id, 'system', body=f'✓ Contact confirmed: {name}')
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    return jsonify(_lead_row(row))


@app.route('/api/leads/<lead_id>/contact/wrong', methods=['POST'])
@login_required
def wrong_contact(lead_id):
    """The researched contact is wrong. Clears ONLY what research put there -
    never a name or email a rep typed - and records the note, which a
    re-research then uses as its hint."""
    note = ((request.get_json(silent=True) or {}).get('note') or '').strip()[:300]
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        sets = {'contact_verified_at': '', 'contact_verified_by': '', 'updated_at': _now()}
        was = f"{row['first_name']} {row['last_name']}".strip()
        if row['contact_source'] == 'research':
            sets.update(first_name='', last_name='', email='', email_norm='', contact_source='')
        db.execute('UPDATE leads SET ' + ', '.join(f'{k}=?' for k in sets) + ' WHERE id=?',
                   list(sets.values()) + [lead_id])
        _refresh_contact_quality(db, lead_id)
        _log_activity(db, lead_id, 'system',
                      body=f'✗ Contact marked wrong{": " + was if was else ""}'
                           + (f' - {note}' if note else ''))
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    return jsonify(_lead_row(row))


@app.route('/api/research/accuracy')
@admin_required
def research_accuracy():
    """How often reps confirm what research found, by lead type - the number
    that says where research is worth the spend and where it is guessing.
    Counted from the reps' own taps: '✓ Contact confirmed' and '✗ Contact
    marked wrong' on the timeline."""
    with get_db() as db:
        rows = db.execute(
            "SELECT l.lead_type, "
            "  SUM(a.body LIKE '✓ Contact confirmed%') ok, "
            "  SUM(a.body LIKE '✗ Contact marked wrong%') bad "
            "FROM activities a JOIN leads l ON l.id = a.lead_id "
            "WHERE a.kind = 'system' AND (a.body LIKE '✓ Contact confirmed%' "
            "  OR a.body LIKE '✗ Contact marked wrong%') "
            "GROUP BY l.lead_type").fetchall()
        found = {r['lead_type']: r['n'] for r in db.execute(
            "SELECT lead_type, COUNT(*) n FROM leads WHERE contact_source = 'research' "
            "GROUP BY lead_type")}
    label = {t['key']: t['label'] for t in LEAD_TYPES}
    out = []
    for r in rows:
        judged = (r['ok'] or 0) + (r['bad'] or 0)
        out.append({'lead_type': r['lead_type'], 'label': label.get(r['lead_type'], r['lead_type']),
                    'confirmed': r['ok'] or 0, 'wrong': r['bad'] or 0,
                    'rate': round(100 * (r['ok'] or 0) / judged) if judged else None,
                    'found': found.get(r['lead_type'], 0)})
    for lt, n in found.items():
        if not any(o['lead_type'] == lt for o in out):
            out.append({'lead_type': lt, 'label': label.get(lt, lt), 'confirmed': 0,
                        'wrong': 0, 'rate': None, 'found': n})
    return jsonify(sorted(out, key=lambda o: -o['found']))


@app.route('/api/leads/<lead_id>/research', methods=['POST'])
@login_required
def research_lead(lead_id):
    """Research one lead now, optionally with a rep's hint ("they said talk to
    Mike in facilities"). Same rules as the batch run: fills only empty fields,
    only with a cited answer. About a cent, under the monthly spend cap."""
    hint = ((request.get_json(silent=True) or {}).get('hint') or '').strip()[:300]
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        lead = dict(row)
    try:
        from agents import perplexity
        from agents.b2b import reenrich
    except Exception as e:
        return jsonify({'error': f'Research is not available here: {e}'}), 503
    try:
        data, cites, _cost = reenrich.research(lead, hint=hint)
    except perplexity.SpendCapReached:
        return jsonify({'error': 'The monthly research budget is used up.'}), 429
    except Exception as e:
        return jsonify({'error': f'Research failed: {e}'}), 502
    filled = reenrich.apply(sys.modules[__name__], lead, data, cites)
    with get_db() as db:
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    out = _lead_row(row)
    out['filled'] = filled
    return jsonify(out)


@app.route('/api/leads/<lead_id>/outreach-status', methods=['PATCH'])
@login_required
def set_outreach_status(lead_id):
    """Manual correction from the lead drawer. Books nothing — outcomes do that."""
    status = (request.get_json(force=True) or {}).get('status')
    if status not in OUTREACH_STATUS_KEYS:
        return jsonify({'error': 'Unknown status'}), 400
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        db.execute('UPDATE leads SET outreach_status=?, outreach_status_at=?, updated_at=? '
                   'WHERE id=?', (status, _now(), _now(), lead_id))
        _log_activity(db, lead_id, 'system',
                      body=f'Outreach status set to {OUTREACH_STATUS_META[status]["label"]}')
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    return jsonify(_lead_row(row))


@app.route('/api/outreach/summary')
@login_required
def outreach_summary():
    """How many of this rep's contacts sit in each status, and how many of
    them have a follow-up due today. The board at the top of the Outreach tab."""
    rep = request.args.get('rep') if is_manager() else current_rep()
    clause, params = ('WHERE rep=?', [rep]) if rep else ('', [])
    with get_db() as db:
        counts = {r['s']: r['c'] for r in db.execute(
            f"SELECT COALESCE(outreach_status,'not_contacted') s, COUNT(*) c FROM leads "
            f"{clause} GROUP BY s", params)}
        due = {r['s']: r['c'] for r in db.execute(
            f"SELECT COALESCE(outreach_status,'not_contacted') s, COUNT(*) c FROM leads "
            f"{clause + (' AND ' if clause else 'WHERE ')} next_action_at != '' "
            f"AND next_action_at <= ? GROUP BY s", params + [_end_of_today()])}
    return jsonify([dict(s, count=counts.get(s['key'], 0), due=due.get(s['key'], 0))
                    for s in OUTREACH_STATUSES])


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
    contact = request.args.get('contact', '')
    cooldown = _iso(_now_dt() - timedelta(days=COOLDOWN_DAYS))

    # Homeowners on the Do Not Call Registry never reach a cold-call queue.
    dnc_l, dnc_lp = _dnc_clause('l')
    dnc_f, dnc_fp = _dnc_clause()
    with get_db() as db:
        supp = _suppression_index(db)

        # Already-scheduled work: cadence steps and manual follow-ups due by
        # end of day. dnc=0 keeps opted-out partners out even mid-cadence.
        due = [dict(r) for r in db.execute(
            'SELECT t.id, t.kind, t.title, t.due_at, t.lead_id, '
            '       l.first_name, l.last_name, l.company, l.phone, l.email, '
            '       l.website, l.address, l.city, l.stage, l.lead_type, l.icp_score, l.hook, '
            '       l.source, l.outreach_status, l.research_notes, l.contact_quality '
            'FROM tasks t JOIN leads l ON l.id = t.lead_id '
            'WHERE t.rep = ? AND t.done = 0 AND t.due_at <= ? AND l.dnc = 0 '
            f'AND NOT {dnc_l} '
            'ORDER BY t.due_at', [rep, _end_of_today()] + dnc_lp).fetchall()]
        due = [d for d in due if not _suppressed_by(
            supp, _norm_phone(d['phone']), _norm_email(d['email']), d['website'])][:target]
        if contact == 'research':
            due = []  # scheduled commitments remain in the daily outreach view
        for d in due:
            d['name'] = (f"{d['first_name']} {d['last_name']}").strip() or d['company']
            d['overdue'] = d['due_at'] < _now()

        done_today = db.execute(
            'SELECT COUNT(*) c FROM activities WHERE rep = ? AND created_at >= ? '
            'AND kind IN (%s)' % ','.join('?' * len(OUTREACH_KINDS)),
            [rep, _start_of_today()] + list(OUTREACH_KINDS)).fetchone()['c']

        # Top up with net-new. Anything with an open task is already in `due`,
        # and anything touched inside the cooldown is deliberately left alone.
        room = target if contact == 'research' else max(0, target - len(due) - done_today)
        fresh = []
        if room:
            contact_sql = ' AND ' + _contact_clause(contact) if contact in ('ready', 'research') else ''
            rows = db.execute(
                "SELECT * FROM leads "
                "WHERE rep = ? AND stage = 'new' AND dnc = 0 "
                "  AND outreach_status NOT IN ('bad_contact','nurture','not_interested','dnc','appt_set') "
                "  AND (last_activity_at = '' OR last_activity_at < ?) "
                "  AND id NOT IN (SELECT lead_id FROM tasks WHERE done = 0) "
                f"  AND NOT {dnc_f} "
                + contact_sql + " ORDER BY contact_quality DESC, icp_score DESC, created_at ASC",
                [rep, cooldown] + dnc_fp)
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
    with get_db() as db:
        for item, lid in ([(d, d['lead_id']) for d in due] + [(f, f['id']) for f in fresh]):
            item['touches'] = touches.get(lid, 0)
            item['draft'] = _render_draft(item, _draft_step(item['touches']), rep_name, db=db)
            ometa = OUTREACH_STATUS_META.get(item.get('outreach_status') or 'not_contacted',
                                             OUTREACH_STATUSES[0])
            item['outreach_label'] = ometa['label']
            item['outreach_color'] = ometa['color']

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
                db.execute('UPDATE leads SET rep = ?, updated_at = ? WHERE id = ?',
                           (reps[i % len(reps)], _now(), r['id']))

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
        by_source = {r['source'] or 'unknown': r['c'] for r in db.execute(
            f'SELECT source, COUNT(*) c FROM leads {lw} GROUP BY source', lead_params)}
        by_state = {(r['state'] or '??').upper(): r['c'] for r in db.execute(
            f'SELECT state, COUNT(*) c FROM leads {lw} GROUP BY state', lead_params)}
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
        'by_source': by_source, 'by_state': by_state, 'by_service': by_service,
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
                               "AND kind='stage_change' AND body LIKE '%→ Appt Set%'",
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
                               "AND kind='stage_change' AND body LIKE '%→ Estimate Presented%'",
                               (rep, since)).fetchone()['c']
        # Avg sales cycle (days) for won deals in window.
        cyc = db.execute("SELECT created_at, won_at FROM leads WHERE rep=? AND stage='won' AND won_at >= ?",
                         (rep, since)).fetchall()
        stalled = db.execute('SELECT COUNT(*) c FROM leads WHERE rep=? AND stage IN (%s)'
                             % ','.join('?' * len(OPEN_STAGES)), [rep] + OPEN_STAGES).fetchall()
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
            db.execute('INSERT INTO documents (id, lead_id, filename, orig_name, size, '
                       'uploaded_by, created_at) VALUES (?,?,?,?,?,?,?)',
                       (did, lead_id, stored, orig, len(blob), current_rep(), _now()))
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
        'stall_days': STALL_DAYS,
        'audiences': AUDIENCES,
        'contact_quality': CONTACT_QUALITY,
        'outreach_statuses': OUTREACH_STATUSES,
        'outcomes': [{k: v for k, v in o.items() if k != 'follow'}
                     | {'follow_days': o['follow'][0] if o.get('follow') else None}
                     for o in OUTCOMES],
        'template_channels': list(TEMPLATE_CHANNELS),
        'template_steps': list(TEMPLATE_STEPS),
        'template_slots': list(TEMPLATE_SLOTS),
        'text_max_chars': TEXT_MAX_CHARS,
        'daily_target': DAILY_TARGET,
        'cooldown_days': COOLDOWN_DAYS,
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'db': DB_PATH, 'den': bool(BASE44_TOKEN),
                    'plans': len(PLANS)})

seed_templates()

if __name__ == '__main__':
    app.run(debug=True, port=5002)
