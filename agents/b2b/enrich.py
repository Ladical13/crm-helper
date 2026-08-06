"""Per-lead enrichment: storm join + Perplexity research.

Runs after the dispatcher has a candidate list but before it pushes to
salescrm. Two calls per lead, both cached:

  * ``storm_join.hail_summary(lat, lng)`` — free, hits canvasser's cache
  * ``perplexity.search_json(...)`` — ~$0.02 per uncached lead

Callers cap this to ``rep.enrich_top_n`` per run so the cost model stays
predictable. A rep whose top-40 covers churches/schools/GCs is ~$0.80 per
Monday run.
"""
import json

from .. import geocode, perplexity, storm_join

# The single system prompt for every research call. Deliberately blunt about
# citations and refusing to guess — accuracy dominates cost here, and reps
# would rather have "unknown" than a fabricated pastor.
_SYSTEM = (
    'You are a sales research assistant for a Colorado roofing contractor. '
    'Answer only from public sources you can cite. Cite every fact with a URL '
    'in a `citations` array. If a field is unknown, use the string "unknown" '
    '— do not guess. Return JSON only.'
)


def _prompt(row, segment):
    kind = _kind_for(segment)
    return (
        f'Research the following {kind} for a cold call from a roofing '
        f'contractor. Report:\n'
        f'- decision_maker: {{name, title}} for building maintenance / '
        f'facilities / property manager (or "unknown")\n'
        f'- size: rough building count or campus square footage if public\n'
        f'- news: any public news mentioning roof, storm, insurance claim, '
        f'construction, capital campaign, or bond in the last 24 months\n'
        f'- fiscal_year_end: month name if public, else "unknown"\n'
        f'- summary: ONE plain-English sentence a rep can drop into a cold email\n\n'
        f'Organization: {row.get("company", "").strip()}\n'
        f'Address: {row.get("address", "")}, {row.get("city", "")} '
        f'{row.get("state", "")} {row.get("zip", "")}\n'
        f'Website: {row.get("website", "unknown")}\n\n'
        f'Return JSON with exactly these keys: '
        f'decision_maker, size, news, fiscal_year_end, summary, citations'
    )


def _kind_for(segment):
    return {
        'church': 'church or congregation',
        'school': 'school or district',
        'gc':     'general contractor',
        'commercial': 'commercial building owner',
        'hoa':    'HOA or property management company',
    }.get(segment, 'organization')


def enrich_one(row, segment, model=None):
    """Return an enriched copy of ``row`` plus the DB update payload.

    The returned dict has ``research_notes`` (JSON string of the research
    body), ``research_citations`` (list of URLs), and ``recent_storm``
    (human summary) merged in. Callers can hand-off the update payload to
    ``ingest.annotate_leads(...)`` after the import commits.
    """
    out = dict(row)

    # 1. Storm join — free
    address_full = ', '.join(x for x in (row.get('address'),
                                         row.get('city'),
                                         row.get('state'),
                                         row.get('zip')) if x)
    storm = ''
    if address_full:
        loc = geocode.geocode(address_full)
        if loc and loc.get('lat') is not None:
            storm = storm_join.hail_summary(loc['lat'], loc['lng']) or ''
    out['recent_storm'] = storm

    # 2. Perplexity research — paid, cached
    try:
        result = perplexity.search_json(
            _prompt(row, segment), system=_SYSTEM, model=model, max_tokens=1500,
            reason=f'b2b-enrich:{segment}')
    except perplexity.SpendCapReached:
        # Don't fail the whole run over the cap — return the row unenriched.
        out['research_notes'] = ''
        out['research_citations'] = []
        out['enrichment_cost'] = 0.0
        return out
    data = result.get('data') or {}
    if isinstance(data, dict):
        # Move Perplexity's citations up if it didn't include them in the body.
        if not data.get('citations'):
            data['citations'] = result.get('citations') or []
    out['research_notes'] = json.dumps(data, sort_keys=True) if data else ''
    out['research_citations'] = data.get('citations') if isinstance(data, dict) else \
        (result.get('citations') or [])
    out['enrichment_cost'] = float(result.get('cost_usd') or 0.0)

    # Nudge the ICP score up if enrichment surfaced something useful.
    if storm:
        out['icp_score'] = int(out.get('icp_score', 0)) + 2
    if isinstance(data, dict) and _looks_promising(data):
        out['icp_score'] = int(out.get('icp_score', 0)) + 1

    return out


def _looks_promising(data):
    """Heuristic: a real decision maker, or news mentioning roof/storm/claim."""
    dm = data.get('decision_maker')
    if isinstance(dm, dict) and dm.get('name') and dm.get('name') != 'unknown':
        return True
    news = str(data.get('news') or '').lower()
    return any(w in news for w in ('roof', 'storm', 'hail', 'claim', 'bond', 'capital'))
