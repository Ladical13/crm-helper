"""Shared Nimbus paths, defaults, and env lookups.

One place for the AGENTS_DATA_DIR / territories.json / spend cap plumbing so
every module stops re-deriving it.
"""
import json
import os
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)


def data_dir():
    """Where Nimbus stores its own files (cache, run manifests, drafts, config).

    Defaults to ``<SALESCRM_DATA_DIR or DATA_DIR>/agents`` so a single Railway
    volume mount holds everything. Explicit override via ``AGENTS_DATA_DIR``
    for tests and standalone dev.
    """
    override = os.environ.get('AGENTS_DATA_DIR')
    if override:
        return override
    parent = (os.environ.get('SALESCRM_DATA_DIR')
              or os.environ.get('DATA_DIR')
              or os.path.join(REPO_ROOT, '.localdata'))
    path = os.path.join(parent, 'agents')
    os.makedirs(path, exist_ok=True)
    return path


def territories_path():
    return os.path.join(data_dir(), 'territories.json')


def cache_db_path():
    return os.path.join(data_dir(), 'nimbus.db')


def drafts_dir():
    d = os.path.join(data_dir(), 'content_drafts')
    os.makedirs(d, exist_ok=True)
    return d


def runs_dir():
    d = os.path.join(data_dir(), 'runs')
    os.makedirs(d, exist_ok=True)
    return d


def settings_path():
    return os.path.join(data_dir(), 'settings.json')


DEFAULT_SETTINGS = {
    # sonar = cheap, sonar-pro = more accurate. Start on sonar for cost; move
    # per-agent overrides here if accuracy suffers.
    'perplexity_model': 'sonar',
    # Hard monthly cap. When cumulative spend crosses this, agents refuse
    # further Perplexity calls until reset. Prevents a runaway from a bad
    # prompt or an infinite loop.
    'monthly_spend_cap_usd': 150.0,
    # 30 days of caching kills most re-charge. Bump higher for slow-changing
    # research (org names, addresses) or lower for volatile topics (news).
    'cache_ttl_days': 30,
    # Which counties fall inside our service area — used only for ICP scoring
    # boosts, not filtering. Every county the reps cover should appear here.
    'service_area_counties': [
        'El Paso', 'Pueblo', 'Fremont', 'Teller', 'Denver', 'Adams',
        'Arapahoe', 'Jefferson', 'Broomfield', 'Boulder', 'Larimer', 'Weld',
        'Douglas',
    ],
}


def load_settings():
    """Merge disk over defaults so new keys always resolve to something."""
    out = dict(DEFAULT_SETTINGS)
    try:
        with open(settings_path(), encoding='utf-8') as f:
            out.update(json.load(f))
    except (FileNotFoundError, ValueError):
        pass
    return out


def save_settings(patch):
    settings = load_settings()
    settings.update(patch or {})
    with open(settings_path(), 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, sort_keys=True)
    return settings


# ── Marketing profile ────────────────────────────────────────────────────────
# What the marketing team may claim: approved services, service area, target
# customers, banned phrases, proven differentiators, credentials.

def marketing_profile_path():
    """The REPO copy, deliberately — not ``data_dir()``.

    Every other config file here lives on the volume so a manager can edit it
    from the dashboard. This one is the opposite: it is version-controlled, so
    git log is its change history and a review is a diff. Seeding it onto the
    volume would recreate the ``price_book.json`` trap, where the repo copy is
    only ever copied when absent and editing it stops changing anything.
    """
    return os.path.join(HERE, 'marketing_profile.json')


def load_marketing_profile():
    """Read the profile fresh. Raises if it is missing or malformed.

    Failing loud is the point. An agent that silently got ``{}`` here would
    have no banned phrases and no approved-service list, and would happily
    write copy offering work we don't do. Re-read on every call so a local
    edit takes effect without a restart.
    """
    path = marketing_profile_path()
    try:
        with open(path, encoding='utf-8') as f:
            profile = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(
            f'marketing profile missing at {path} — it is version-controlled '
            f'and must be present; agents refuse to write copy without it')
    except ValueError as e:
        raise ValueError(f'marketing profile at {path} is not valid JSON: {e}')
    if not isinstance(profile, dict):
        raise ValueError(f'marketing profile at {path} must be a JSON object')
    return profile


