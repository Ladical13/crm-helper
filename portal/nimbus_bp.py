"""Nimbus — the AI agent dashboard, mounted at /nimbus/*.

Admin-only. Every route defends behind ``users.is_admin(username)``. The
before_request auth guard in portal/app.py has already ensured the request is
signed in — this only gates on role.

Runs the B2B and content agents in background threads so a click on
"Run now" returns immediately and the dashboard shows progress via polling.
No async framework: one worker thread per run, tracked in ``agent_runs``.
"""
import json
import os
import sys
import threading
import traceback

from flask import Blueprint, jsonify, request, session

from portal import users as pusers

# The Nimbus blueprint imports the agents package. That import is lazy
# because the package pulls in `requests` and the salescrm module lookup,
# and we want a portal that starts cleanly even if `PERPLEXITY_API_KEY` isn't
# set yet.


nimbus_bp = Blueprint('nimbus', __name__, url_prefix='/nimbus')


# ── Auth ─────────────────────────────────────────────────────────────────────

def _admin_only():
    from flask import jsonify as _j
    if not pusers.is_admin(session.get('username')):
        return _j({'error': 'admin only'}), 403
    return None


@nimbus_bp.before_request
def _gate():
    # portal/app.py already blocks anonymous requests via its default-deny
    # before_request hook. Layer role on top: even a signed-in rep gets 403.
    denied = _admin_only()
    if denied is not None:
        return denied


# ── Dashboard shell ──────────────────────────────────────────────────────────

_SHELL_HTML = None  # cached at import time


def _shell():
    global _shell_html_cached
    from flask import current_app
    static_root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'static', 'nimbus')
    with open(os.path.join(static_root, 'nimbus.html'), encoding='utf-8') as f:
        return f.read()


@nimbus_bp.route('/')
@nimbus_bp.route('/rep/<username>')
@nimbus_bp.route('/marketing/topics')
@nimbus_bp.route('/marketing/drafts')
@nimbus_bp.route('/marketing/connections')
@nimbus_bp.route('/marketing/seo')
@nimbus_bp.route('/marketing/social')
@nimbus_bp.route('/settings')
def shell(**_kw):
    return _shell()


# ── Local SEO strategist (public research only, read-only) ───────────────────

_seo_lock = threading.Lock()
_seo_active = {'running': False, 'stage': '', 'manifest': None}


@nimbus_bp.route('/api/seo/runs', methods=['GET'])
def seo_runs():
    from agents import config
    from agents.seo import run as seo
    # A run killed by a restart would otherwise read "running" forever.
    seo.reap_stale_runs()
    limit = int(request.args.get('limit', 20))
    with config.get_cache_db() as db:
        rows = db.execute(
            'SELECT id, started_at, finished_at, status, mode, pages_crawled, '
            'recs_created, cost_usd, error, summary FROM seo_runs '
            'ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    with _seo_lock:
        active = dict(_seo_active)
    return jsonify({'runs': [dict(r) for r in rows], 'active': active})


@nimbus_bp.route('/api/seo/run', methods=['POST'])
def seo_run():
    """Kick off a strategist pass. ``dry_run`` writes nothing at all."""
    data = request.get_json(force=True, silent=True) or {}
    dry_run = bool(data.get('dry_run'))
    max_pages = int(data.get('max_pages') or 40)

    with _seo_lock:
        if _seo_active['running']:
            return jsonify({'error': 'a run is already in progress'}), 409
        _seo_active.update({'running': True, 'stage': 'crawling', 'manifest': None})

    def worker():
        from agents.seo import run as seo
        try:
            manifest = seo.run(dry_run=dry_run, max_pages=max_pages)
        except Exception as e:                                   # noqa: BLE001
            manifest = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        with _seo_lock:
            _seo_active.update({'running': False, 'stage': 'done',
                                'manifest': manifest})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'started': True, 'dry_run': dry_run}), 202


