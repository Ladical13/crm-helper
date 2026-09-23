"""Static asset / service-worker invariants for the Pipeline PWA.

The CRM's cache-buster is bumped by hand â€” unlike the estimator there is no
bump_version.py here â€” and it has to agree in five places: the CSS link and
the script tag in static/index.html, and the CACHE name plus the two versioned
SHELL entries in static/sw.js.

Nothing used to check that. It drifted: CACHE went to v13 while SHELL still
precached ?v=12, so the worker installed two files the page never requests and
the bundle the page *does* request was absent from the offline precache. This
module is the backstop.
"""
import os
import re
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, 'static')


def _read(name):
    with open(os.path.join(STATIC, name), encoding='utf-8') as fh:
        return fh.read()


def _versions():
    """(description, version) for all five spots. Raises if a spot stops
    matching, so changed markup fails loudly instead of going unchecked."""
    index, sw = _read('index.html'), _read('sw.js')
    spots = [
        ('index.html style.css link', index, r'style\.css\?v=(\d+)'),
        ('index.html app.js script',  index, r'app\.js\?v=(\d+)'),
        ('sw.js CACHE name',          sw,    r"CACHE\s*=\s*'p1pipeline-v(\d+)'"),
        ('sw.js SHELL style.css',     sw,    r'style\.css\?v=(\d+)'),
        ('sw.js SHELL app.js',        sw,    r'app\.js\?v=(\d+)'),
    ]
    out = []
    for desc, text, pat in spots:
        m = re.search(pat, text)
        assert m, f'cache-buster spot no longer matches: {desc} (/{pat}/)'
        out.append((desc, int(m.group(1))))
    return out


def test_cache_buster_agrees_in_all_five_spots():
    found = _versions()
    seen = {v for _desc, v in found}
    assert len(seen) == 1, (
        'PWA cache-buster versions disagree â€” installed clients will precache a '
        'bundle the page never asks for:\n'
        + '\n'.join(f'  v{v}  {desc}' for desc, v in found)
        + '\n\nBump all five by hand to the same number.'
    )


def test_all_five_spots_are_present():
    assert len(_versions()) == 5


def test_service_worker_revalidates_in_the_background():
    """Cache-first with no refetch froze the portal's unversioned /shell.js at
    whatever was first cached. The fetch handler must still kick off a network
    request even when there is a cache hit."""
    # Strip // comments first: the fetch handler's own comment quotes the old
    # `hit || fetch(...)` shape to explain why it changed, and matching that
    # would fail the test on the very code that fixes it.
    sw = re.sub(r'//[^\n]*', '', _read('sw.js'))
    assert not re.search(r'hit\s*\|\|\s*fetch\(', sw), (
        'sw.js is cache-first with no background revalidation: an unversioned '
        'asset (/shell.js, /shell.css) would never update.'
    )
    assert 'return hit || live;' in sw


def test_service_worker_is_syntactically_valid():
    """sw.js is served to every client; a syntax error breaks the whole PWA."""
    node = subprocess.run(['node', '--check', os.path.join(STATIC, 'sw.js')],
                          capture_output=True, text=True)
    if 'not found' in (node.stderr or '') and node.returncode not in (0, 1):
        pytest.skip('node not installed')
    assert node.returncode == 0, node.stderr


def test_app_bundle_is_syntactically_valid():
    node = subprocess.run(['node', '--check', os.path.join(STATIC, 'app.js')],
                          capture_output=True, text=True)
    assert node.returncode == 0, node.stderr


# ── Mobile / touch ──────────────────────────────────────────────────────────

def _css():
    return _read('style.css')


def test_touch_targets_are_not_gated_on_a_tablet_width():
    """The 44px touch-target rules used to sit behind
    `(min-width: 768px) and (max-width: 1366px) and (pointer: coarse)`.

    That gave the comfortable sizes to the iPad and left the PHONE — the
    device reps actually work the outreach queue on all day — with the
    desktop ones: a 24px task checkbox, a 27px script toggle, a bare-glyph
    document delete. A coarse pointer is a finger at any width; the phone
    needs this more than the tablet does, not less.

    Laptops are unaffected because they report `pointer: fine`."""
    css = _css()
    assert '@media (pointer: coarse)' in css, 'the coarse-pointer block is gone'
    banned = '(min-width: 768px) and (max-width: 1366px) and (pointer: coarse)'
    assert banned not in css, (
        'the touch-target block is width-bounded again — phones are excluded '
        'from it, which is the regression this test exists to catch'
    )


def test_the_thumb_sized_targets_a_rep_hits_all_day():
    """Each of these is tapped dozens of times a shift, and two of them are
    destructive. Sub-44px is a miss under a thumb."""
    css = _css()
    marker = '@media (pointer: coarse)'
    assert marker in css, 'the coarse-pointer block is gone'
    block = css.split(marker)[1]
    block = block[:block.index('\n}')]
    # Strip comments before matching: an explanation that happens to name a
    # selector would otherwise satisfy the check with the rule deleted.
    block = re.sub(r'/\*.*?\*/', '', block, flags=re.S)
    for sel in ('.task-check',        # completes a task in My Day
                '.doc-del',           # deletes a customer document
                '.dh-close',          # closes the lead drawer
                '.icon-btn',          # add-lead / menu, the topbar
                '.oq-tabs button',    # script vs draft on an outreach card
                '.oq-skips button'):
        assert re.search(re.escape(sel) + r'\s*[,{]', block), \
            f'{sel} lost its coarse-pointer touch target'


def test_the_drawer_and_modal_contain_their_scroll():
    """Without overscroll-behavior:contain, flicking past the end of a lead's
    timeline chains the scroll to the pipeline behind it — so closing the
    drawer landed the rep somewhere else in the board entirely. `body` already
    sets overscroll-behavior-y:none, which makes the chained scroll look like
    a bug rather than a gesture."""
    css = _css()
    for sel in ('.drawer-panel', '.modal-box'):
        rule = re.search(re.escape(sel) + r'\{[^}]*\}', css)
        assert rule, f'{sel} rule not found'
        assert 'overscroll-behavior:contain' in rule.group(0).replace(' ', ''), \
            f'{sel} does not contain its scroll — it will chain to the page behind'


def test_the_modal_measures_the_visible_viewport():
    """88vh is taller than what the rep can see while the Safari toolbar is
    up, which put the modal's confirm/cancel buttons below the fold."""
    rule = re.search(r'\.modal-box\{[^}]*\}', _css())
    assert rule and 'dvh' in rule.group(0), \
        '.modal-box sizes itself in vh, not dvh — its buttons fall off screen on iOS'


def test_the_drawer_clears_the_home_indicator():
    """The last thing in the drawer is the Convert / Delete button pair. With
    a flat 40px of padding it sat under the home indicator on a notched
    iPhone."""
    rule = re.search(r'\.drawer-panel\{[^}]*\}', _css())
    assert rule and 'safe-b' in rule.group(0), \
        '.drawer-panel does not pad for the home indicator'


def test_text_size_adjust_is_pinned():
    """Landscape on iOS inflates font sizes per-block, which breaks the kanban
    card ellipsis and overflows the KPI tiles."""
    assert re.search(r'html\{[^}]*-webkit-text-size-adjust:100%', _css())
    assert re.search(r'html\{[^}]*[^-]text-size-adjust:100%', _css())