def banned_phrases():
    """Just the phrases, lowercased — the shape a copy check actually wants."""
    section = load_marketing_profile().get('phrases_to_avoid') or {}
    return [str(p.get('phrase', '')).strip().lower()
            for p in (section.get('phrases') or [])
            if str(p.get('phrase', '')).strip()]


DEFAULT_TERRITORIES = {
    'avery': {
        'display_name': 'Avery Schroeder',
        'counties': ['El Paso', 'Pueblo', 'Fremont', 'Teller'],
        'cities':   ['Colorado Springs', 'Pueblo', 'Cañon City', 'Woodland Park'],
        'segments': ['realtor', 'insurance_agent', 'hoa', 'church',
                     'school', 'gc', 'commercial'],
        'monthly_lead_cap': 400,
        'enrich_top_n':     40,
    },
    'phil': {
        'display_name': 'Phil Hunt',
        'counties': ['Denver', 'Adams', 'Arapahoe', 'Douglas'],
        'cities':   ['Denver', 'Aurora', 'Thornton', 'Centennial'],
        'segments': ['commercial', 'hoa', 'property_manager', 'church',
                     'school', 'gc'],
        'monthly_lead_cap': 400,
        'enrich_top_n':     60,
    },
    'derik': {
        'display_name': 'Derik Lints',
        'counties': ['Jefferson', 'Broomfield', 'Boulder'],
        'cities':   ['Lakewood', 'Boulder', 'Broomfield', 'Golden'],
        'segments': ['insurance_agent', 'realtor', 'hoa'],
        'monthly_lead_cap': 300,
        'enrich_top_n':     30,
    },
    'bryan': {
        'display_name': 'Bryan Samsel',
        'counties': ['Larimer', 'Weld'],
        'cities':   ['Fort Collins', 'Loveland', 'Greeley', 'Windsor'],
        'segments': ['realtor', 'insurance_agent', 'hoa', 'commercial',
                     'gc', 'church'],
        'monthly_lead_cap': 300,
        'enrich_top_n':     30,
    },
}


def load_territories():
    """Return the live per-rep config. Seeds a default file on first read."""
    path = territories_path()
    if not os.path.exists(path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_TERRITORIES, f, indent=2, sort_keys=True)
        return dict(DEFAULT_TERRITORIES)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_territories(data):
    path = territories_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data or {}, f, indent=2, sort_keys=True)
    return load_territories()


# ── Cache / runs DB ──────────────────────────────────────────────────────────
# One SQLite file for Nimbus's own state: Perplexity cache, geocode cache,
# run manifests, spend ledger, content drafts. Kept separate from salescrm.db
# so a nuke of the Nimbus state never risks a lead.