@nimbus_bp.route('/api/seo/result', methods=['GET'])
def seo_result():
    """The last finished run's manifest — how a dry run is read back.

    A dry run persists nothing, so this in-process handoff is the only place
    its output exists.
    """
    with _seo_lock:
        return jsonify(dict(_seo_active))


@nimbus_bp.route('/api/seo/report', methods=['GET'])
def seo_report():
    from agents import config
    report_id = request.args.get('id')
    with config.get_cache_db() as db:
        if report_id:
            row = db.execute('SELECT * FROM seo_reports WHERE id = ?',
                             (report_id,)).fetchone()
        else:
            row = db.execute('SELECT * FROM seo_reports ORDER BY id DESC '
                             'LIMIT 1').fetchone()
    if not row:
        return jsonify({'report': None})
    out = dict(row)
    try:
        out['stats'] = json.loads(out.get('stats') or '{}')
    except (ValueError, TypeError):
        out['stats'] = {}
    return jsonify({'report': out})


@nimbus_bp.route('/api/seo/recommendations', methods=['GET'])
def seo_recommendations():
    from agents import config
    status = request.args.get('status', 'pending')
    category = request.args.get('category', '')
    where, params = [], []
    if status and status != 'all':
        where.append('status = ?'); params.append(status)
    if category:
        where.append('category = ?'); params.append(category)
    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    with config.get_cache_db() as db:
        rows = db.execute(
            f'SELECT * FROM seo_recommendations {clause} '
            f'ORDER BY score DESC, id DESC LIMIT 200', params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['evidence'] = json.loads(d.get('evidence') or '[]')
        except (ValueError, TypeError):
            d['evidence'] = []
        out.append(d)
    return jsonify(out)


@nimbus_bp.route('/api/seo/recommendations/<int:rec_id>', methods=['POST'])
def seo_review(rec_id):
    """Approve or reject. Records a decision — never performs the work."""
    from agents.seo import run as seo
    data = request.get_json(force=True, silent=True) or {}
    try:
        changed = seo.set_status(rec_id, data.get('status'),
                                 session.get('username', ''))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not changed:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True, 'acted': False,
                    'note': 'decision recorded — a human still does the work'})


# ── Content briefs (approved recommendations only) ───────────────────────────

@nimbus_bp.route('/api/seo/briefs', methods=['GET'])
def seo_briefs():
    from agents.seo import brief as brief_mod
    return jsonify(brief_mod.list_briefs())


@nimbus_bp.route('/api/seo/briefs/<int:brief_id>', methods=['GET'])
def seo_brief(brief_id):
    from agents.seo import brief as brief_mod
    row = brief_mod.get(brief_id)
    if not row:
        return jsonify({'error': 'not found'}), 404
    row['markdown'] = brief_mod.to_markdown(row['brief'])
    return jsonify(row)


@nimbus_bp.route('/api/seo/recommendations/<int:rec_id>/brief', methods=['POST'])
def seo_make_brief(rec_id):
    """Generate a structured content brief. Approved recommendations only."""
    from agents.seo import brief as brief_mod
    try:
        brief = brief_mod.create_for_recommendation(rec_id)
    except brief_mod.NotApproved as e:
        return jsonify({'error': str(e)}), 409
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    return jsonify({'ok': True, 'brief': brief,
                    'markdown': brief_mod.to_markdown(brief)}), 201


# ── Marketing connections (read-only status) ─────────────────────────────────

@nimbus_bp.route('/api/connections', methods=['GET'])
def list_connections():
    """Connector status for the Marketing Connections page.

    Never returns a secret — ``connections.status_all()`` emits env var names,
    set/unset booleans and non-secret IDs only. Guarded by
    ``test_connections_api_never_returns_a_secret``.

    ``?probe=0`` reports configuration without touching the network, which is
    what the page paints first; the Re-check button then calls with probing on.
    """
    from agents import connections
    probe = request.args.get('probe', '1') not in ('0', 'false', 'no')
    return jsonify({'connections': connections.status_all(probe=probe),
                    'summary': connections.summary()})


