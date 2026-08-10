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