def get_cache_db():
    conn = sqlite3.connect(cache_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    _init_cache_db(conn)
    return conn


def _init_cache_db(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS perplexity_cache (
            query_hash  TEXT PRIMARY KEY,
            query       TEXT NOT NULL,
            model       TEXT NOT NULL,
            answer      TEXT NOT NULL,
            citations   TEXT DEFAULT '[]',
            cost_usd    REAL DEFAULT 0,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS geocode_cache (
            address_hash TEXT PRIMARY KEY,
            address      TEXT NOT NULL,
            lat          REAL,
            lng          REAL,
            city         TEXT DEFAULT '',
            state        TEXT DEFAULT '',
            zip          TEXT DEFAULT '',
            created_at   TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS spend_ledger (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            source      TEXT NOT NULL,       -- 'perplexity'
            reason      TEXT DEFAULT '',
            cost_usd    REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS spend_month_idx ON spend_ledger(occurred_at);

        CREATE TABLE IF NOT EXISTS agent_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            agent         TEXT NOT NULL,     -- 'b2b' | 'content'
            rep           TEXT DEFAULT '',   -- '' for content runs
            started_at    TEXT NOT NULL,
            finished_at   TEXT DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'running',
            leads_found   INTEGER DEFAULT 0,
            leads_pushed  INTEGER DEFAULT 0,
            leads_deduped INTEGER DEFAULT 0,
            cost_usd      REAL DEFAULT 0,
            error         TEXT DEFAULT '',
            summary       TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS runs_rep_idx ON agent_runs(rep, started_at DESC);

        CREATE TABLE IF NOT EXISTS content_drafts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    TEXT NOT NULL,
            platform      TEXT NOT NULL,     -- 'facebook' | 'instagram' | 'linkedin' | 'blog'
            topic         TEXT NOT NULL,
            draft_text    TEXT NOT NULL,
            citations     TEXT DEFAULT '[]',
            status        TEXT DEFAULT 'draft',   -- 'draft' | 'approved' | 'posted' | 'rejected'
            approved_by   TEXT DEFAULT '',
            approved_at   TEXT DEFAULT '',
            posted_at     TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS drafts_status_idx ON content_drafts(status, created_at DESC);

        -- ── Local SEO strategist ──────────────────────────────────────────
        -- Public-research only: no owned analytics feed these tables, so
        -- every recommendation carries the basis of its evidence and nothing
        -- claims a measured number. See agents/seo/.
        CREATE TABLE IF NOT EXISTS seo_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    TEXT NOT NULL,
            finished_at   TEXT DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'running',  -- running|ok|error
            mode          TEXT NOT NULL DEFAULT 'live',     -- live|dry_run
            pages_crawled INTEGER DEFAULT 0,
            recs_created  INTEGER DEFAULT 0,
            cost_usd      REAL DEFAULT 0,
            error         TEXT DEFAULT '',
            summary       TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS seo_runs_idx ON seo_runs(started_at DESC);

        CREATE TABLE IF NOT EXISTS seo_reports (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id     INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            week_of    TEXT NOT NULL,
            markdown   TEXT NOT NULL,
            stats      TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS seo_reports_idx ON seo_reports(created_at DESC);

        CREATE TABLE IF NOT EXISTS seo_recommendations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id         INTEGER NOT NULL,
            created_at     TEXT NOT NULL,
            category       TEXT NOT NULL,
            city           TEXT DEFAULT '',
            service        TEXT DEFAULT '',
            intent         TEXT DEFAULT '',   -- the customer question / search intent
            action         TEXT NOT NULL,
            rationale      TEXT DEFAULT '',
            evidence       TEXT DEFAULT '[]', -- JSON list of URLs
            confidence     TEXT DEFAULT 'low',        -- high|medium|low
            evidence_basis TEXT DEFAULT 'public_research',  -- public_research|owned_data
            review_notes   TEXT DEFAULT '',   -- what a human must check before acting
            score          REAL DEFAULT 0,
            status         TEXT DEFAULT 'pending',    -- pending|approved|rejected
            reviewed_by    TEXT DEFAULT '',
            reviewed_at    TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS seo_recs_idx
            ON seo_recommendations(status, score DESC, id DESC);

        -- Crawl cache. Stores EXTRACTED fields, never raw HTML: the whole
        -- point is the derived metadata, and a page body per URL would bloat
        -- the Nimbus DB for no gain.
        CREATE TABLE IF NOT EXISTS seo_page_cache (
            url         TEXT PRIMARY KEY,
            fetched_at  TEXT NOT NULL,
            status_code INTEGER DEFAULT 0,
            extracted   TEXT DEFAULT '{}',
            error       TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS trending_topics (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at  TEXT NOT NULL,
            topic        TEXT NOT NULL,
            score        REAL DEFAULT 0,
            sources      TEXT DEFAULT '[]',   -- JSON: [{source, url, snippet}, ...]
            summary      TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS topics_captured_idx ON trending_topics(captured_at DESC);
    ''')
    conn.commit()


def now_iso():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
