"""Weekly listen pass: pull trending topics from every available source.

Sources are independent and optional. Each one that is configured contributes;
each one that is not says so and the pass continues. That matters because the
paid source is the one most likely to be unavailable — a blown spend cap must
not take the free sources down with it.

The pass records which sources actually reported, so a thin week is
distinguishable from a broken one.
"""
import json

from .. import config
from . import score
from .sources import news_gdelt, perplexity_synth, reddit


def sources_status():
    """What is wired up right now, for the dashboard and the run notes."""
    import os
    return {
        'perplexity': {'available': bool(os.environ.get('PERPLEXITY_API_KEY', '').strip()),
                       'needs': 'PERPLEXITY_API_KEY', 'cost': 'paid, capped'},
        'reddit':     {'available': reddit.available(),
                       'needs': 'REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET',
                       'cost': 'free'},
        'gdelt':      {'available': news_gdelt.available(),
                       'needs': 'nothing', 'cost': 'free'},
    }


def run(market='Colorado', n=10, model=None, use_paid=True):
    """Pull, merge, score, persist. Returns the ranked topic list.

    ``use_paid=False`` runs the free sources only — useful for a cheap extra
    pass mid-week, and for proving the free sources carry their own weight.
    """
    lists, notes, cost = [], [], 0.0

    # ── Free sources first, so a paid failure never costs us them ──
    r_res = reddit.pull()
    if r_res.get('posts'):
        lists.append(reddit.as_topics(r_res))
    notes.append(f'reddit: {r_res.get("note", "")}')

    g_res = news_gdelt.pull(days=7)
    if g_res.get('articles'):
        lists.append(news_gdelt.as_topics(g_res))
    notes.append(f'gdelt: {g_res.get("note", "")}')

    # ── Paid synthesis last ──
    if use_paid:
        try:
            synth = perplexity_synth.pull(market=market, n=n, model=model)
            topics = synth.get('topics') or []
            for t in topics:
                t.setdefault('source_names', ['perplexity'])
            if topics:
                lists.append(topics)
            cost += float(synth.get('cost_usd') or 0.0)
            notes.append(f'perplexity: {len(topics)} topic(s)')
        except Exception as e:                                   # noqa: BLE001
            # A spend cap or a missing key is one fewer input, not a failure.
            notes.append(f'perplexity: unavailable ({type(e).__name__})')
    else:
        notes.append('perplexity: skipped (free sources only)')

    ranked = score.rank_merged(*lists)

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

    return {
        'topics': ranked,
        'cost_usd': round(cost, 4),
        'sources_used': sorted({s for t in ranked
                                for s in (t.get('source_names') or [])}),
        'note': ' | '.join(notes),
    }
