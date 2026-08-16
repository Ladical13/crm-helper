"""What the supervisor may look at, and what it may set running.

**Every tool is an HTTP call to the portal's own Nimbus API, made with the
signed-in admin's session cookie.** Nothing here reads the database directly
and nothing here imports an agent module. That is the whole safety argument:
the supervisor is exactly as powerful as the human sitting in front of the
dashboard, no more, and it inherits every guard already written — the
admin-only gate, the "a run is already in progress" 409, the
NotApproved check on brief generation — with no second copy to drift.

**It cannot approve anything.** Approving an SEO recommendation, approving or
rejecting a social draft, marking a post published: those stay a human click,
because the entire Nimbus design rests on a person having read the copy before
it can reach a customer. ``FORBIDDEN_ROUTES`` states that in code and
``test_supervisor_cannot_approve`` fails if a tool ever reaches one.

A run started here returns immediately with "started" — an SEO pass takes
minutes and a chat turn cannot hold the line that long. The supervisor is told
to say so and to check back with ``get_seo_runs`` rather than pretend it
watched the run finish.
"""
import json

from .. import config

# Routes the supervisor must never reach, as (method, path-prefix) pairs.
# These are the approve/reject/publish endpoints. Guarded by a test.
FORBIDDEN_ROUTES = (
    ('POST', '/nimbus/api/seo/recommendations/'),   # approve / reject a rec
    ('POST', '/nimbus/api/content/drafts/'),        # approve / post / reject
    ('POST', '/nimbus/api/social/drafts/'),         # approve / post / reject
    ('POST', '/nimbus/api/settings'),               # raising its own spend cap
    ('PUT',  '/nimbus/api/territories/'),           # reassigning reps
)

# The one deliberate exception: generating a brief lives UNDER the
# recommendations path but is not a review action — the endpoint refuses
# unless a human has already approved the recommendation.
_ALLOWED_UNDER_FORBIDDEN = ('/brief',)


class Denied(RuntimeError):
    """A tool tried to reach a route it is not allowed to touch. Raised rather
    than returned, because it means the tool table and the denylist disagree —
    a bug, not a user-facing outcome."""


def _check_allowed(method, path):
    for bad_method, prefix in FORBIDDEN_ROUTES:
        if method != bad_method or not path.startswith(prefix):
            continue
        if any(path.endswith(tail) for tail in _ALLOWED_UNDER_FORBIDDEN):
            continue
        raise Denied(f'{method} {path} is not available to the supervisor')


def _api(ctx, method, path, payload=None):
    """Call a Nimbus route in-process as the signed-in admin."""
    _check_allowed(method, path)
    client = ctx['client']
    if method == 'GET':
        r = client.get(path)
    else:
        r = client.open(path, method=method, json=payload or {})
    body = r.get_json(silent=True)
    if body is None:
        body = {'error': f'HTTP {r.status_code}', 'body': r.get_data(as_text=True)[:400]}
    if r.status_code >= 400:
        # Handed back to the model as a normal result, not an exception: a 409
        # "already running" is information the supervisor should relay, not a
        # crash. `is_error` on the tool_result block tells it something went
        # wrong without ending the turn.
        return {'ok': False, 'status': r.status_code, 'error': body}
    return body


# ── Read ─────────────────────────────────────────────────────────────────────

def _get_pipeline(ctx, **_):
    return _api(ctx, 'GET', '/nimbus/api/pipeline')


def _get_seo_recommendations(ctx, status='pending', category='', limit=25, **_):
    path = f'/nimbus/api/seo/recommendations?status={status}'
    if category:
        path += f'&category={category}'
    rows = _api(ctx, 'GET', path)
    return rows[:int(limit)] if isinstance(rows, list) else rows


def _get_seo_report(ctx, **_):
    return _api(ctx, 'GET', '/nimbus/api/seo/report')


def _get_seo_runs(ctx, limit=10, **_):
    return _api(ctx, 'GET', f'/nimbus/api/seo/runs?limit={int(limit)}')


def _get_content_drafts(ctx, status='draft', **_):
    return _api(ctx, 'GET', f'/nimbus/api/content/drafts?status={status}')


def _get_topics(ctx, limit=20, **_):
    return _api(ctx, 'GET', f'/nimbus/api/content/topics?limit={int(limit)}')


def _get_briefs(ctx, **_):
    return _api(ctx, 'GET', '/nimbus/api/seo/briefs')


def _get_field_notes(ctx, status='new', **_):
    return _api(ctx, 'GET', f'/nimbus/api/seo/field-notes?status={status}')