# ── Territories ──────────────────────────────────────────────────────────────

@nimbus_bp.route('/api/territories', methods=['GET'])
def get_territories():
    from agents import config
    territories = config.load_territories()
    # Bolt on portal display names + role.
    out = []
    for username, cfg in sorted(territories.items()):
        u = pusers.get(username)
        out.append({
            'username':      username,
            'display_name':  cfg.get('display_name') or (u and u['full_name']) or username.title(),
            'exists':        bool(u),
            'role':          (u or {}).get('role', 'unknown'),
            'counties':      cfg.get('counties') or [],
            'cities':        cfg.get('cities') or [],
            'segments':      cfg.get('segments') or [],
            'monthly_lead_cap': int(cfg.get('monthly_lead_cap', 400) or 0),
            'enrich_top_n':     int(cfg.get('enrich_top_n', 40) or 0),
        })
    return jsonify(out)


@nimbus_bp.route('/api/territories/<username>', methods=['PUT'])
def set_territory(username):
    from agents import config
    data = request.get_json(force=True, silent=True) or {}
    territories = config.load_territories()
    cfg = territories.get(username) or {}
    for key in ('display_name', 'counties', 'cities', 'segments'):
        if key in data:
            cfg[key] = data[key]
    if 'monthly_lead_cap' in data:
        try:
            cfg['monthly_lead_cap'] = int(data['monthly_lead_cap'])
        except (TypeError, ValueError):
            return jsonify({'error': 'monthly_lead_cap must be an integer'}), 400
    if 'enrich_top_n' in data:
        try:
            cfg['enrich_top_n'] = int(data['enrich_top_n'])
        except (TypeError, ValueError):
            return jsonify({'error': 'enrich_top_n must be an integer'}), 400
    territories[username] = cfg
    config.save_territories(territories)
    return jsonify(cfg)


# ── Settings & spend ─────────────────────────────────────────────────────────

@nimbus_bp.route('/api/settings', methods=['GET'])
def get_settings():
    from agents import config, perplexity
    settings = config.load_settings()
    settings['month_spend_usd'] = round(perplexity.month_spend_usd(), 4)
    settings['perplexity_key_set'] = bool(os.environ.get('PERPLEXITY_API_KEY'))
    return jsonify(settings)


@nimbus_bp.route('/api/settings', methods=['POST'])
def update_settings():
    from agents import config
    patch = request.get_json(force=True, silent=True) or {}
    allowed = {'perplexity_model', 'monthly_spend_cap_usd', 'cache_ttl_days',
               'service_area_counties'}
    cleaned = {k: v for k, v in patch.items() if k in allowed}
    saved = config.save_settings(cleaned)
    return jsonify(saved)


# ── Runs ─────────────────────────────────────────────────────────────────────

_active_runs = {}     # run_id -> {'thread': Thread, 'stage': str, 'manifest': dict}
_active_runs_lock = threading.Lock()


@nimbus_bp.route('/api/runs', methods=['GET'])
def list_runs():
    from agents import config
    limit = int(request.args.get('limit', 25))
    rep = request.args.get('rep', '')
    where = 'WHERE 1=1'
    params = []
    if rep:
        where += ' AND rep = ?'
        params.append(rep)
    with config.get_cache_db() as db:
        rows = db.execute(
            f'SELECT id, agent, rep, started_at, finished_at, status, '
            f'leads_found, leads_pushed, leads_deduped, cost_usd, error, summary '
            f'FROM agent_runs {where} ORDER BY id DESC LIMIT ?',
            params + [limit]).fetchall()
    return jsonify([dict(r) for r in rows])


