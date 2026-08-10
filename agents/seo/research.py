"""Public web research via Perplexity — customer questions and competitors.

Every call goes through ``agents.perplexity``, so it inherits the existing
30-day cache, the spend ledger and the monthly cap. A run that would exceed
the cap degrades to "no research this week" rather than failing the whole
strategy report: the crawl-derived recommendations are still worth having.

Nothing here is treated as measurement. Perplexity tells us what people ask
and who else is visible; it does not tell us our rankings, and the system
prompt in ``honesty.RESEARCH_SYSTEM`` forbids it from pretending otherwise.
"""
from .. import config, perplexity
from .honesty import RESEARCH_SYSTEM


def sibling_domains():
    """Same-brand franchise sites. Ours in name only, and never rivals.

    projectoneroofing.com is the Texas franchise. Public research for
    "roofing contractor Colorado" will not usually surface it, but research
    for the brand name certainly can — and reporting it as a competitor would
    have us benchmarking against ourselves, while treating it as ours would
    produce copy for a market we do not serve.
    """
    try:
        profile = config.load_marketing_profile()
    except (FileNotFoundError, ValueError):
        return []
    siblings = (profile.get('company', {}).get('sibling_sites') or {}).get('sites') or []
    return [str(s.get('domain', '')).lower() for s in siblings if s.get('domain')]


def _is_sibling(url_or_name):
    text = str(url_or_name or '').lower()
    return any(d and d in text for d in sibling_domains())


class ResearchUnavailable(RuntimeError):
    """Research could not run — no API key, or the spend cap is exhausted."""


def _citations(result):
    """URLs backing a research answer, from whichever field Perplexity used."""
    data = result.get('data') or {}
    urls = []
    for c in (result.get('citations') or []):
        if isinstance(c, str) and c.startswith('http'):
            urls.append(c)
    if isinstance(data, dict):
        for c in (data.get('sources') or data.get('citations') or []):
            if isinstance(c, str) and c.startswith('http'):
                urls.append(c)
            elif isinstance(c, dict) and str(c.get('url', '')).startswith('http'):
                urls.append(c['url'])
    # Preserve order, drop repeats.
    return list(dict.fromkeys(urls))


def customer_questions(service_label, city, n=6, model=None):
    """What homeowners in ``city`` actually ask about ``service_label``.

    Returns ``{questions: [{question, why_it_matters}], citations, cost_usd}``.
    """
    prompt = (
        f'What questions do homeowners and property managers in {city}, '
        f'Colorado actually ask when they are considering {service_label} '
        f'work? Focus on the wording real people use, including local factors '
        f'specific to the Colorado Front Range. Return the {n} most common.\n\n'
        f'Return JSON: {{"questions": [{{"question": "...", '
        f'"why_it_matters": "..."}}], "sources": ["https://..."]}}'
    )
    try:
        r = perplexity.search_json(prompt, system=RESEARCH_SYSTEM, model=model,
                                   max_tokens=1200,
                                   reason=f'seo-questions:{city}:{service_label}')
    except perplexity.SpendCapReached as e:
        raise ResearchUnavailable(str(e)) from e
    except perplexity.PerplexityError as e:
        raise ResearchUnavailable(str(e)) from e
    data = r.get('data') or {}
    questions = data.get('questions') if isinstance(data, dict) else None
    return {
        'questions': [q for q in (questions or []) if isinstance(q, dict) and q.get('question')],
        'citations': _citations(r),
        'cost_usd': float(r.get('cost_usd') or 0.0),
        'cached': bool(r.get('cached')),
    }


def competitor_landscape(city, service_label, n=5, model=None):
    """Who else is visible for this work in this city, and what they cover.

    Deliberately asks about *topics covered*, never about performance. We
    cannot see a competitor's traffic or rankings, so we don't ask — a number
    returned here would be a guess dressed as data.
    """
    prompt = (
        f'Which roofing and exterior contractors have a visible web presence '
        f'for {service_label} in {city}, Colorado? For each, list the service '
        f'or location topics their public website covers. Do NOT estimate '
        f'traffic, rankings, or performance — describe only what is publicly '
        f'visible on their site.\n\n'
        f'Return JSON: {{"competitors": [{{"name": "...", "url": "...", '
        f'"topics_covered": ["..."]}}], "content_gaps": ["..."], '
        f'"sources": ["https://..."]}}'
    )
    try:
        r = perplexity.search_json(prompt, system=RESEARCH_SYSTEM, model=model,
                                   max_tokens=1400,
                                   reason=f'seo-competitors:{city}:{service_label}')
    except perplexity.SpendCapReached as e:
        raise ResearchUnavailable(str(e)) from e
    except perplexity.PerplexityError as e:
        raise ResearchUnavailable(str(e)) from e
    data = r.get('data') or {}
    if not isinstance(data, dict):
        data = {}
    competitors = [c for c in (data.get('competitors') or [])
                   if isinstance(c, dict) and c.get('name')]
    # Drop our own sibling franchises before anything downstream sees them.
    kept = [c for c in competitors
            if not (_is_sibling(c.get('url')) or _is_sibling(c.get('name')))]
    excluded = len(competitors) - len(kept)
    gaps = [g for g in (data.get('content_gaps') or [])
            if isinstance(g, str) and not _is_sibling(g)]
    return {
        'competitors': kept,
        'content_gaps': gaps,
        'citations': [u for u in _citations(r) if not _is_sibling(u)],
        'excluded_siblings': excluded,
        'cost_usd': float(r.get('cost_usd') or 0.0),
        'cached': bool(r.get('cached')),
    }
