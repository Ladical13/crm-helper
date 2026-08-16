"""The supervisor's guard rails, in tests rather than in prose.

The load-bearing ones are the first two: the supervisor must not be able to
approve anything, and it must not be able to spend past its cap. Everything
else in the design assumes both.
"""
import json

import pytest

from agents import config
from agents.supervisor import chat, client as sup_client, tools as sup_tools


# ── Powers ───────────────────────────────────────────────────────────────────

def test_supervisor_cannot_approve():
    """No tool may reach an approve / reject / publish route.

    This is the promise the whole design rests on: a person reads every piece
    of copy before it can reach a customer. If a future tool is added that
    POSTs to a review endpoint, this fails.
    """
    for method, prefix in sup_tools.FORBIDDEN_ROUTES:
        path = prefix + '7'
        with pytest.raises(sup_tools.Denied):
            sup_tools._check_allowed(method, path)


def test_generating_a_brief_is_still_allowed():
    """The one carve-out under the recommendations path. Generating a brief is
    not a review action — the endpoint itself refuses unless a human already
    approved the recommendation."""
    sup_tools._check_allowed('POST', '/nimbus/api/seo/recommendations/7/brief')


def test_no_tool_name_promises_approval():
    """A tool called `approve_draft` would be a lie even if it 403'd. Catch the
    naming before anyone wires it up."""
    banned = ('approve', 'reject', 'publish', 'post_to', 'send')
    for tool in sup_tools.TOOLS:
        assert not any(word in tool['name'] for word in banned), tool['name']


def test_every_tool_has_a_handler_and_a_description():
    assert {t['name'] for t in sup_tools.TOOLS} == set(sup_tools._HANDLERS)
    for tool in sup_tools.TOOLS:
        # Prescriptive descriptions are what actually drive correct tool
        # selection; a one-liner is the usual cause of a tool never firing.
        assert len(tool['description']) > 80, tool['name']
        assert 'input_schema' in tool


def test_action_tools_are_declared():
    """ACTION_TOOLS drives the UI badge. A run tool missing from it renders as
    a harmless lookup, which is exactly the wrong signal."""
    for tool in sup_tools.TOOLS:
        if tool['name'].startswith('run_'):
            assert tool['name'] in sup_tools.ACTION_TOOLS


# ── Spend ────────────────────────────────────────────────────────────────────

def test_cap_blocks_a_turn_before_it_starts(monkeypatch):
    config.save_settings({'supervisor_monthly_cap_usd': 5.0})
    monkeypatch.setattr(sup_client, 'month_spend_usd', lambda: 5.01)
    with pytest.raises(sup_client.SpendCapReached):
        sup_client.check_cap()


def test_cap_of_zero_means_no_cap(monkeypatch):
    config.save_settings({'supervisor_monthly_cap_usd': 0})
    monkeypatch.setattr(sup_client, 'month_spend_usd', lambda: 999.0)
    sup_client.check_cap()          # must not raise


def test_supervisor_spend_is_separate_from_perplexity():
    """Two sources, two caps. A chatty afternoon must not silently starve next
    week's SEO run, and vice versa."""
    from agents import perplexity
    with config.get_cache_db() as db:
        db.execute('INSERT INTO spend_ledger (occurred_at, source, reason, cost_usd) '
                   'VALUES (?, ?, ?, ?)',
                   (config.now_iso(), 'perplexity', 'research', 4.0))
        db.execute('INSERT INTO spend_ledger (occurred_at, source, reason, cost_usd) '
                   'VALUES (?, ?, ?, ?)',
                   (config.now_iso(), 'anthropic', 'supervisor turn', 1.0))
        db.commit()
    assert sup_client.month_spend_usd() == pytest.approx(1.0)
    assert perplexity.month_spend_usd() == pytest.approx(4.0)


class _Usage:
    def __init__(self, **kw):
        self.input_tokens = kw.get('input_tokens', 0)
        self.output_tokens = kw.get('output_tokens', 0)
        self.cache_creation_input_tokens = kw.get('cache_creation_input_tokens', 0)
        self.cache_read_input_tokens = kw.get('cache_read_input_tokens', 0)


def test_cost_prices_cache_tokens_at_their_own_rates():
    """Cached input is billed at 1.25x to write and 0.1x to read. Counting it
    as plain input over-reports the bill by roughly a system prompt per turn,
    which would drain the cap meter against spend that never happened."""
    plain  = sup_client.estimate_cost('claude-opus-5', _Usage(input_tokens=1_000_000))
    write  = sup_client.estimate_cost('claude-opus-5',
                                      _Usage(cache_creation_input_tokens=1_000_000))
    read   = sup_client.estimate_cost('claude-opus-5',
                                      _Usage(cache_read_input_tokens=1_000_000))
    assert plain == pytest.approx(5.0)
    assert write == pytest.approx(6.25)
    assert read  == pytest.approx(0.5)


def test_cost_uses_output_rate_for_output():
    out = sup_client.estimate_cost('claude-opus-5', _Usage(output_tokens=1_000_000))
    assert out == pytest.approx(25.0)


def test_unknown_model_still_prices_rather_than_crashing():
    """A model id typed into Settings must not make the cost meter explode —
    it falls back to the Opus rate, which errs expensive."""
    cost = sup_client.estimate_cost('made-up-model', _Usage(input_tokens=1_000_000))
    assert cost == pytest.approx(5.0)


