"""Thin sync wrapper around the Anthropic Messages API.

Deliberately shaped like ``agents/perplexity.py``: read the key from env, cap
the monthly spend, and write every charge to the same ``spend_ledger`` table so
the Nimbus cost meter stays grounded in real numbers rather than estimates.

The differences from the Perplexity wrapper are the two that matter for a
conversation rather than a lookup:

  * **No response cache.** Perplexity answers a question about the world, so
    the same query has the same answer for 30 days. This answers a question
    about *our* pipeline at *this* moment; a cached "you have 14 open leads"
    would be worse than no answer at all. Prompt caching (below) is a
    different thing — it discounts re-sending the same *prompt prefix*, and
    never reuses a response.
  * **A separate cap.** ``supervisor_monthly_cap_usd``, checked against this
    source only. See the note in ``config.DEFAULT_SETTINGS``.
"""
import json
import os
from datetime import datetime

try:
    import anthropic
except ImportError:                            # pragma: no cover - optional dep
    anthropic = None

from .. import config

SOURCE = 'anthropic'

# USD per 1M tokens. Cache writes bill at 1.25x input and cache reads at 0.1x,
# which is why the two are listed rather than derived at the call site — a
# hard-coded 0.1 in the cost maths is the kind of thing that silently stops
# matching the price list. Verify at platform.claude.com/docs/en/pricing before
# trusting the cost meter to the cent.
_PRICE = {
    'claude-opus-5':    {'in':  5.0, 'out': 25.0},
    'claude-sonnet-5':  {'in':  3.0, 'out': 15.0},
    'claude-haiku-4-5': {'in':  1.0, 'out':  5.0},
}
_DEFAULT_PRICE = _PRICE['claude-opus-5']

_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER  = 0.10


class SupervisorError(RuntimeError):
    """Any non-retryable failure — bad key, missing dependency, bad request."""


class SpendCapReached(RuntimeError):
    """The supervisor's monthly cap is exhausted. Nothing further runs until
    the cap is raised in Nimbus Settings or the month rolls over."""


def _month_start_iso():
    now = datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def month_spend_usd():
    """Sum of every logged supervisor charge this UTC month."""
    with config.get_cache_db() as db:
        row = db.execute(
            'SELECT COALESCE(SUM(cost_usd), 0) AS s FROM spend_ledger '
            'WHERE source = ? AND occurred_at >= ?',
            (SOURCE, _month_start_iso())).fetchone()
    return float(row['s'] or 0)


def _record_spend(cost, reason):
    with config.get_cache_db() as db:
        db.execute('INSERT INTO spend_ledger (occurred_at, source, reason, cost_usd) '
                   'VALUES (?, ?, ?, ?)',
                   (config.now_iso(), SOURCE, reason, cost))
        db.commit()


def estimate_cost(model, usage):
    """Price one response from its usage block.

    ``usage`` is the object the SDK returns; the four token buckets are billed
    at four different rates and a response that ignores the cache fields will
    over-report cost by roughly the size of the system prompt on every turn.
    """
    p = _PRICE.get(model, _DEFAULT_PRICE)
    plain  = int(getattr(usage, 'input_tokens', 0) or 0)
    write  = int(getattr(usage, 'cache_creation_input_tokens', 0) or 0)
    read   = int(getattr(usage, 'cache_read_input_tokens', 0) or 0)
    out    = int(getattr(usage, 'output_tokens', 0) or 0)
    return (plain * p['in']
            + write * p['in'] * _CACHE_WRITE_MULTIPLIER
            + read  * p['in'] * _CACHE_READ_MULTIPLIER
            + out   * p['out']) / 1_000_000


def key_is_set():
    return bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())


def _client():
    if anthropic is None:
        raise SupervisorError(
            'The `anthropic` package is not installed. '
            'Run: pip install -r requirements.txt')
    if not key_is_set():
        raise SupervisorError(
            'ANTHROPIC_API_KEY is not set — the supervisor cannot run. '
            'Set it in Railway (and in your local shell for dev).')
    return anthropic.Anthropic()


def check_cap():
    """Raise if the monthly cap is already exhausted. Called before a turn
    starts, so a capped-out supervisor fails with a sentence rather than
    halfway through a tool loop."""
    settings = config.load_settings()
    cap = float(settings.get('supervisor_monthly_cap_usd', 50.0) or 0)
    spent = month_spend_usd()
    if cap and spent >= cap:
        raise SpendCapReached(
            f'The supervisor\'s monthly cap of ${cap:.2f} is used up '
            f'(spent ${spent:.2f}). Raise it in Nimbus Settings, or wait for '
            f'the 1st of next month.')


def call(messages, system, tools=None, max_tokens=8000, reason=''):
    """One Messages API round trip. Returns ``(response, cost_usd)``.

    Records the charge before returning, so a caller that crashes while
    handling the response has still paid for it in the ledger — under-reporting
    spend is the failure mode that lets a cap be silently overrun.
    """
    settings = config.load_settings()
    model = settings.get('anthropic_model') or 'claude-opus-5'
    effort = settings.get('supervisor_effort') or 'medium'

    check_cap()

    kwargs = {
        'model': model,
        'max_tokens': int(max_tokens),
        'system': system,
        'messages': messages,
        # Adaptive thinking is on by default on this model; naming it keeps the
        # intent visible next to `effort`, which is the dial that actually
        # trades a chat turn's latency against its depth.
        'thinking': {'type': 'adaptive'},
        'output_config': {'effort': effort},
    }
    if tools:
        kwargs['tools'] = tools

    try:
        response = _client().messages.create(**kwargs)
    except anthropic.APIStatusError as e:                     # noqa: BLE001
        raise SupervisorError(f'Anthropic HTTP {e.status_code}: {e.message}') from e
    except anthropic.APIConnectionError as e:                 # noqa: BLE001
        raise SupervisorError(f'Could not reach Anthropic: {e}') from e

    cost = estimate_cost(model, response.usage)
    _record_spend(cost, reason or 'supervisor turn')
    return response, cost


def blocks_to_dicts(content):
    """Response content blocks → the plain dicts the API accepts back.

    Thinking blocks ride along unchanged: the API rejects an edited one, and
    dropping them mid-conversation breaks the turn. ``model_dump`` is what the
    SDK gives us for a faithful round trip.
    """
    out = []
    for block in content:
        if hasattr(block, 'model_dump'):
            out.append(block.model_dump(exclude_none=True))
        else:                                    # pragma: no cover - defensive
            out.append(json.loads(json.dumps(block, default=str)))
    return out
