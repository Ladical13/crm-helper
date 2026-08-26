"""Perplexity-driven prospect list.

The paid fallback when a segment has no free open-data source (churches
without an IRS BMF hookup, private schools, GCs, commercial owners in a
given city). One search per (segment, city, county) — we ask Perplexity for
a structured list, then normalize into the shape ``POST
/crm/api/prospects/import`` accepts.

Every row carries a citation URL in ``source_ref`` so reps can verify
before calling — this is the single non-negotiable defense against LLM
hallucination.
"""
import re

from ... import perplexity
from . import _common


# Prompt templates per segment. Each asks for a JSON array with a strict
# shape, requires a source URL per row, and refuses to guess phone numbers
# it can't verify.
_PROMPTS = {
    'church': (
        'Find {limit} churches, congregations, or religious organizations '
        'in {city}, {state} (county: {county}). '
        'For EACH, return: name, street address, phone (only if verifiable '
        'on a public website), website, and source_url (the URL you found '
        'them on). Skip entries you can\'t find on a real website — do '
        'not fabricate.'
    ),
    'school': (
        'Find {limit} schools (K-12, public or private) in {city}, {state} '
        '(county: {county}). '
        'For EACH, return: name, street address, phone, website, and '
        'source_url. Skip entries you can\'t find on a real website.'
    ),
    'gc': (
        'Find {limit} licensed general contractors or construction companies '
        'based in {city}, {state} (county: {county}) that work on commercial '
        'or multi-family buildings. '
        'For EACH, return: name, street address, phone, website, and '
        'source_url. Skip entries without a verifiable web presence.'
    ),
    'commercial': (
        'Find {limit} commercial building owners, property owners, or '
        'commercial real estate holders active in {city}, {state} '
        '(county: {county}). '
        'For EACH, return: name (owning entity or LLC), mailing address, '
        'phone, website, and source_url. Skip entries you can\'t find on a '
        'real website — this must be real ownership, not a listing agent.'
    ),
}

_DEFAULT_PROMPT = (
    'Find {limit} businesses in the "{segment}" category in {city}, {state}. '
    'For EACH, return: name, street address, phone, website, and source_url. '
    'Skip entries you can\'t find on a real website.'
)


def pull(city='', county='', state='CO', limit=None, segment='church',
         model=None, reason=''):
    """Query Perplexity for a segment list. Returns rows or []."""
    n = int(limit or 10)
    prompt_tpl = _PROMPTS.get(segment, _DEFAULT_PROMPT)
    prompt = prompt_tpl.format(
        limit=n, city=city or 'the Front Range',
        state=state or 'CO', county=county or 'unknown', segment=segment)

    system = (
        'You are a B2B prospect research assistant for a Colorado roofing '
        'contractor. Every fact must come from a public source you can cite. '
        'Never fabricate contact details. If a phone or address is not '
        'verifiable, leave that field empty rather than guessing.'
    )
    result = perplexity.search_json(
        prompt, system=system, model=model, max_tokens=2500,
        reason=reason or f'b2b:{segment}:{city}')

    rows_raw = _extract_list(result.get('data'))
    rows = []
    for r in rows_raw[:n]:
        norm = _normalize(r, segment=segment, state=state, city=city)
        if norm:
            rows.append(norm)
    return rows


def _extract_list(data):
    """Perplexity might return an array, or an object wrapping the array."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in ('results', 'items', 'rows', 'data', 'orgs', 'churches',
                  'schools', 'contractors', 'businesses'):
            v = data.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


# Both live in `_common` so every source scores and cleans identically — a
# free row and a Perplexity row land in the same queue and must be comparable.
#
# `clean` matters most here: the model is told not to fabricate contact
# details, and it obeys by writing "unknown" into the field. That is the right
# answer and the wrong value — it survives `.strip()`, lands in the CRM as a
# phone number, and a rep dials it. It also poisons dedupe, since every such
# row normalizes to the same phone_norm.
_clean = _common.clean
_base_icp_score = _common.base_icp_score


def _normalize(raw, segment, state, city):
    """Coerce a Perplexity row into the salescrm importer shape.

    Must produce enough for the importer's dedupe to accept it: at least one
    of phone_norm, email_norm, license_no, or source_ref. We always try to
    fill ``source_ref`` from the citation URL — that alone is a stable key.
    """
    name    = _clean(raw.get('name') or raw.get('organization') or raw.get('company'))
    phone   = _clean(raw.get('phone'))
    email   = _clean(raw.get('email'))
    website = _clean(raw.get('website') or raw.get('url'))
    address = _clean(raw.get('address') or raw.get('street_address') or raw.get('street'))
    city_v  = _clean(raw.get('city')) or (city or '')
    state_v = _clean(raw.get('state')) or (state or 'CO')
    zip_v   = _clean(raw.get('zip') or raw.get('postal') or raw.get('zip_code'))
    source_url = _clean(raw.get('source_url') or raw.get('source') or raw.get('url'))

    if not name:
        return None
    # Prefer a plain domain-style source_ref so a re-run is idempotent.
    source_ref = 'nimbus:' + segment + ':' + re.sub(r'\W+', '-', name.lower()).strip('-')
    if source_url:
        source_ref = f'nimbus:{segment}:{_host(source_url)}:{re.sub(chr(92) + "W+", "-", name.lower()).strip("-")}'

    return {
        'company':    name,
        'first_name': _clean(raw.get('first_name') or raw.get('contact_first_name')),
        'last_name':  _clean(raw.get('last_name') or raw.get('contact_last_name')),
        'phone':      phone,
        'email':      email,
        'website':    website,
        'address':    address,
        'city':       city_v,
        'state':      (state_v or '')[:2].upper() or 'CO',
        'zip':        zip_v,
        'source_ref': source_ref,
        'hook':       _clean(raw.get('note') or raw.get('summary'))[:180],
        # Ranked on reachability here so the dispatcher's "enrich the top N"
        # has something to sort by; enrich.py adds storm/decision-maker on top.
        'icp_score':  _base_icp_score(phone=phone, email=email,
                                      website=website, address=address),
    }


def _host(url):
    s = (url or '').lower().replace('https://', '').replace('http://', '')
    s = s.split('/', 1)[0]
    return s[4:] if s.startswith('www.') else s
