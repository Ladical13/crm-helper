"""Rank topics by combined signal strength.

Deliberately simple and explainable. When free sources come online, extend
``_score`` to include cross-source overlap (a topic mentioned on Reddit AND
GDELT AND in the Perplexity synth is worth more than one that's only in one).
"""

# Topic keywords that map to actionable roofing content. A topic touching one
# of these is what a roofing company can meaningfully say something about.
_ACTIONABILITY_KEYWORDS = {
    'hail', 'storm', 'roof', 'shingle', 'leak', 'insurance', 'claim',
    'depreciation', 'adjuster', 'deductible', 'hoa', 'reserve', 'financing',
    'estimate', 'inspection',
}


def _score(topic):
    """0.0..10.0. Higher = call this out first in the drafts."""
    text = ' '.join(str(topic.get(k, '')).lower()
                    for k in ('topic', 'summary', 'why_now', 'audience'))
    actionability = sum(1 for kw in _ACTIONABILITY_KEYWORDS if kw in text)
    citations = len(topic.get('citations') or [])
    # Cap so a topic can't dominate on citations alone.
    return round(min(10.0, actionability * 1.5 + min(citations, 6) * 0.5), 2)


def rank(topics):
    scored = []
    for t in topics or []:
        if not isinstance(t, dict):
            continue
        s = _score(t)
        out = dict(t)
        out['score'] = s
        scored.append(out)
    scored.sort(key=lambda t: -t['score'])
    return scored
