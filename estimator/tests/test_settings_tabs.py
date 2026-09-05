"""Settings is tabbed, and the role gating on it finally does something.

Eight unrelated editors — shingle colours, permit jurisdictions, ASCE fastener
densities, the contract text — used to sit in one scrolling column. Worse, each
gated section carried a `hidden` class that matched NO rule in style.css (which
says so itself: "There is no global .hidden utility in this stylesheet — every
use is scoped"), so `.field-group.hidden` styled nothing and the role gate did
nothing.

The blast radius was narrower than it looks: applyRoleGates() hides the
Settings button from reps outright, so nobody below manager could open this at
all. What it actually meant was every MANAGER seeing the admin-only contract
and proposal editors. The server always refused their writes, so it was never
a data leak — but the gate a reader would assume was working was not.
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, 'static', 'index.html')
CSS = os.path.join(ROOT, 'static', 'style.css')
APP_JS = os.path.join(ROOT, 'static', 'app.js')


@pytest.fixture(scope='module')
def html():
    return open(INDEX, encoding='utf-8').read()


@pytest.fixture(scope='module')
def css():
    return open(CSS, encoding='utf-8').read()


@pytest.fixture(scope='module')
def js():
    return open(APP_JS, encoding='utf-8').read()


# Every top-level section of the Settings modal. If one is added without a tab
# it is unreachable; if it is added without .settings-pane it renders on top of
# whichever tab is open.
PANES = [
    'settings-general', 'settings-margin', 'settings-gbb', 'settings-company',
    'settings-contract', 'settings-jurisdictions', 'settings-fastening',
]


@pytest.mark.parametrize('pane', PANES)
def test_every_section_is_a_pane(pane, html):
    m = re.search(rf'<div id="{pane}"[^>]*class="([^"]*)"', html)
    assert m, f'{pane} is missing from index.html'
    assert 'settings-pane' in m.group(1), \
        f'{pane} would render on top of whichever tab is open'


@pytest.mark.parametrize('pane', PANES)
def test_every_pane_has_a_tab(pane, js):
    assert f"'{pane}'" in js, f'{pane} has no entry in SETTINGS_TABS — unreachable'


def test_a_pane_needs_both_the_active_tab_and_the_role(css):
    """The `:not(.hidden)` half is the whole gate, not belt-and-braces."""
    assert '.settings-pane { display: none; }' in css
    assert '.settings-pane.is-active:not(.hidden)' in css


def test_the_stylesheet_still_has_no_global_hidden_utility(css):
    """The bug this fixes was a `hidden` class that styled nothing. If someone
    later adds a global utility, the scoped rules here become redundant — and
    this test is where they should find that out."""
    assert not re.search(r'^\.hidden\s*\{', css, re.M), \
        'a global .hidden now exists — revisit the scoped rules that work around it'


def test_the_gated_panes_still_carry_hidden_in_the_markup(html):
    """Role gating removes it at runtime, so shipping without it shows every
    editor to everyone the gate does not unhide — which is exactly the state
    this replaced."""
    for pane in ('settings-margin', 'settings-gbb', 'settings-company',
                 'settings-contract', 'settings-jurisdictions', 'settings-fastening'):
        m = re.search(rf'<div id="{pane}"[^>]*class="([^"]*)"', html)
        assert 'hidden' in m.group(1), f'{pane} ships visible to everyone'


def test_general_is_not_gated(html):
    """Shingle colours and default waste are the baseline for anyone who can
    open Settings at all, so there is always a tab to land on."""
    m = re.search(r'<div id="settings-general"[^>]*class="([^"]*)"', html)
    assert 'hidden' not in m.group(1)


def test_the_strip_is_built_after_the_gating_runs(js):
    """renderSettingsTabs reads which panes are unhidden, so calling it before
    the role checks would build a one-tab strip for an admin."""
    i_gate = js.index("document.getElementById('settings-contract').classList.remove('hidden')")
    i_tabs = js.index('renderSettingsTabs();\n  document.getElementById')
    assert i_gate < i_tabs, 'the tab strip is built before the role gating'


# ── Lost-reason modal ─────────────────────────────────────────────────────

def test_the_loss_picker_is_a_modal_not_a_prompt(js):
    """It shipped as a stack of browser prompt() dialogs."""
    i = js.index('async function setEstStatus(')
    body = js[i:js.index('\n}', i)]
    assert 'prompt(' not in body
    assert 'openLostModal' in body


def test_cancelling_records_nothing(js):
    """Backing out must leave the outcome alone, not file a loss with no
    reason — and the dropdown has to spring back to what it was showing."""
    i = js.index('function closeLostModal(')
    body = js[i:js.index('\n}', i)]
    assert 'renderEstStatusBar()' in body
    assert '_patchEstStatus' not in body


def test_saving_is_blocked_until_a_reason_is_picked(html, js):
    assert 'id="lost-save-btn"' in html and 'disabled>' in html
    i = js.index('function confirmLostReason(')
    assert 'if (!_lostPick) return;' in js[i:js.index('\n}', i)]


def test_the_options_are_not_hardcoded_in_the_markup(html):
    """They come from /api/lost-reasons so the picker cannot drift from the
    validator that accepts its value."""
    assert 'id="lost-reason-list"' in html
    assert 'competitor' not in html


def test_a_reason_key_reaching_an_inline_handler_is_js_escaped(js):
    """Same trap the customer-name note documents: esc() escapes for HTML but
    not for the JS string literal an onclick drops it into."""
    i = js.index('async function openLostModal(')
    body = js[i:js.index('\n}\n', i)]
    assert "jsq(k)" in body


def test_the_tab_strip_cannot_be_squeezed(css):
    """.modal-box is a flex column with a max-height. Without flex-shrink:0 the
    strip is a shrinkable child, so seven wrapped tabs get compressed instead of
    the body scrolling."""
    i = css.index('.settings-tabs {')
    assert 'flex-shrink: 0' in css[i:css.index('}', i)]


def test_the_lost_modal_is_sized_in_dvh(css):
    """.modal-box caps at 80vh only, and the Safari toolbar overlaps the bottom
    of vh — which is where the Mark Lost button is. This is the one modal a rep
    opens standing in a driveway. dvh must come SECOND to win."""
    i = css.index('.lost-modal-box {')
    block = css[i:css.index('}', i)]
    assert 'max-height: 80vh' in block and 'max-height: 80dvh' in block
    assert block.index('80vh') < block.index('80dvh'), 'dvh must come second'