def _get_crm_questions(ctx, days=180, **_):
    return _api(ctx, 'GET', f'/nimbus/api/seo/crm-questions?days={int(days)}')


def _get_agent_runs(ctx, rep='', limit=15, **_):
    path = f'/nimbus/api/runs?limit={int(limit)}'
    if rep:
        path += f'&rep={rep}'
    return _api(ctx, 'GET', path)


def _get_connections(ctx, probe=False, **_):
    return _api(ctx, 'GET', f'/nimbus/api/connections?probe={1 if probe else 0}')


def _get_spend_and_settings(ctx, **_):
    from . import client as sup_client
    settings = _api(ctx, 'GET', '/nimbus/api/settings')
    if isinstance(settings, dict):
        settings['supervisor_month_spend_usd'] = round(sup_client.month_spend_usd(), 4)
        settings['anthropic_key_set'] = sup_client.key_is_set()
    return settings


def _get_schedule(ctx, **_):
    return _api(ctx, 'GET', '/nimbus/api/schedule')


def _get_territories(ctx, **_):
    return _api(ctx, 'GET', '/nimbus/api/territories')


def _get_marketing_profile(_ctx, **_kw):
    """The version-controlled rules about what we may claim. Read from disk
    rather than the API — there is no route for it, and it is the one file the
    supervisor must never contradict."""
    return config.load_marketing_profile()


# ── Act (start work; never approve it) ───────────────────────────────────────

def _run_seo_strategist(ctx, dry_run=False, max_pages=40, **_):
    return _api(ctx, 'POST', '/nimbus/api/seo/run',
                {'dry_run': bool(dry_run), 'max_pages': int(max_pages)})


def _run_social_drafts(ctx, max_topics=2, dry_run=False, **_):
    return _api(ctx, 'POST', '/nimbus/api/social/run',
                {'max_topics': int(max_topics), 'dry_run': bool(dry_run)})


def _run_content_listen(ctx, market='Colorado', n=10, **_):
    return _api(ctx, 'POST', '/nimbus/api/content/listen',
                {'market': market, 'n': int(n)})


def _run_b2b_prospecting(ctx, rep, segment='', city='', per_city_limit=10,
                         dry_run=True, **_):
    return _api(ctx, 'POST', '/nimbus/api/b2b/run', {
        'rep': rep, 'segment': segment or None, 'city': city or None,
        'per_city_limit': int(per_city_limit), 'dry_run': bool(dry_run),
    })


def _make_content_brief(ctx, rec_id, **_):
    return _api(ctx, 'POST',
                f'/nimbus/api/seo/recommendations/{int(rec_id)}/brief')


def _set_schedule_job(ctx, name, enabled, **_):
    return _api(ctx, 'POST', f'/nimbus/api/schedule/{name}',
                {'enabled': bool(enabled)})


# ── Tool table ───────────────────────────────────────────────────────────────
# Descriptions are prescriptive about WHEN to call, not just what the tool
# does. That is what actually drives a model to reach for the right one.

