"""Merge topics across sources and rank them.

Deliberately simple and explainable — no model call, no opaque weighting.

The cross-source part was written as an intention long before it could run:
with only Perplexity reporting, every topic had exactly one source and the
bonus was dead code. Reddit and GDELT make it real, and it is the main reason
wiring free sources is worth more than the sum of their rows. A question asked
on Reddit *and* covered in local news *and* surfaced by Perplexity is a
different proposition from one that appears once.
"""
import re

# Topic keywords that map to actionable roofing content. A topic touching one
# of these is what a roofing company can meaningfully say something about.
_ACTIONABILITY_KEYWORDS = {
    'hail', 'storm', 'roof', 'shingle', 'leak', 'insurance', 'claim',
    'depreciation', 'adjuster', 'deductible', 'hoa', 'reserve', 'financing',
    'estimate', 'inspection', 'siding', 'gutter', 'window',
}

# Words too common to indicate two topics are the same subject.
_STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'for', 'to', 'of', 'in', 'on', 'at',
    'is', 'are', 'was', 'were', 'be', 'been', 'my', 'your', 'our', 'their',
    'this', 'that', 'these', 'those', 'it', 'its', 'i', 'we', 'you', 'they',
    'do', 'does', 'did', 'can', 'could', 'should', 'would', 'will', 'with',
    'from', 'by', 'as', 'how', 'what', 'why', 'when', 'any', 'anyone', 'help',
    'new', 'get', 'got', 'need', 'about', 'have', 'has', 'not', 'me',
}

# How much each additional independent source is worth. Capped so a topic
# cannot ride source count alone — three thin mentions is not a strong signal.
_SOURCE_BONUS = 1.2
_MAX_SOURCE_BONUS = 3.0

# Overlap needed to call two topics the same subject. Tuned to be
# conservative: wrongly merging two distinct questions loses one of them
# silently, which is worse than showing a near-duplicate a human can spot.
MERGE_THRESHOLD = 0.6


def _terms(text):
    words = re.findall(r'[a-z0-9]+', (text or '').lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _similar(a, b):
    """Jaccard-ish overlap against the smaller term set."""
    ta, tb = _terms(a), _terms(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def merge_sources(*topic_lists):
    """Combine topic lists from different sources, unioning their evidence.

    Each input topic may carry ``source_names``; anything without one is
    treated as coming from an unnamed source so it still merges correctly.
    Returns topics with a deduplicated ``source_names`` and ``citations``.
    """
    merged = []
    for topics in topic_lists:
        for t in (topics or []):
            if not isinstance(t, dict) or not (t.get('topic') or '').strip():
                continue
            incoming = dict(t)
            incoming['source_names'] = list(incoming.get('source_names') or ['unknown'])
            incoming['citations'] = list(incoming.get('citations') or [])

            for existing in merged:
                if _similar(existing['topic'], incoming['topic']) >= MERGE_THRESHOLD:
                    # Keep the longer phrasing — it is usually the more
                    # specific question rather than a headline fragment.
                    if len(incoming['topic']) > len(existing['topic']):
                        existing['topic'] = incoming['topic']
                    existing['source_names'] = sorted(
                        set(existing['source_names']) | set(incoming['source_names']))
                    existing['citations'] = list(dict.fromkeys(
                        existing['citations'] + incoming['citations']))
                    existing['summary'] = (existing.get('summary')
                                           or incoming.get('summary', ''))
                    existing['is_local'] = bool(existing.get('is_local')
                                                or incoming.get('is_local'))
                    break
            else:
                merged.append(incoming)
    return merged


def _score(topic):
    """0.0..10.0. Higher = call this out first in the drafts."""
    text = ' '.join(str(topic.get(k, '')).lower()
                    for k in ('topic', 'summary', 'why_now', 'audience'))
    actionability = sum(1 for kw in _ACTIONABILITY_KEYWORDS if kw in text)
    citations = len(topic.get('citations') or [])

    # Independent corroboration. This is the payoff for wiring free sources:
    # one topic in three sources beats three topics in one.
    sources = len(set(topic.get('source_names') or []) - {'unknown'})
    source_bonus = min(max(sources - 1, 0) * _SOURCE_BONUS, _MAX_SOURCE_BONUS)

    # A Colorado-local subreddit is a prospect; the same question nationally
    # is a stranger.
    local_bonus = 1.0 if topic.get('is_local') else 0.0

    return round(min(10.0, actionability * 1.5 + min(citations, 6) * 0.5
                     + source_bonus + local_bonus), 2)


def rank(topics):
    scored = []
    for t in topics or []:
        if not isinstance(t, dict):
            continue
        out = dict(t)
        out['score'] = _score(t)
        out['source_names'] = sorted(set(out.get('source_names') or []))
        scored.append(out)
    scored.sort(key=lambda t: -t['score'])
    return scored


def rank_merged(*topic_lists):
    """Merge then rank — the normal entry point once >1 source is live."""
    return rank(merge_sources(*topic_lists))