@nimbus_bp.route('/api/runs/<int:run_id>', methods=['GET'])
def get_run(run_id):
    from agents import config
    with config.get_cache_db() as db:
        row = db.execute('SELECT * FROM agent_runs WHERE id = ?',
                         (run_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    out = dict(row)
    with _active_runs_lock:
        active = _active_runs.get(run_id)
        if active:
            out['stage'] = active.get('stage', '')
    return jsonify(out)


@nimbus_bp.route('/api/b2b/run', methods=['POST'])
def start_b2b_run():
    data = request.get_json(force=True, silent=True) or {}
    rep = (data.get('rep') or '').strip()
    if not rep:
        return jsonify({'error': 'rep is required'}), 400
    segment = data.get('segment')
    city = data.get('city')
    dry_run = bool(data.get('dry_run'))
    per_city_limit = int(data.get('per_city_limit') or 10)

    # Build an in-process client with the caller's session so the b2b import
    # goes through /crm/api/prospects/import authenticated as the admin.
    from portal.wsgi import application
    from werkzeug.test import Client

    caller_cookie = request.cookies.get('p1session')
    if not caller_cookie:
        return jsonify({'error': 'no session cookie to forward'}), 400

    def do_run():
        # Late import to avoid pulling agents.b2b into the portal at boot.
        from agents.b2b import dispatcher
        client = Client(application)
        client.set_cookie('p1session', caller_cookie, domain='localhost')
        try:
            manifest = dispatcher.run(
                rep,
                segments=[segment] if segment else None,
                cities=[city] if city else None,
                per_city_limit=per_city_limit,
                dry_run=dry_run,
                client=client,
            )
            _record_active(manifest.get('run_id'), stage='done', manifest=manifest)
        except Exception as e:                                       # noqa: BLE001
            _record_active(None, stage=f'error: {e}', manifest={'error': str(e),
                                                                'traceback': traceback.format_exc()})

    # Kick off the run and return the id once dispatcher.run has opened it.
    # dispatcher.run opens the run BEFORE returning, so we can't grab the id
    # up front — instead we hold the caller until the thread has recorded it.
    result_holder = {}

    def wrapped():
        try:
            from agents.b2b import dispatcher
            client = Client(application)
            client.set_cookie('p1session', caller_cookie, domain='localhost')
            manifest = dispatcher.run(
                rep,
                segments=[segment] if segment else None,
                cities=[city] if city else None,
                per_city_limit=per_city_limit,
                dry_run=dry_run,
                client=client,
            )
            result_holder['manifest'] = manifest
            _record_active(manifest.get('run_id'), stage='done', manifest=manifest)
        except Exception as e:                                       # noqa: BLE001
            result_holder['error'] = str(e)
            result_holder['traceback'] = traceback.format_exc()

    thread = threading.Thread(target=wrapped, daemon=True)
    thread.start()
    # Give the dispatcher a moment to open its run row so the client can start
    # polling; this returns a placeholder id if the run row hasn't landed yet.
    thread.join(timeout=1.0)

    # Look up the most recent b2b run for this rep — the one this thread just
    # opened (or is about to).
    from agents import config
    with config.get_cache_db() as db:
        row = db.execute(
            "SELECT id FROM agent_runs WHERE agent='b2b' AND rep=? "
            "ORDER BY id DESC LIMIT 1", (rep,)).fetchone()
    run_id = row['id'] if row else None
    if run_id:
        with _active_runs_lock:
            _active_runs[run_id] = {'thread': thread, 'stage': 'running',
                                    'manifest': None}
    return jsonify({'run_id': run_id, 'rep': rep, 'started': True}), 202


def _record_active(run_id, stage, manifest):
    if run_id is None:
        return
    with _active_runs_lock:
        entry = _active_runs.get(run_id) or {}
        entry['stage'] = stage
        entry['manifest'] = manifest
        _active_runs[run_id] = entry


# ── Content ──────────────────────────────────────────────────────────────────

@nimbus_bp.route('/api/content/topics', methods=['GET'])
def list_topics():
    from agents import config
    limit = int(request.args.get('limit', 20))
    with config.get_cache_db() as db:
        rows = db.execute(
            'SELECT id, captured_at, topic, score, sources, summary '
            'FROM trending_topics ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['sources'] = json.loads(d.get('sources') or '[]')
        except (ValueError, TypeError):
            d['sources'] = []
        out.append(d)
    return jsonify(out)


@nimbus_bp.route('/api/content/listen', methods=['POST'])
def content_listen():
    data = request.get_json(force=True, silent=True) or {}
    market = (data.get('market') or 'Colorado').strip()
    n = int(data.get('n') or 10)

    def wrapped():
        from agents.content import listen
        try:
            listen.run(market=market, n=n)
        except Exception:                                            # noqa: BLE001
            pass    # error surfaces in the topics list being empty

    threading.Thread(target=wrapped, daemon=True).start()
    return jsonify({'started': True}), 202


@nimbus_bp.route('/api/social/drafts', methods=['GET'])
def social_drafts():
    from agents.content import posts
    return jsonify(posts.list_drafts(status=request.args.get('status', 'draft')))


@nimbus_bp.route('/api/social/run', methods=['POST'])
def social_run():
    """Write a week's post packages. Drafts only — nothing is published."""
    data = request.get_json(force=True, silent=True) or {}
    dry_run = bool(data.get('dry_run'))
    max_topics = int(data.get('max_topics') or 2)

    with _seo_lock:
        if _seo_active['running']:
            return jsonify({'error': 'a run is already in progress'}), 409
        _seo_active.update({'running': True, 'stage': 'writing posts',
                            'manifest': None})

    def worker():
        from agents.content import posts
        try:
            out = posts.weekly_run(max_topics=max_topics, dry_run=dry_run)
            out['ok'] = True
        except Exception as e:                                   # noqa: BLE001
            out = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        with _seo_lock:
            _seo_active.update({'running': False, 'stage': 'done', 'manifest': out})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'started': True, 'dry_run': dry_run}), 202


@nimbus_bp.route('/api/social/drafts/<int:draft_id>', methods=['POST'])
def social_review(draft_id):
    """Approve, reject, or mark posted. Marking posted records that a HUMAN
    posted it — nothing here publishes anything."""
    from agents import config
    data = request.get_json(force=True, silent=True) or {}
    status = data.get('status')
    if status not in ('draft', 'approved', 'rejected', 'posted'):
        return jsonify({'error': 'unknown status'}), 400
    now = config.now_iso()
    with config.get_cache_db() as db:
        sets, params = ['status = ?'], [status]
        if status == 'approved':
            sets += ['approved_by = ?', 'approved_at = ?']
            params += [session.get('username', ''), now]
        if status == 'posted':
            sets.append('posted_at = ?'); params.append(now)
        if data.get('draft_text') is not None:
            sets.append('draft_text = ?'); params.append(data['draft_text'])
        params.append(draft_id)
        cur = db.execute(
            f'UPDATE content_drafts SET {", ".join(sets)} WHERE id = ?', params)
        db.commit()
        if not cur.rowcount:
            return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True, 'published': False,
                    'note': 'recorded — Nimbus does not publish anything'})