TOOLS = [
    {
        'name': 'get_pipeline',
        'description': (
            'Current sales pipeline: how many leads sit in each stage, how '
            'many are open, what was won, and the dollar value of both. Call '
            'this for any question about how sales is doing, what is in the '
            'funnel, or whether marketing is producing leads.'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_seo_recommendations',
        'description': (
            'The Local SEO Strategist\'s ranked queue of suggested actions. '
            'Each carries its evidence, confidence, and a review note. Call '
            'this when asked what SEO work is waiting, what to do next on the '
            'website, or what the strategist found. Default status "pending" '
            'is the review queue; use "approved" for work already signed off '
            'and "all" to see everything.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string',
                           'enum': ['pending', 'approved', 'rejected', 'all']},
                'category': {'type': 'string',
                             'description': 'Optional category filter.'},
                'limit': {'type': 'integer', 'description': 'Default 25.'},
            },
        },
    },
    {
        'name': 'get_seo_report',
        'description': (
            'The most recent weekly SEO report in full (Markdown) plus its '
            'stats. Call this when asked to summarise the latest SEO run or '
            'explain what changed on the site.'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_seo_runs',
        'description': (
            'History of SEO Strategist runs: when each started and finished, '
            'pages crawled, recommendations produced, cost, and any error. '
            'Call this to check whether a run you started has finished, or to '
            'answer "when did the SEO bot last run".'),
        'input_schema': {
            'type': 'object',
            'properties': {'limit': {'type': 'integer', 'description': 'Default 10.'}},
        },
    },
    {
        'name': 'get_content_drafts',
        'description': (
            'Social and blog post drafts with their text, citations, and '
            'review status. Call this when asked what content is waiting for '
            'approval, what was posted, or to read a specific draft. You can '
            'read and critique drafts but you cannot approve them.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string',
                           'enum': ['draft', 'approved', 'posted', 'rejected']},
            },
        },
    },
    {
        'name': 'get_topics',
        'description': (
            'Trending topics the content listener captured, with scores and '
            'sources. Call this when asked what people are talking about, '
            'what to post about, or where a draft\'s idea came from.'),
        'input_schema': {
            'type': 'object',
            'properties': {'limit': {'type': 'integer', 'description': 'Default 20.'}},
        },
    },
    {
        'name': 'get_briefs',
        'description': (
            'Content briefs generated from approved SEO recommendations — the '
            'spec for a page someone still has to write. Call this when asked '
            'what pages are queued to be written.'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_field_notes',
        'description': (
            'Questions somebody actually heard from a homeowner and typed in '
            'by hand. Primary-source customer language. Call this when asked '
            'what customers are really asking, or when judging whether a '
            'content idea reflects a real question.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string', 'enum': ['new', 'used', 'archived']},
            },
        },
    },
    {
        'name': 'get_crm_questions',
        'description': (
            'Questions mined out of the sales CRM notes over the last N days. '
            'Call this alongside get_field_notes when looking for content '
            'ideas grounded in what real prospects asked.'),
        'input_schema': {
            'type': 'object',
            'properties': {'days': {'type': 'integer', 'description': 'Default 180.'}},
        },
    },
    {
        'name': 'get_agent_runs',
        'description': (
            'History of B2B prospecting and content runs: leads found, pushed '
            'and deduped, cost, errors. Call this to report on prospecting '
            'activity or to check a run you started.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'rep': {'type': 'string', 'description': 'Optional rep username.'},
                'limit': {'type': 'integer', 'description': 'Default 15.'},
            },
        },
    },
    {
        'name': 'get_connections',
        'description': (
            'Status of every marketing data connection — which are live, '
            'which need someone else to act, which are blocked. Call this '
            'before claiming a data source is or is not available, and when '
            'asked why some number cannot be produced.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'probe': {'type': 'boolean',
                          'description': 'True hits the network to re-verify; '
                                         'slower. Default false reports config.'},
            },
        },
    },
    {
        'name': 'get_spend_and_settings',
        'description': (
            'Current Nimbus settings plus month-to-date spend for both the '
            'research budget and your own. Call this when asked about cost, '
            'budget, or how much the bots have spent.'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_schedule',
        'description': (
            'The weekly scheduler: which jobs exist, whether each is on, and '
            'when it last ran. Call this when asked what runs automatically '
            'or why something did not run.'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_territories',
        'description': (
            'Per-rep territory config: counties, cities, segments, monthly '
            'lead caps. Call this when a question involves who covers where, '
            'or before starting a prospecting run for a rep.'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_marketing_profile',
        'description': (
            'The version-controlled rules on what this company may claim: '
            'approved services, service area, target customers, banned '
            'phrases, provable differentiators, credentials. Call this before '
            'writing or judging any customer-facing copy, and before stating '
            'that we offer a service.'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'run_seo_strategist',
        'description': (
            'Start an SEO Strategist pass: crawl the site, research, and '
            'produce a fresh report plus ranked recommendations. Takes '
            'several minutes and returns as soon as it has STARTED — say so, '
            'and check get_seo_runs later rather than claiming it finished. '
            'Only start this when the user asks for it. Costs research '
            'budget unless dry_run is true.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'dry_run': {'type': 'boolean',
                            'description': 'True writes nothing and spends nothing.'},
                'max_pages': {'type': 'integer', 'description': 'Default 40.'},
            },
        },
    },
    {
        'name': 'run_social_drafts',
        'description': (
            'Start a run that writes a week of social post drafts from saved '
            'topics. Drafts only — nothing is ever published. Returns once '
            'STARTED. Only start this when the user asks for it.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'max_topics': {'type': 'integer', 'description': 'Default 2.'},
                'dry_run': {'type': 'boolean'},
            },
        },
    },
    {
        'name': 'run_content_listen',
        'description': (
            'Start a listening pass that captures fresh trending topics for a '
            'market. Returns once STARTED. Only start this when the user asks '
            'for it, or when they ask for content ideas and the saved topics '
            'are stale.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'market': {'type': 'string', 'description': 'Default "Colorado".'},
                'n': {'type': 'integer', 'description': 'Topics to capture. Default 10.'},
            },
        },
    },
    {
        'name': 'run_b2b_prospecting',
        'description': (
            'Start a B2B prospecting run for one rep, optionally scoped to a '
            'segment and city. This pushes leads into the sales CRM when '
            'dry_run is false, so DEFAULT TO dry_run true and confirm with '
            'the user before running it live. Returns once STARTED.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'rep': {'type': 'string',
                        'description': 'Rep username. Required. Check '
                                       'get_territories if unsure.'},
                'segment': {'type': 'string'},
                'city': {'type': 'string'},
                'per_city_limit': {'type': 'integer', 'description': 'Default 10.'},
                'dry_run': {'type': 'boolean',
                            'description': 'Default true. False writes real leads.'},
            },
            'required': ['rep'],
        },
    },
    {
        'name': 'make_content_brief',
        'description': (
            'Turn an ALREADY-APPROVED SEO recommendation into a structured '
            'content brief. Fails if the recommendation has not been approved '
            'by a human — you cannot approve it yourself, so ask the user to '
            'approve it first if this comes back refused.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'rec_id': {'type': 'integer',
                           'description': 'Recommendation id from '
                                          'get_seo_recommendations.'},
            },
            'required': ['rec_id'],
        },
    },
    {
        'name': 'set_schedule_job',
        'description': (
            'Turn a scheduled weekly job on or off. Call get_schedule first '
            'for the exact job name. Confirm with the user before switching '
            'anything off — a silently disabled job is how weekly reporting '
            'quietly stops.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string'},
                'enabled': {'type': 'boolean'},
            },
            'required': ['name', 'enabled'],
        },
    },
]

