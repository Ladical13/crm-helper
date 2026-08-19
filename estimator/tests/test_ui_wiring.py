"""Front-end wiring invariants that fail invisibly.

None of these are style nits. Each one is a thing that broke, or nearly broke,
without producing an error anywhere:

  * A feature whose ONLY caller sits inside a control that CSS hides at some
    breakpoint is simply gone at that width. That is what happened to the
    Order Sheet: `openOrderSheet()` was called from the overflow menu and
    nowhere else, and `.more-menu-btn` was `display:none` above 767px, so the
    whole feature did not exist on a laptop. Nothing errored; the button just
    was not there.
  * Status that lives INSIDE an element a breakpoint hides disappears with its
    host. The "Signed" badge used to be nested in #estimate-number, which the
    phone layout hides, so a rep on a phone could not tell a signed estimate
    from a draft.
  * The tab strip is the running order of the sales pitch. Its order carries
    meaning, so a reorder should be a deliberate edit to this list rather than
    something that drifts.
"""
import os
import re

import pytest

HERE  = os.path.dirname(os.path.abspath(__file__))
EST   = os.path.dirname(HERE)
INDEX = os.path.join(EST, 'static', 'index.html')
CSS   = os.path.join(EST, 'static', 'style.css')
APPJS = os.path.join(EST, 'static', 'app.js')


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def _block(css, selector):
    """The declaration body of the first top-level `selector { ... }` rule."""
    i = css.find(selector)
    assert i >= 0, f'no rule for {selector}'
    i = css.index('{', i)
    depth, j = 0, i
    while True:
        if css[j] == '{':
            depth += 1
        elif css[j] == '}':
            depth -= 1
            if depth == 0:
                return css[i + 1:j]
        j += 1


def _media_block(css, query):
    return _block(css, query)


# ── the tab strip is the pitch ─────────────────────────────────────────

EXPECTED_ORDER = ['client', 'cover', 'intro', 'photos', 'report', 'scope',
                  'products', 'visualizer', 'pricing', 'contract']


def test_nav_runs_in_sale_order():
    """Condition sits with Photos (the evidence), Contract is last (the close).
    Change this list only on purpose."""
    order = re.findall(r'data-page="([a-z]+)"', _read(INDEX))
    assert order == EXPECTED_ORDER


def test_nav_numbers_are_sequential():
    """A numbered strip that skips or repeats reads as broken. Customer is the
    hub and is deliberately unnumbered."""
    nums = re.findall(r'<span class="pg-num">([^<]+)</span>', _read(INDEX))
    assert nums == [str(n) for n in range(1, len(EXPECTED_ORDER))]


def test_condition_comes_before_the_contract():
    """The one ordering rule worth stating outright: never ask for the
    signature before showing the roof report that justifies it."""
    order = re.findall(r'data-page="([a-z]+)"', _read(INDEX))
    assert order.index('report') < order.index('contract')


# ── reachability ───────────────────────────────────────────────────────

def test_the_overflow_menu_is_reachable_at_every_width():
    """It holds the only caller for the Order Sheet."""
    base = _block(_read(CSS), '.more-menu-btn {')
    assert 'display: none' not in base, (
        'the overflow menu button is hidden by default again — everything '
        'reachable only from that menu is unreachable at desktop widths')


def test_order_sheet_has_a_caller_that_is_actually_rendered():
    html, js = _read(INDEX), _read(APPJS)
    assert 'function openOrderSheet' in js
    callers = html.count('openOrderSheet(') + js.count('openOrderSheet(')
    # definition + at least one invocation
    assert callers >= 2, 'openOrderSheet() has no caller — the feature is dead'
    assert 'openOrderSheet()' in html, (
        'the Order Sheet is only reachable from JS; it needs a control a rep '
        'can actually see')


# ── status that survives the phone ─────────────────────────────────────

def test_signed_badge_is_not_nested_in_the_hidden_estimate_number():
    html = _read(INDEX)
    assert 'id="est-status-badge"' in html
    # The badge element must be a SIBLING of #estimate-number, not inside it.
    m = re.search(r'<div id="estimate-number"[^>]*>.*?</div>', html, re.S)
    assert m, '#estimate-number not found'
    assert 'est-status-badge' not in m.group(0), (
        'the signed badge is nested inside #estimate-number again, which the '
        'phone layout hides')


def test_signed_badge_is_rendered_outside_the_estimate_number():
    js = _read(APPJS)
    assert "getElementById('est-status-badge')" in js
    # renderEstNum must not write the badge back into the number element.
    body = js[js.index('function renderEstNum'):]
    body = body[:body.index('\nfunction ', 10)]
    assert 'sig-badge' not in body, (
        'renderEstNum is writing the badge into #estimate-number again')


def test_mobile_keeps_the_save_indicator():
    """The only signal telling a rep their work survived — and a phone is
    where the tab is most likely to be killed mid-edit."""
    mobile = _media_block(_read(CSS), '@media (max-width: 767px)')
    hidden = re.search(r'\.save-indicator\s*\{[^}]*display:\s*none', mobile)
    assert not hidden, 'the save indicator is hidden on mobile again'


def test_mobile_keeps_the_status_badge():
    mobile = _media_block(_read(CSS), '@media (max-width: 767px)')
    hidden = re.search(r'\.est-status-badge\s*\{[^}]*display:\s*none', mobile)
    assert not hidden, 'the signed/sent badge is hidden on mobile again'


# ── the signed contract has a door ─────────────────────────────────────

def test_the_signed_contract_is_linked_from_somewhere_a_rep_looks():
    """It has always been filed in Documents, but Documents has no tab in the
    strip — so it needed a door from the Contract page and the header."""
    js = _read(APPJS)
    assert 'function signedContractAttachment' in js
    assert 'function renderSignedContractPanel' in js
    assert 'renderSignedContractPanel()' in js, 'the panel is defined but never rendered'
    assert 'estStatusBadgeClick' in _read(INDEX), 'the header badge is not wired'
