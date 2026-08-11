"""Classify search intent and estimate business value for a topic.

**These are topics, not keywords.** A keyword tool would tell you how many
people search a phrase and how hard it is to rank for. We have no such tool
and no owned data, so nothing here produces a volume, a difficulty or a
traffic estimate — and ``honesty.py`` rejects any recommendation that tries.

What this *can* do honestly is sort candidate topics by two things we can
reason about from first principles:

  * **Intent** — what someone typing this actually wants. "roof replacement
    cost fort collins" is somebody shopping; "how long does a roof last" is
    somebody reading. Both matter, differently.
  * **Business value** — how close that intent sits to a roofing job we
    actually sell, in a market the marketing profile prioritises.

That ordering is a judgement, clearly labelled as one. It is not a ranking
prediction and must never be presented as one.
"""
import re

from .. import config

# Intent classes, ordered by how close the searcher is to buying.
TRANSACTIONAL = 'transactional'      # ready to hire
COMMERCIAL    = 'commercial'         # comparing options / researching a purchase
INFORMATIONAL = 'informational'      # learning, may become a buyer
NAVIGATIONAL  = 'navigational'       # looking for a specific company

INTENT_ORDER = (TRANSACTIONAL, COMMERCIAL, INFORMATIONAL, NAVIGATIONAL)

INTENT_LABEL = {
    TRANSACTIONAL: 'Ready to hire',
    COMMERCIAL:    'Comparing options',
    INFORMATIONAL: 'Learning',
    NAVIGATIONAL:  'Looking for a company by name',
}

# Weight by how directly the intent leads to a job. Informational is not
# scored near zero on purpose: in roofing, "does insurance cover hail damage"
# is asked by someone standing in a yard full of shingles.
_INTENT_WEIGHT = {
    TRANSACTIONAL: 1.0,
    COMMERCIAL:    0.8,
    INFORMATIONAL: 0.55,
    NAVIGATIONAL:  0.2,
}

_PATTERNS = [
    (TRANSACTIONAL, [
        r'\bnear me\b', r'\bcost\b', r'\bprice[sd]?\b', r'\bquote\b',
        r'\bestimate\b', r'\bhire\b', r'\bcontractor[s]?\b', r'\bcompan(?:y|ies)\b',
        r'\bhow much\b', r'\breplace(?:ment)?\b', r'\brepair\b', r'\binstall',
        r'\bemergency\b', r'\bsame.day\b', r'\bfinancing\b',
    ]),
    (COMMERCIAL, [
        r'\bbest\b', r'\btop\b', r'\bvs\.?\b', r'\bversus\b', r'\bcompare\b',
        r'\breview[s]?\b', r'\brated\b', r'\bwhich\b', r'\boptions?\b',
        r'\bworth it\b', r'\bpros and cons\b', r'\btypes? of\b',
    ]),
    (INFORMATIONAL, [
        r'^how\b', r'^what\b', r'^why\b', r'^when\b', r'^does\b', r'^do\b',
        r'^can\b', r'^is\b', r'^are\b', r'\bhow long\b', r'\bsigns? of\b',
        r'\bguide\b', r'\bmean[s]?\b', r'\bcovered by\b',
    ]),
]


def classify(text):
    """Return (intent, why). Deterministic and explainable — no model call."""
    t = (text or '').strip().lower()
    if not t:
        return INFORMATIONAL, 'no text to classify — defaulted to informational'

    profile = _profile_safe()
    brand = (profile.get('company', {}).get('name') or '').lower()
    if brand and brand.split()[0] in t and len(t.split()) <= 6:
        return NAVIGATIONAL, 'mentions the company name'

    # "roofing in fort collins" — a service plus a place, no question word.
    # This is the single most commercially valuable pattern in local search,
    # and it matches none of the keyword patterns below because it contains no
    # verb at all. Without this rule it fell through to the informational
    # default and the content plan called for an FAQ instead of a city page.
    if _names_service(t) and _names_city(t) and not _is_question(t):
        return TRANSACTIONAL, 'names a service and a city we serve'

    for intent, patterns in _PATTERNS:
        for p in patterns:
            m = re.search(p, t)
            if m:
                return intent, f'matched {m.group(0)!r}'

    # A service plus a question word is someone researching that service.
    if _names_service(t) and _is_question(t):
        return INFORMATIONAL, 'asks a question about a service we offer'
    return INFORMATIONAL, 'no buying signal found — treated as informational'


_QUESTION_OPENERS = re.compile(
    r'^(how|what|why|when|where|which|does|do|did|can|could|should|is|are|will)\b')


def _is_question(t):
    return bool(_QUESTION_OPENERS.match(t.strip())) or t.strip().endswith('?')


def _names_service(t):
    profile = _profile_safe()
    for c in (profile.get('approved_services', {}).get('categories') or []):
        if c.get('key', '').lower() in t or c.get('label', '').lower() in t:
            return True
        for kind in (c.get('types') or []):
            if kind.lower() in t:
                return True
    return False


def _names_city(t):
    return any(c in t for c in _priority_cities())


def _profile_safe():
    try:
        return config.load_marketing_profile()
    except (FileNotFoundError, ValueError):
        return {}


def _priority_cities():
    cities = set()
    for m in (_profile_safe().get('service_area', {}).get('priority_markets') or []):
        for c in (m.get('examples') or []):
            cities.add(c.lower())
    return cities


def business_value(text, intent=None, city='', service=''):
    """0..10. How close this topic sits to work we sell, where we sell it.

    A judgement, not a measurement. Named so in every surface that shows it.
    """
    intent = intent or classify(text)[0]
    score = 6.0 * _INTENT_WEIGHT.get(intent, 0.5)

    # A named service we actually offer is worth more than a general query.
    services = {c['key']: c for c in
                (_profile_safe().get('approved_services', {}).get('categories') or [])}
    body = f'{text} {service}'.lower()
    if service and service in services:
        score += 1.5
    elif any(k in body for k in services):
        score += 1.0

    # Local intent is the whole point of local SEO.
    if city and city.lower() in _priority_cities():
        score += 2.0
    elif any(c in (text or '').lower() for c in _priority_cities()):
        score += 1.5
    elif re.search(r'\bcolorado\b|\bco\b', (text or '').lower()):
        score += 0.5

    return round(min(10.0, score), 2)


def opportunity(text, city='', service=''):
    """One ranked topic opportunity, with its reasoning attached."""
    intent, why = classify(text)
    return {
        'topic': text,
        'intent': intent,
        'intent_label': INTENT_LABEL[intent],
        'intent_reason': why,
        'city': city,
        'service': service,
        'business_value': business_value(text, intent, city, service),
        # Said plainly, everywhere this travels: there is no volume behind it.
        'basis': 'Judgement from public research. No search volume, difficulty '
                 'or traffic data exists for this topic — we have no keyword tool '
                 'and no Search Console access.',
    }


def rank(opportunities):
    """Sort by business value, then by how close the intent is to buying."""
    return sorted(
        opportunities,
        key=lambda o: (-o['business_value'], INTENT_ORDER.index(o['intent'])))
