"""Weekly listen pass: pull trending topics from every available source.

v1 relies on Perplexity synthesis (paid) since the free-source APIs need
credentials configured. As each source is wired up, its results feed into
``score.rank(...)`` alongside the Perplexity output.
"""
from .. import config
from . import score
from .sources import perplexity_synth
import json


def run(market='Colorado', n=10, model=None):
    """Pull, score, persist. Returns the ranked topic list."""
    synth = perplexity_synth.pull(market=market, n=n, model=model)
    topics = synth.get('topics') or []
    ranked = score.rank(topics)

    # Persist for the dashboard's "trending topics" panel.
    with config.get_cache_db() as db:
        for t in ranked:
            db.execute(
                'INSERT INTO trending_topics (captured_at, topic, score, sources, summary) '
                'VALUES (?, ?, ?, ?, ?)',
                (config.now_iso(),
                 t.get('topic', '')[:200],
                 float(t.get('score', 0)),
                 json.dumps(t.get('citations') or []),
                 t.get('summary', '')[:600]))
        db.commit()

    return {'topics': ranked, 'cost_usd': float(synth.get('cost_usd') or 0.0),
            'note': synth.get('note', '')}