_HANDLERS = {
    'get_pipeline':             _get_pipeline,
    'get_seo_recommendations':  _get_seo_recommendations,
    'get_seo_report':           _get_seo_report,
    'get_seo_runs':             _get_seo_runs,
    'get_content_drafts':       _get_content_drafts,
    'get_topics':               _get_topics,
    'get_briefs':               _get_briefs,
    'get_field_notes':          _get_field_notes,
    'get_crm_questions':        _get_crm_questions,
    'get_agent_runs':           _get_agent_runs,
    'get_connections':          _get_connections,
    'get_spend_and_settings':   _get_spend_and_settings,
    'get_schedule':             _get_schedule,
    'get_territories':          _get_territories,
    'get_marketing_profile':    _get_marketing_profile,
    'run_seo_strategist':       _run_seo_strategist,
    'run_social_drafts':        _run_social_drafts,
    'run_content_listen':       _run_content_listen,
    'run_b2b_prospecting':      _run_b2b_prospecting,
    'make_content_brief':       _make_content_brief,
    'set_schedule_job':         _set_schedule_job,
}

# Every declared tool must have a handler and vice versa — a mismatch means a
# tool the model can call but nothing implements. Checked at import.
assert {t['name'] for t in TOOLS} == set(_HANDLERS), (
    'supervisor TOOLS and _HANDLERS disagree')

# Tools that change something. Used by the UI to badge an action differently
# from a lookup, and by the tests that assert none of them approve anything.
ACTION_TOOLS = frozenset({
    'run_seo_strategist', 'run_social_drafts', 'run_content_listen',
    'run_b2b_prospecting', 'make_content_brief', 'set_schedule_job',
})

_MAX_RESULT_CHARS = 20000


def dispatch(name, tool_input, ctx):
    """Run one tool. Returns ``(payload_text, is_error)``.

    Never raises for a bad tool call: an unknown name or a missing argument
    comes back as an error *result*, which the model can read and correct. An
    exception here would end the conversation instead.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({'error': f'unknown tool: {name}'}), True
    try:
        result = handler(ctx, **(tool_input or {}))
    except Denied as e:
        return json.dumps({'error': str(e)}), True
    except TypeError as e:
        return json.dumps({'error': f'bad arguments for {name}: {e}'}), True
    except Exception as e:                                     # noqa: BLE001
        return json.dumps({'error': f'{type(e).__name__}: {e}'}), True

    text = json.dumps(result, default=str)
    if len(text) > _MAX_RESULT_CHARS:
        # A full SEO report or a 200-row recommendation list can dwarf the
        # rest of the turn. Truncate with a visible marker so the model knows
        # to narrow its query rather than assuming it saw everything.
        text = (text[:_MAX_RESULT_CHARS]
                + f'\n\n[truncated at {_MAX_RESULT_CHARS} characters — '
                  f'narrow the query (filter by status, lower the limit) to '
                  f'see the rest]')
    return text, False
