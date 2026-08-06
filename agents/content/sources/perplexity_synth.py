"""Cross-platform trending-topic synthesis via Perplexity.

The paid tier of the listening stack. Asks Perplexity to check what's
trending across public Facebook, Instagram, LinkedIn, X, TikTok, Reddit, and
news for Colorado homeowners around roofing / hail / insurance topics —
without needing Meta/LinkedIn API approvals.

Returns a list of topics with citation URLs. Combined with the free
listening sources by ``content.listen`` (once those are wired), Perplexity
provides the synthesis layer that turns raw signal into "here's what to
talk about this week."
"""
from ... import perplexity


_SYSTEM = (
    'You track what Colorado homeowners are discussing publicly about their '
    'roofs, hail damage, insurance claims, HOAs, and property maintenance. '
    'Answer only from public posts you can cite. Never invent quotes or '
    'authors. Every trending topic must include at least one source URL.'
)


def _prompt(market='Colorado', n=10):
    return (
        f'Identify the top {n} trending discussion topics from THIS PAST '
        f'WEEK among {market} homeowners on public social media (Reddit, '
        f'Facebook, Instagram, LinkedIn) and local news, related to any of: '
        f'roof condition, hail damage, insurance claims, insurance '
        f'depreciation and deductibles, HOA roof rules, roofing contractors, '
        f'storm season prep, roof financing.\n\n'
        f'Return JSON: {{ "topics": [ '
        f'{{ "topic": "<short name>", "summary": "<1-2 sentences>", '
        f'"why_now": "<what changed this week>", "audience": "<who cares>", '
        f'"citations": ["<url>", ...] }}, ... ] }}\n\n'
        f'Rank the topics by combined signal strength (frequency × velocity '
        f'× relevance to a roofing contractor).'
    )


def pull(market='Colorado', n=10, model=None):
    """Return a list of topic dicts (may be empty on cap / API failure)."""
    try:
        r = perplexity.search_json(_prompt(market, n), system=_SYSTEM, model=model,
                                   max_tokens=3000, reason='content-listen')
    except perplexity.SpendCapReached:
        return {'topics': [], 'cost_usd': 0.0, 'note': 'monthly Perplexity cap reached'}
    data = r.get('data') or {}
    topics = data.get('topics') if isinstance(data, dict) else []
    return {'topics': list(topics or []),
            'cost_usd': float(r.get('cost_usd') or 0.0),
            'note': ''}