# ── Scheduler ────────────────────────────────────────────────────────────────

@nimbus_bp.route('/api/schedule', methods=['GET'])
def schedule_list():
    from agents import scheduler
    return jsonify({'enabled': scheduler.enabled(), 'jobs': scheduler.list_jobs()})


@nimbus_bp.route('/api/schedule/<name>', methods=['POST'])
def schedule_toggle(name):
    from agents import scheduler
    data = request.get_json(force=True, silent=True) or {}
    if not scheduler.set_enabled(name, bool(data.get('enabled'))):
        return jsonify({'error': 'unknown job'}), 404
    return jsonify({'ok': True, 'jobs': scheduler.list_jobs()})


@nimbus_bp.route('/api/content/drafts', methods=['GET'])
def list_drafts():
    from agents import config
    status = request.args.get('status', '')
    where, params = '', []
    if status:
        where = 'WHERE status = ?'
        params.append(status)
    with config.get_cache_db() as db:
        rows = db.execute(
            f'SELECT id, created_at, platform, topic, draft_text, citations, '
            f'status, approved_by, approved_at, posted_at '
            f'FROM content_drafts {where} ORDER BY id DESC LIMIT 100',
            params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['citations'] = json.loads(d.get('citations') or '[]')
        except (ValueError, TypeError):
            d['citations'] = []
        out.append(d)
    return jsonify(out)


@nimbus_bp.route('/api/content/drafts/<int:draft_id>', methods=['POST'])
def update_draft(draft_id):
    """Approve, mark posted, reject, or edit."""
    from agents import config
    data = request.get_json(force=True, silent=True) or {}
    status = data.get('status')
    if status not in ('draft', 'approved', 'posted', 'rejected'):
        return jsonify({'error': 'unknown status'}), 400
    text = data.get('draft_text')
    now = config.now_iso()
    with config.get_cache_db() as db:
        sets, params = ['status = ?'], [status]
        if status == 'approved':
            sets.append('approved_by = ?'); params.append(session.get('username', ''))
            sets.append('approved_at = ?'); params.append(now)
        if status == 'posted':
            sets.append('posted_at = ?'); params.append(now)
        if text is not None:
            sets.append('draft_text = ?'); params.append(text)
        params.append(draft_id)
        cur = db.execute(f'UPDATE content_drafts SET {", ".join(sets)} WHERE id = ?', params)
        db.commit()
        if not cur.rowcount:
            return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True})


