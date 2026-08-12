"""The guardrails. Every recommendation passes through here before it is saved.

Five rules, all testable:

  1. **No measured numbers we cannot measure.** Rankings, search volume,
     traffic, conversions, competitor performance — we have no owned data
     source, so any sentence asserting one is a fabrication. Rejected.
  2. **Indirect evidence must say so.** Anything derived from public research
     is labelled a *public-research opportunity*, never presented as a finding
     about our actual performance.
  3. **Research claims need citations.** A recommendation whose basis is web
     research and which carries no evidence URL is dropped, not softened.
  4. **Only approved services and real cities.** The marketing profile is the
     boundary. A recommendation pitching work we don't do never reaches a human.
  5. **The profile's banned phrases apply here too.** One list, one place.

Rejection is deliberate. A recommendation that trips a rule is dropped with a
reason rather than rewritten, because silently "fixing" a hallucinated number
leaves the surrounding reasoning intact and still wrong.
"""
import re

from .. import config

PUBLIC_RESEARCH = 'public_research'
OWNED_DATA      = 'owned_data'
# Our own CRM notes and field notes. A genuinely different category from
# OWNED_DATA: that means owned *search analytics*, which we do not have and
# cannot fake. This means records we wrote ourselves about conversations we
# actually had — so "mentioned in 7 logged conversations" is a fact we can
# stand behind, while "ranks #3" remains impossible either way.
FIRST_PARTY     = 'first_party'

# Phrasing that asserts a measurement. Each pattern is a claim we would need
# Search Console, GA4 or a paid rank tracker to make — and we have none.
_FABRICATED_METRIC_PATTERNS = [
    (r'\brank(?:s|ed|ing)?\s+(?:#|no\.?\s*|number\s*)?\d+',   'claims a specific ranking'),
    (r'\bposition\s+#?\d+',                                    'claims a SERP position'),
    (r'#\d+\s+(?:on|in)\s+google',                             'claims a Google ranking'),
    (r'\b\d[\d,.]*\s*(?:k|m)?\s*(?:monthly\s+)?searches?\b',   'claims a search volume'),
    (r'\bsearch volume\b',                                     'claims a search volume'),
    (r'\b\d[\d,.]*\s*(?:visits|sessions|pageviews|clicks|impressions)\b',
                                                               'claims a traffic number'),
    (r'\b\d[\d,.]*%\s*(?:of\s+)?(?:conversion|conversions|ctr|click.through)',
                                                               'claims a conversion rate'),
    (r'\bconversion rate of\b',                                'claims a conversion rate'),
    (r'\b(?:receives?|gets?|drives?)\s+\d[\d,.]*\s*(?:visitors|leads|calls)',
                                                               'claims measured traffic'),
    (r'\bdomain authority\s*(?:of|:)?\s*\d+',                  'claims a third-party metric'),
    (r'\bwe rank\b',                                           'claims a ranking'),
    (r'\boutranks?\b',                                         'claims relative ranking'),
]

# Confidence is about how well-evidenced a recommendation is, never about how
# likely it is to "work" — we cannot measure outcomes.
VALID_CONFIDENCE = ('high', 'medium', 'low')

CATEGORIES = (
    'improve_existing_page',
    'create_service_page',
    'create_city_or_service_area_page',
    'faq_or_content_brief',
    'internal_linking_opportunity',
    'technical_website_fix',
    'google_business_profile_opportunity',
)

# Categories that describe something we can see directly on our own site. These
# may be stated as fact — "this page has no meta description" is an observation,
# not a claim about Google.
_DIRECT_OBSERVATION_CATEGORIES = {
    'improve_existing_page', 'technical_website_fix', 'internal_linking_opportunity',
}


class Rejected(ValueError):
    """A recommendation broke an honesty rule and will not be saved."""


def _profile_bounds():
    """Approved services and the cities we may name, from the marketing profile."""
    profile = config.load_marketing_profile()
    services = {c['key'] for c in profile['approved_services']['categories']}
    labels   = {c['label'].lower() for c in profile['approved_services']['categories']}
    cities = set()
    for m in profile['service_area'].get('priority_markets') or []:
        for c in m.get('examples') or []:
            cities.add(c.lower())
    return services, labels, cities


def find_fabricated_metrics(text):
    """Return [(matched text, why)] for every unmeasurable claim in ``text``."""
    hits = []
    for pattern, why in _FABRICATED_METRIC_PATTERNS:
        for m in re.finditer(pattern, text or '', re.IGNORECASE):
            hits.append((m.group(0), why))
    return hits