# ── Dispatch ─────────────────────────────────────────────────────────────────

def test_unknown_tool_returns_an_error_result_not_an_exception():
    """A bad tool call must come back as a readable result so the model can
    correct itself. Raising here would end the conversation."""
    payload, is_error = sup_tools.dispatch('no_such_tool', {}, {'client': None})
    assert is_error
    assert 'unknown tool' in json.loads(payload)['error']


def test_bad_arguments_return_an_error_result():
    payload, is_error = sup_tools.dispatch('get_pipeline', {'nope': 1},
                                           {'client': None})
    assert is_error


def test_oversized_results_are_truncated_with_a_visible_marker(monkeypatch):
    """A full SEO report can dwarf the rest of the turn. The marker is what
    stops the model assuming it saw the whole list."""
    monkeypatch.setitem(sup_tools._HANDLERS, 'get_pipeline',
                        lambda ctx, **kw: {'blob': 'x' * 40000})
    payload, is_error = sup_tools.dispatch('get_pipeline', {}, {'client': None})
    assert not is_error
    assert 'truncated at' in payload
    assert len(payload) < 40000


# ── Conversation storage ─────────────────────────────────────────────────────

def test_history_round_trips_content_blocks_verbatim():
    """The next turn rebuilds history from these rows. Thinking blocks and
    tool_use ids must survive byte-for-byte or the API rejects the turn."""
    tid = chat.create_thread('luke')
    blocks = [
        {'type': 'thinking', 'thinking': '', 'signature': 'abc123'},
        {'type': 'text', 'text': 'Three are waiting.'},
        {'type': 'tool_use', 'id': 'toolu_1', 'name': 'get_pipeline', 'input': {}},
    ]
    chat._append(tid, 'assistant', blocks, display='Three are waiting.',
                 tools_used=['get_pipeline'])
    with config.get_cache_db() as db:
        history = chat._history(db, tid)
    assert history == [{'role': 'assistant', 'content': blocks}]


def test_tool_results_are_stored_but_not_shown():
    """Tool-result rows are role=user at the API level. The UI keys off an
    empty `display` to keep them out of the chat log — otherwise every lookup
    would render as the user shouting JSON."""
    tid = chat.create_thread('luke')
    chat._append(tid, 'user', [{'type': 'text', 'text': 'hello'}], display='hello')
    chat._append(tid, 'user', [{'type': 'tool_result', 'tool_use_id': 't1',
                                'content': '{}'}], display='')
    thread = chat.get_thread(tid)
    shown = [m for m in thread['messages'] if m['role'] == 'user' and m['display']]
    assert len(shown) == 1
    assert shown[0]['display'] == 'hello'


def test_first_message_names_the_thread():
    tid = chat.create_thread('luke')
    chat._set_title_if_blank(tid, 'What needs my attention today?')
    chat._set_title_if_blank(tid, 'a later message that must not rename it')
    assert chat.get_thread(tid)['title'] == 'What needs my attention today?'


def test_stale_running_threads_are_reaped():
    """A turn killed by a deploy would otherwise read 'running' forever and
    the UI would poll it until someone gave up."""
    tid = chat.create_thread('luke')
    chat._set_status(tid, 'running')
    with config.get_cache_db() as db:
        db.execute("UPDATE supervisor_threads SET updated_at = ? WHERE id = ?",
                   ('2020-01-01T00:00:00Z', tid))
        db.commit()
    chat.reap_stale_threads()
    assert chat.get_thread(tid)['status'] == 'error'


def test_a_fresh_running_thread_is_left_alone():
    tid = chat.create_thread('luke')
    chat._set_status(tid, 'running')
    chat.reap_stale_threads()
    assert chat.get_thread(tid)['status'] == 'running'


# ── Prompt ───────────────────────────────────────────────────────────────────

def test_cached_system_block_holds_no_date():
    """The date lives in the SECOND block, after the cache breakpoint. Putting
    it in the cached block would invalidate the prefix every midnight and
    quietly double the cost of every request."""
    blocks = chat._system_blocks()
    assert blocks[0]['cache_control'] == {'type': 'ephemeral'}
    assert 'Today' not in blocks[0]['text']
    assert 'Today' in blocks[1]['text']
    assert 'cache_control' not in blocks[1]


def test_system_prompt_states_what_we_cannot_measure():
    """No Search Console, no Analytics. The supervisor inventing a traffic
    number is the single most damaging thing it could do, so the constraint
    has to be in the prompt, not just in the docs."""
    for phrase in ('cannot', 'rankings', 'Search Console'):
        assert phrase in chat.SYSTEM


def test_system_prompt_says_it_cannot_approve():
    assert 'cannot approve' in chat.SYSTEM


def test_cache_breakpoint_lands_on_the_last_block():
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'a'},
                                             {'type': 'text', 'text': 'b'}]}]
    chat._mark_cache_breakpoint(messages)
    assert 'cache_control' not in messages[0]['content'][0]
    assert messages[0]['content'][1]['cache_control'] == {'type': 'ephemeral'}


def test_cache_breakpoint_ignores_string_content():
    """cache_control only exists on block dicts; a string content field must
    pass through untouched rather than raise."""
    messages = [{'role': 'user', 'content': 'plain string'}]
    chat._mark_cache_breakpoint(messages)
    assert messages[0]['content'] == 'plain string'
