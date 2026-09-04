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


def _media_block_with(css, query, marker):
    """The `query` media block that actually contains `marker`.

    style.css has THREE separate `@media (max-width: 767px) { ... }` blocks
    (a KPI-card one, the header one, a third further down) — `_media_block`
    always grabs the first, so `test_mobile_keeps_the_status_badge` and
    `test_mobile_keeps_the_save_indicator` were checking the KPI-card block
    the whole time and passing vacuously (no `.est-status-badge`/
    `.save-indicator` rule there to find, hidden or not) — they would not
    have caught either rule actually being hidden in the real header block.
    This scans every occurrence of `query` and returns the one that mentions
    `marker`, so a test actually exercises the block it claims to."""
    start = 0
    while True:
        i = css.find(query, start)
        assert i >= 0, f'no block found containing {marker!r}'
        # _block() re-searches `query` from the start of its input, so slice
        # css from `i` forward and let it find the block beginning right here.
        body = _block(css[i:], query)
        if marker in body:
            return body
        start = i + 1


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
    mobile = _media_block_with(_read(CSS), '@media (max-width: 767px)', '.save-indicator')
    hidden = re.search(r'\.save-indicator\s*\{[^}]*display:\s*none', mobile)
    assert not hidden, 'the save indicator is hidden on mobile again'


def test_mobile_keeps_the_status_badge():
    mobile = _media_block_with(_read(CSS), '@media (max-width: 767px)', '.est-status-badge')
    hidden = re.search(r'\.est-status-badge\s*\{[^}]*display:\s*none', mobile)
    assert not hidden, 'the signed/sent badge is hidden on mobile again'


# ── which estimate this is, for a customer with more than one ──────────

def test_label_badge_is_not_nested_in_the_hidden_estimate_number():
    """Same trap as the signed badge above, for the newer label badge that
    shows which of a customer's several estimates is on screen."""
    html = _read(INDEX)
    assert 'id="estimate-label-badge"' in html
    m = re.search(r'<div id="estimate-number"[^>]*>.*?</div>', html, re.S)
    assert m, '#estimate-number not found'
    assert 'estimate-label-badge' not in m.group(0), (
        'the label badge is nested inside #estimate-number again, which the '
        'phone layout hides')


def test_label_badge_is_rendered_outside_the_estimate_number():
    js = _read(APPJS)
    assert "getElementById('estimate-label-badge')" in js
    body = js[js.index('function renderEstNum'):]
    body = body[:body.index('\nfunction ', 10)]
    assert 'estimate-label-badge' not in body, \
        'renderEstNum must not write the label badge into #estimate-number'


def test_mobile_hides_the_label_badge():
    """Deliberately the OPPOSITE of test_mobile_keeps_the_status_badge: the
    375px header has already overflowed once from exactly this kind of
    crowding (see the comment above .header-left in the same media block), so
    a third, purely informational badge follows #estimate-number's precedent
    (hidden on mobile) rather than the signed/sent badge's (never hidden)."""
    mobile = _media_block_with(_read(CSS), '@media (max-width: 767px)', '.estimate-number')
    hidden = re.search(r'\.estimate-label-badge\s*\{[^}]*display:\s*none', mobile)
    assert hidden, 'the label badge must be hidden on mobile, like .estimate-number'


# ── the signed contract has a door ─────────────────────────────────────

def test_the_signed_contract_is_linked_from_somewhere_a_rep_looks():
    """It has always been filed in Documents, but Documents has no tab in the
    strip — so it needed a door from the Contract page and the header."""
    js = _read(APPJS)
    assert 'function signedContractAttachment' in js
    assert 'function renderSignedContractPanel' in js
    assert 'renderSignedContractPanel()' in js, 'the panel is defined but never rendered'
    assert 'estStatusBadgeClick' in _read(INDEX), 'the header badge is not wired'


# ── mobile / touch ─────────────────────────────────────────────────────

def test_ios_focus_zoom_guard_is_gated_on_the_pointer_not_the_width():
    """Mobile Safari zooms in on focus for any control under 16px and does not
    zoom back out on blur.

    The guard used to live inside `@media (max-width: 767px)`, which covers
    phones and misses the iPad entirely — and the iPad is where estimates
    actually get written, on a table full of 13px numeric inputs. Tapping any
    of them zoomed the estimate to ~115% and left it there.

    A width query cannot express "this is a finger". `pointer: coarse` can, and
    it is the gate the canvasser already uses. This test fails if the rule
    migrates back into a width-bounded block."""
    css = _read(CSS)
    block = _media_block_with(css, '@media (pointer: coarse)', 'font-size: 16px')
    for kind in ('text', 'number', 'tel', 'email'):
        assert f'input[type="{kind}"]' in block, \
            f'input[type={kind}] is not covered by the coarse-pointer zoom guard'
    assert 'textarea' in block and 'select' in block
    assert re.search(r'font-size:\s*16px\s*!important', block), (
        'the 16px needs !important — the per-control rules are the same '
        'specificity (element+class ties element+attribute) and several of '
        'them come later in the file'
    )


def test_the_tablet_number_inputs_are_sized_for_sixteen_px():
    """The coarse-pointer guard above forces 16px onto controls the tablet
    block deliberately sizes by hand. Those widths were picked when the text
    was 14px; at 16px the money columns clip, which is the failure mode where
    a rep reads $14,850 as $14,85. Keep them in step."""
    tablet = _media_block_with(
        _read(CSS),
        '@media (min-width: 768px) and (max-width: 1366px) and (pointer: coarse)',
        'li-row-total-input')
    for sel, floor in (('li-row-cost-input', 104),
                       ('li-row-total-input', 112),
                       ('other-price-input', 140)):
        m = re.search(rf'input\.{sel}\s*\{{[^}}]*width:\s*(\d+)px', tablet)
        assert m, f'{sel} lost its explicit tablet width'
        assert int(m.group(1)) >= floor, (
            f'input.{sel} is {m.group(1)}px — too narrow for 16px digits; '
            f'needs at least {floor}px'
        )


def test_text_size_adjust_is_pinned():
    """Rotating an iPhone to landscape makes Safari inflate font sizes on a
    per-block basis, which pushes the line-item numbers out of their
    fixed-width inputs and reflows the tier columns — turning the phone
    sideways to see MORE of the table showed less of it. Android Chrome does
    the same under its accessibility text scaling."""
    css = _read(CSS)
    assert re.search(r'html\s*\{[^}]*-webkit-text-size-adjust:\s*100%', css), \
        'the -webkit-text-size-adjust guard on html is gone'
    assert re.search(r'html\s*\{[^}]*[^-]text-size-adjust:\s*100%', css), \
        'the unprefixed text-size-adjust (Android/Chrome) is gone'