def find_banned_phrases(text):
    """The marketing profile's banned phrases, whole-word, case-insensitive."""
    found = []
    for phrase in config.banned_phrases():
        if re.search(rf'\b{re.escape(phrase)}\b', text or '', re.IGNORECASE):
            found.append(phrase)
    return found


def label_for(rec):
    """The prefix a human sees. Indirect evidence must announce itself."""
    if rec.get('evidence_basis') == OWNED_DATA:
        return ''
    if rec.get('evidence_basis') == FIRST_PARTY:
        return 'From our own records'
    if rec.get('category') in _DIRECT_OBSERVATION_CATEGORIES:
        # Observed on our own page — still not owned *analytics*, but it is a
        # direct observation rather than an inference about the market.
        return 'Observed on our site'
    return 'Public-research opportunity'


def check(rec):
    """Validate one recommendation. Returns it (with a label) or raises Rejected.

    Kept pure and dict-in/dict-out so the tests can throw adversarial input at
    it without standing up a crawl or a Perplexity call.
    """
    if rec.get('category') not in CATEGORIES:
        raise Rejected(f'unknown category {rec.get("category")!r}')
    if rec.get('confidence') not in VALID_CONFIDENCE:
        raise Rejected(f'confidence must be one of {VALID_CONFIDENCE}')

    basis = rec.get('evidence_basis') or PUBLIC_RESEARCH
    if basis not in (PUBLIC_RESEARCH, OWNED_DATA, FIRST_PARTY):
        raise Rejected(f'unknown evidence_basis {basis!r}')
    if basis == OWNED_DATA:
        # v1 has no owned data source at all. Anything claiming one is wrong
        # by construction, and would license exactly the numeric claims the
        # rest of this module exists to prevent.
        raise Rejected('evidence_basis "owned_data" is impossible in v1 — '
                       'no owned analytics source is connected')

    prose = ' '.join(str(rec.get(k) or '') for k in
                     ('intent', 'action', 'rationale', 'review_notes'))

    fabricated = find_fabricated_metrics(prose)
    if fabricated:
        what, why = fabricated[0]
        raise Rejected(f'{why}: {what!r} — no owned data source can support this')

    banned = find_banned_phrases(prose)
    if banned:
        raise Rejected(f'uses banned phrase(s) from the marketing profile: {banned}')

    # Research-derived recommendations must show their work. A direct
    # observation of our own page is evidenced by the page URL itself.
    # Evidence is normally a URL, but a first-party finding cites an internal
    # record instead (`crm:lead:abc123`, `field-note:12`). Both are sources; a
    # claim with neither is not shippable.
    evidence = rec.get('evidence') or []
    if not evidence:
        raise Rejected('no evidence — a claim with no source is not shippable')

    services, service_labels, cities = _profile_bounds()
    service = (rec.get('service') or '').strip().lower()
    if service and service not in services and service not in service_labels:
        raise Rejected(f'service {service!r} is not in approved_services')

    if not (rec.get('action') or '').strip():
        raise Rejected('no recommended action')
    if not (rec.get('review_notes') or '').strip():
        raise Rejected('no human-review requirements — every recommendation '
                       'needs to say what a person must check')

    out = dict(rec)
    out['evidence_basis'] = basis
    out['label'] = label_for(out)
    return out


def filter_all(recs):
    """Check a batch. Returns (kept, [(rec, reason), ...]).

    Rejections are returned rather than logged away: a run that silently drops
    half its output looks identical to a run that found half as much.
    """
    kept, dropped = [], []
    for rec in recs:
        try:
            kept.append(check(rec))
        except Rejected as e:
            dropped.append((rec, str(e)))
    return kept, dropped


# The system prompt every Perplexity call in this package must carry. Belt and
# braces: the model is told the rules, and check() enforces them regardless.
RESEARCH_SYSTEM = (
    'You research local home-services marketing for a Colorado roofing and '
    'exterior contractor. Absolute rules:\n'
    '- NEVER state a search volume, keyword difficulty, traffic number, '
    'ranking position, conversion rate, or any competitor performance metric. '
    'You do not have access to that data and neither does the caller.\n'
    '- NEVER invent reviews, ratings, certifications, prices, storm events, '
    'insurance rules, or customer quotes. If you do not have a source, omit it.\n'
    '- Every factual claim must be traceable to a URL you return.\n'
    '- Prefer real questions real homeowners ask, phrased the way they ask them.\n'
    '- If you do not know, say "unknown". A short honest answer beats a long '
    'plausible one.'
)
