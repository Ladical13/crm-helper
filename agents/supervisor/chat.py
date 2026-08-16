"""The supervisor conversation: system prompt, tool loop, persistence.

One turn is: append the user's message, then loop
``call → run any tools it asked for → call again`` until the model stops
asking for tools. Every API message is written to ``supervisor_messages`` as
it happens, verbatim, so a crash mid-loop leaves a readable transcript and the
next turn rebuilds exact history with a SELECT.

This is a hand-written loop rather than the SDK's tool runner on purpose: the
runner is a beta surface, this ships to Railway in a pinned requirements file,
and the loop is thirty lines. Revisit if the runner goes GA.
"""
import json
import threading
from datetime import datetime

from .. import config
from . import client as sup_client
from . import tools as sup_tools

# Ceiling on tool round trips in one turn. A supervisor that needs more than
# this is looping, not working; stopping with a partial answer beats burning
# the monthly cap on a question nobody can see going wrong.
MAX_ITERATIONS = 12

# Frozen. Every byte of this is the cached prompt prefix — see the note on
# _system_blocks() below before adding anything that varies.
SYSTEM = """\
You are the marketing supervisor for Project One Roofing's Colorado franchise.
You report to Luke, who runs the marketing side of the business. You are the
person he talks to instead of reading six dashboards.

# The team you supervise

Nimbus is a set of narrow bots. You are the layer that reads across all of
them and turns what they produce into an answer.

- **Local SEO Strategist** — crawls our own site plus public research, and
  produces a weekly report and a ranked queue of recommendations. Each
  recommendation carries its evidence and needs a human to approve it.
- **Content Brief bot** — turns an approved recommendation into a spec for a
  page somebody still has to write.
- **Content listener** — captures trending topics and customer questions.
- **Social + Business Profile post bot** — writes post drafts. Drafts only.
  Nothing in Nimbus has ever published anything.
- **B2B prospector** — finds partner leads (realtors, insurance agents, HOAs,
  property managers) from free Colorado open data and pushes them into the
  sales CRM.
- **Weekly scheduler** — runs the above on a cadence.

# What you can and cannot do

You can read everything and you can start work: kick off an SEO pass, a
listening pass, a social drafting run, a prospecting run, or generate a brief
from a recommendation a human already approved.

You **cannot approve anything**, and you should not pretend otherwise. You
cannot approve or reject an SEO recommendation, cannot approve, reject or
publish a draft, and cannot change spend caps or rep territories. Those are
Luke's clicks. This is deliberate: no copy reaches a customer without a person
having read it. When something needs approving, say exactly which screen to go
to and what to look for.

Starting a run returns as soon as it has *started*, not when it finishes. An
SEO pass takes minutes. Say that it is running, and check back with the run
history tools rather than describing results you have not seen.

Before you start anything that costs money or writes real records — a live
(non-dry-run) prospecting run especially — say what it will do and get a yes.

# What we can and cannot know — this matters most

Google Search Console and Google Analytics are owned at the franchise level
and we hold no access. So:

- We **cannot** know our search rankings, our search volume, our website
  traffic, our conversion rates, or any competitor's real performance.
- We **can** know what is on our own pages, what our sitemap contains, what
  questions the public web and our own CRM say Northern Colorado homeowners
  ask, what topics competitors publish, and everything in our own sales
  pipeline.

Never invent a number from the first list. If Luke asks about traffic or
rankings, say plainly that we cannot see it and why, then offer the closest
thing we *can* answer. If Bing Webmaster Tools is connected, that is real
query data and you may use it — check `get_connections` before claiming any
data source is or is not live.

Report opportunities with sources, never results. If you have not called a
tool for a number, do not state the number.

# Other standing facts

- **Two websites, same brand.** projectoneroofingcolorado.com is ours.
  projectoneroofing.com belongs to the Texas franchises — same brand,
  different business, not a competitor and not ours. Never treat Texas
  traffic, content or reviews as ours.
- **Social media does not move Google rankings.** It earns its place as a
  source of real customer language and a driver of branded search. Do not
  claim posting improves SEO.
- Before writing or judging any customer-facing copy, or before saying we
  offer a service, call `get_marketing_profile`. It is the version-controlled
  rule on what we may claim, including phrases we do not use.

# How to talk

Lead with the answer. First sentence is what Luke would ask for if he said
"just give me the short version"; supporting detail comes after.

Write plain English, the way you would explain it to a smart person who does
not do marketing for a living. No jargon unless you define it in the same
breath. Complete sentences, not fragments or arrow chains.

Be concrete. "Three recommendations are waiting, the top one is a Fort Collins
hail damage page" beats "there are some pending items". Use real numbers,
names and dates from tools, and say where each came from when it matters.

Keep it short by leaving things out, not by compressing the writing. If a
simple question has a simple answer, give the answer in a sentence — no
headers, no bullet walls.

When you recommend something, recommend it. Give your call and the reason,
not a survey of the options. If you are unsure, say what would settle it.
"""