@nimbus_bp.route('/api/content/topics/<int:topic_id>/draft', methods=['POST'])
def draft_from_topic(topic_id):
    """Draft posts for one saved topic."""
    from agents import config
    data = request.get_json(force=True, silent=True) or {}
    platforms = data.get('platforms') or ['facebook', 'instagram', 'linkedin']

    with config.get_cache_db() as db:
        row = db.execute('SELECT * FROM trending_topics WHERE id = ?',
                         (topic_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    topic = {'topic': row['topic'], 'summary': row['summary'],
             'citations': json.loads(row['sources'] or '[]')}

    def wrapped():
        from agents.content import draft
        try:
            draft.draft_topic(topic, platforms=tuple(platforms))
        except Exception:                                            # noqa: BLE001
            pass

    threading.Thread(target=wrapped, daemon=True).start()
    return jsonify({'started': True}), 202


# ── Pipeline pulse (proxy salescrm) ──────────────────────────────────────────

@nimbus_bp.route('/api/pipeline')
def pipeline_pulse():
    """Aggregate open-pipeline + this-week-signed for the dashboard center panel.

    Calls salescrm's own endpoints via the in-process test client so this
    stays a thin adapter — no duplicated stage/revenue math.
    """
    from portal.wsgi import application
    from werkzeug.test import Client
    caller_cookie = request.cookies.get('p1session')
    if not caller_cookie:
        return jsonify({'error': 'no session cookie'}), 400
    c = Client(application)
    c.set_cookie('p1session', caller_cookie, domain='localhost')
    r = c.get('/crm/api/leads')
    if r.status_code != 200:
        # Surface an empty pulse rather than a red 500 in the corner of the
        # dashboard — the CRM might just be empty in a fresh dev environment.
        return jsonify({'stage_counts': {}, 'open_leads': 0,
                        'won_this_period': 0, 'won_value': 0, 'open_value': 0})
    leads = r.get_json() or []
    stage_counts = {}
    for l in leads:
        stage_counts[l.get('stage', 'new')] = stage_counts.get(l.get('stage', 'new'), 0) + 1
    total_open = sum(v for k, v in stage_counts.items() if k not in ('won', 'lost'))
    won_leads  = [l for l in leads if l.get('stage') == 'won']
    won_value  = sum(float(l.get('est_value') or 0) for l in won_leads)
    open_value = sum(float(l.get('est_value') or 0) for l in leads
                     if l.get('stage') not in ('won', 'lost'))
    return jsonify({
        'stage_counts': stage_counts,
        'open_leads':   total_open,
        'won_this_period': len(won_leads),
        'won_value':    won_value,
        'open_value':   open_value,
    })
