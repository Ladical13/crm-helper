"""Thin sync wrapper around the Perplexity Sonar API.

Reads ``PERPLEXITY_API_KEY`` from env. Every call is cached to a SQLite table
keyed by (model, prompt) hash — a re-query inside the TTL is free and returns
the cached answer verbatim. Also updates the spend ledger so the Nimbus
dashboard's cost meter is grounded in real numbers, not estimates.

Never call anything that mutates the world from here — this is search only.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None

from . import config

API_URL = 'https://api.perplexity.ai/chat/completions'

# Per-model pricing, USD per 1M tokens (input, output) plus per-1000-search fee.
# Verify at api.perplexity.ai/pricing before locking spend caps — Perplexity
# has re-priced their tiers before. If you see nan / negative spend, this
# table drifted from their live pricing.
_PRICE = {
    'sonar':               {'in':  1.0, 'out':  1.0, 'search': 5.0},
    'sonar-pro':           {'in':  3.0, 'out': 15.0, 'search': 5.0},
    'sonar-reasoning':     {'in':  1.0, 'out':  5.0, 'search': 5.0},
    'sonar-reasoning-pro': {'in':  2.0, 'out':  8.0, 'search': 5.0},
}
_DEFAULT_PRICE = _PRICE['sonar']


class PerplexityError(RuntimeError):
    """Any non-retryable failure — bad key, rate limit exhausted, invalid model."""


class SpendCapReached(RuntimeError):
    """The global monthly spend cap is exhausted. Nothing further will run
    until the cap is raised in Nimbus Settings or the month rolls over."""


def _hash(model, prompt, system):
    h = hashlib.sha256()
    h.update((model or '').encode('utf-8'))
    h.update(b'\x1e')
    h.update((system or '').encode('utf-8'))
    h.update(b'\x1e')
    h.update((prompt or '').encode('utf-8'))
    return h.hexdigest()


def _month_start_iso():
    now = datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def month_spend_usd():
    """Sum of every logged Perplexity charge this UTC month."""
    with config.get_cache_db() as db:
        row = db.execute(
            'SELECT COALESCE(SUM(cost_usd), 0) AS s FROM spend_ledger '
            "WHERE source = 'perplexity' AND occurred_at >= ?",
            (_month_start_iso(),)).fetchone()
    return float(row['s'] or 0)


def _record_spend(cost, reason):
    with config.get_cache_db() as db:
        db.execute('INSERT INTO spend_ledger (occurred_at, source, reason, cost_usd) '
                   'VALUES (?, ?, ?, ?)',
                   (config.now_iso(), 'perplexity', reason, cost))
        db.commit()


def _estimate_cost(model, in_tokens, out_tokens, searches):
    p = _PRICE.get(model, _DEFAULT_PRICE)
    return (in_tokens * p['in'] + out_tokens * p['out']) / 1_000_000 + searches * p['search'] / 1000


def search(query, model=None, system=None, temperature=0.2, max_tokens=1500,
           cache_ttl_days=None, reason='', force_refresh=False):
    """Run one Perplexity search-and-synthesize call. Cached by (model, prompt).

    Returns ``{answer, citations, cost_usd, cached}``. Never raises for a
    cache hit; raises ``SpendCapReached`` when the monthly cap is exhausted
    and this would be a live call.
    """
    settings = config.load_settings()
    model = model or settings.get('perplexity_model', 'sonar')
    cache_ttl_days = int(cache_ttl_days
                         if cache_ttl_days is not None
                         else settings.get('cache_ttl_days', 30))

    key = _hash(model, query, system or '')
    now = datetime.utcnow()

    if not force_refresh:
        with config.get_cache_db() as db:
            row = db.execute(
                'SELECT answer, citations, cost_usd, expires_at FROM perplexity_cache '
                'WHERE query_hash = ?', (key,)).fetchone()
        if row and row['expires_at'] >= config.now_iso():
            return {
                'answer':    row['answer'],
                'citations': json.loads(row['citations'] or '[]'),
                'cost_usd':  0.0,           # cache hits are free
                'cached':    True,
                'model':     model,
            }

    # Cache miss — do a live call, unless we've blown the spend cap.
    cap = float(settings.get('monthly_spend_cap_usd', 150.0) or 0)
    spent = month_spend_usd()
    if cap and spent >= cap:
        raise SpendCapReached(
            f'Monthly Perplexity cap of ${cap:.2f} already reached (spent ${spent:.2f}). '
            f'Raise the cap in Nimbus Settings or wait until next month.')

    api_key = os.environ.get('PERPLEXITY_API_KEY', '').strip()
    if not api_key:
        raise PerplexityError('PERPLEXITY_API_KEY is not set — cannot make a live call.')
    if requests is None:
        raise PerplexityError('The `requests` package is not installed.')

    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': query})

    payload = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': int(max_tokens),
    }
    try:
        r = requests.post(API_URL,
                          headers={'Authorization': f'Bearer {api_key}',
                                   'Content-Type': 'application/json'},
                          json=payload, timeout=60)
    except requests.exceptions.RequestException as e:
        raise PerplexityError(f'Perplexity request failed: {e}') from e
    if r.status_code == 429:
        raise PerplexityError('Perplexity rate limited (HTTP 429). '
                              'Back off and retry, or upgrade the tier.')
    if not r.ok:
        raise PerplexityError(f'Perplexity HTTP {r.status_code}: {r.text[:400]}')
    body = r.json()

    answer = ''
    choices = body.get('choices') or []
    if choices:
        answer = (choices[0].get('message') or {}).get('content', '') or ''
    citations = body.get('citations') or []
    usage = body.get('usage') or {}
    in_tokens  = int(usage.get('prompt_tokens', 0) or 0)
    out_tokens = int(usage.get('completion_tokens', 0) or 0)
    # Perplexity charges per search separately; the API doesn't always report
    # search count in usage, so assume 1 per call (matches the tier docs).
    cost = _estimate_cost(model, in_tokens, out_tokens, searches=1)

    expires = (now + timedelta(days=cache_ttl_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with config.get_cache_db() as db:
        db.execute(
            'INSERT OR REPLACE INTO perplexity_cache '
            '(query_hash, query, model, answer, citations, cost_usd, created_at, expires_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (key, query, model, answer, json.dumps(citations), cost,
             config.now_iso(), expires))
        db.commit()

    _record_spend(cost, reason or 'perplexity search')

    return {
        'answer':    answer,
        'citations': citations,
        'cost_usd':  cost,
        'cached':    False,
        'model':     model,
    }


def search_json(query, model=None, system=None, **kw):
    """Search with a JSON-only prompt and best-effort JSON parse.

    Perplexity's ``sonar`` returns free text; asking it for JSON and parsing
    what you get is the pragmatic way to get structured data. Falls back to
    ``{'raw': answer}`` when the model wrapped its JSON in a code fence or
    added prose.
    """
    system = ((system or '') + '\n\n'
              'Return ONLY a valid JSON object or array. No prose, no '
              'commentary, no code fences. If unknown, use the string '
              '"unknown" — do not guess.').strip()
    result = search(query, model=model, system=system, **kw)
    data = _try_parse_json(result['answer'])
    result['data'] = data
    return result


def _try_parse_json(text):
    s = (text or '').strip()
    if not s:
        return None
    if s.startswith('```'):
        # Strip a code fence like ```json ... ```
        s = s.strip('`')
        if s.lower().startswith('json'):
            s = s[4:]
        s = s.strip()
    # Some models still add a sentence before the JSON — grab the first { or [.
    for opener, closer in (('{', '}'), ('[', ']')):
        i = s.find(opener)
        if i >= 0:
            j = s.rfind(closer)
            if j > i:
                try:
                    return json.loads(s[i:j + 1])
                except ValueError:
                    continue
    try:
        return json.loads(s)
    except ValueError:
        return {'raw': s}