def _system_blocks():
    """The system prompt, as blocks, with the cache breakpoint.

    Block 0 is frozen text and carries ``cache_control``; because tools render
    before system, that one breakpoint caches the tool schemas *and* the
    prompt together — the large stable majority of every request.

    Block 1 holds today's date and is placed AFTER the breakpoint on purpose.
    A date interpolated into block 0 would change its bytes every midnight and
    silently invalidate the cache for every request that day.
    """
    # %-d is glibc-only and raises on Windows, where this repo is developed.
    now = datetime.utcnow()
    today = f"{now.strftime('%A, %B')} {now.day}, {now.year}"
    return [
        {'type': 'text', 'text': SYSTEM,
         'cache_control': {'type': 'ephemeral'}},
        {'type': 'text', 'text': f"Today's date is {today} (UTC)."},
    ]


# ── Persistence ──────────────────────────────────────────────────────────────

def create_thread(username, title=''):
    now = config.now_iso()
    with config.get_cache_db() as db:
        cur = db.execute(
            'INSERT INTO supervisor_threads (created_at, updated_at, created_by, '
            'title, status) VALUES (?, ?, ?, ?, ?)',
            (now, now, username or '', title or '', 'idle'))
        db.commit()
        return cur.lastrowid


def list_threads(limit=25):
    with config.get_cache_db() as db:
        rows = db.execute(
            'SELECT id, created_at, updated_at, created_by, title, status, '
            'error, cost_usd FROM supervisor_threads '
            'ORDER BY updated_at DESC LIMIT ?', (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def get_thread(thread_id):
    with config.get_cache_db() as db:
        row = db.execute('SELECT * FROM supervisor_threads WHERE id = ?',
                         (thread_id,)).fetchone()
        if not row:
            return None
        msgs = db.execute(
            'SELECT id, created_at, role, display, tools_used, cost_usd '
            'FROM supervisor_messages WHERE thread_id = ? ORDER BY id',
            (thread_id,)).fetchall()
    out = dict(row)
    out['messages'] = []
    for m in msgs:
        d = dict(m)
        try:
            d['tools_used'] = json.loads(d.get('tools_used') or '[]')
        except (ValueError, TypeError):
            d['tools_used'] = []
        out['messages'].append(d)
    return out


def _history(db, thread_id):
    """Rebuild the exact API message list from stored content blocks."""
    rows = db.execute(
        'SELECT role, content FROM supervisor_messages WHERE thread_id = ? '
        'ORDER BY id', (thread_id,)).fetchall()
    messages = []
    for r in rows:
        try:
            content = json.loads(r['content'])
        except (ValueError, TypeError):          # pragma: no cover - defensive
            continue
        messages.append({'role': r['role'], 'content': content})
    return messages


def _append(thread_id, role, content, display='', tools_used=(), cost=0.0):
    with config.get_cache_db() as db:
        db.execute(
            'INSERT INTO supervisor_messages (thread_id, created_at, role, '
            'content, display, tools_used, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (thread_id, config.now_iso(), role, json.dumps(content), display,
             json.dumps(list(tools_used)), cost))
        db.execute('UPDATE supervisor_threads SET updated_at = ? WHERE id = ?',
                   (config.now_iso(), thread_id))
        db.commit()


def _set_status(thread_id, status, error='', add_cost=0.0):
    with config.get_cache_db() as db:
        db.execute(
            'UPDATE supervisor_threads SET status = ?, error = ?, '
            'updated_at = ?, cost_usd = cost_usd + ? WHERE id = ?',
            (status, error, config.now_iso(), add_cost, thread_id))
        db.commit()


def _set_title_if_blank(thread_id, text):
    title = (text or '').strip().replace('\n', ' ')[:60]
    with config.get_cache_db() as db:
        db.execute("UPDATE supervisor_threads SET title = ? "
                   "WHERE id = ? AND title = ''", (title, thread_id))
        db.commit()


def reap_stale_threads(max_age_minutes=20):
    """A turn killed by a deploy would otherwise read 'running' forever, and
    the UI would poll it until someone gave up. Same idea as
    ``seo.run.reap_stale_runs``."""
    cutoff = datetime.utcnow().timestamp() - max_age_minutes * 60
    with config.get_cache_db() as db:
        rows = db.execute("SELECT id, updated_at FROM supervisor_threads "
                          "WHERE status = 'running'").fetchall()
        for r in rows:
            try:
                ts = datetime.strptime(r['updated_at'], '%Y-%m-%dT%H:%M:%SZ').timestamp()
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                db.execute(
                    "UPDATE supervisor_threads SET status = 'error', error = ? "
                    "WHERE id = ?",
                    ('The turn stopped partway — the server most likely '
                     'restarted. Ask again.', r['id']))
        db.commit()


# ── The turn ─────────────────────────────────────────────────────────────────

def _text_of(content_blocks):
    return '\n\n'.join(b.get('text', '') for b in content_blocks
                       if b.get('type') == 'text' and b.get('text'))


def run_turn(thread_id, user_text, ctx):
    """Run one full turn to completion. Blocking — callers use a thread."""
    _set_title_if_blank(thread_id, user_text)
    _append(thread_id, 'user', [{'type': 'text', 'text': user_text}],
            display=user_text)
    _set_status(thread_id, 'running')

    turn_cost = 0.0
    try:
        sup_client.check_cap()

        for _ in range(MAX_ITERATIONS):
            with config.get_cache_db() as db:
                messages = _history(db, thread_id)
            _mark_cache_breakpoint(messages)

            response, cost = sup_client.call(
                messages=messages,
                system=_system_blocks(),
                tools=sup_tools.TOOLS,
                reason=f'supervisor thread {thread_id}',
            )
            turn_cost += cost

            blocks = sup_client.blocks_to_dicts(response.content)
            tool_calls = [b for b in blocks if b.get('type') == 'tool_use']
            _append(thread_id, 'assistant', blocks,
                    display=_text_of(blocks),
                    tools_used=[b['name'] for b in tool_calls],
                    cost=cost)

            if response.stop_reason == 'refusal':
                _set_status(thread_id, 'error',
                            'Claude declined to answer that one.', turn_cost)
                return
            if response.stop_reason == 'max_tokens':
                _append(thread_id, 'user', [{'type': 'text', 'text':
                        'Your last reply was cut off at the length limit. '
                        'Continue from where you stopped, more briefly.'}],
                        display='')
                continue
            if not tool_calls:
                _set_status(thread_id, 'idle', '', turn_cost)
                return

            # All results for a turn go back in ONE user message. Splitting
            # them across several trains the model out of parallel tool calls.
            results = []
            for call_block in tool_calls:
                payload, is_error = sup_tools.dispatch(
                    call_block['name'], call_block.get('input') or {}, ctx)
                results.append({
                    'type': 'tool_result',
                    'tool_use_id': call_block['id'],
                    'content': payload,
                    'is_error': is_error,
                })
            _append(thread_id, 'user', results, display='')

        _set_status(thread_id, 'error',
                    f'Stopped after {MAX_ITERATIONS} rounds of tool calls '
                    f'without reaching an answer.', turn_cost)
    except sup_client.SpendCapReached as e:
        _set_status(thread_id, 'error', str(e), turn_cost)
    except sup_client.SupervisorError as e:
        _set_status(thread_id, 'error', str(e), turn_cost)
    except Exception as e:                                     # noqa: BLE001
        _set_status(thread_id, 'error', f'{type(e).__name__}: {e}', turn_cost)


def _mark_cache_breakpoint(messages):
    """Cache the conversation prefix as well as the system prompt.

    Marks the last content block of the last message, so each turn reuses
    everything said before it. Strings are left alone — ``cache_control`` only
    exists on block dicts.
    """
    if not messages:
        return
    content = messages[-1].get('content')
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1] = dict(content[-1], cache_control={'type': 'ephemeral'})


def start_turn(thread_id, user_text, ctx):
    """Kick off a turn in the background and return immediately.

    Turns take tens of seconds once tools are involved, and there are only two
    gunicorn workers — holding one open for the duration would block the rest
    of the portal. The UI polls ``get_thread`` instead, which is the same
    shape as every other long job in Nimbus.
    """
    thread = threading.Thread(target=run_turn,
                              args=(thread_id, user_text, ctx), daemon=True)
    thread.start()
    return thread
