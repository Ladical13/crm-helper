"""Canvasser static-asset invariants.

This app had no test suite at all until the vendoring change, which is also
what gave it a cache-buster and a service worker worth guarding. Two things
are being held down here:

1. Leaflet stays VENDORED. It used to load from unpkg, which made a
   third-party CDN a hard dependency of a tool used in driveways on one bar of
   signal — if unpkg was slow or unreachable the rep got a blank screen. A
   stray `https://unpkg.com` creeping back in would silently undo that.

2. The cache-buster agrees across index.html and sw.js. Same five-spot trap the
   CRM fell into (CACHE bumped, SHELL left behind), and this app has no
   bump_version.py either.
"""
import os
import re
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, 'static')
VENDOR = os.path.join(STATIC, 'vendor')


def _read(*parts):
    with open(os.path.join(*parts), encoding='utf-8') as fh:
        return fh.read()


INDEX = _read(STATIC, 'index.html')
SW = _read(STATIC, 'sw.js')


# ── Vendoring ───────────────────────────────────────────────────────────────

def test_leaflet_is_not_loaded_from_a_cdn():
    for host in ('unpkg.com', 'cdn.jsdelivr.net', 'cdnjs.cloudflare.com'):
        assert host not in INDEX, (
            f'index.html loads a script or stylesheet from {host}. Leaflet is '
            'vendored under static/vendor/ precisely so the map does not depend '
            'on a CDN the rep may not be able to reach.'
        )


def test_vendored_files_are_all_present():
    """The service worker precaches these by name; a missing one makes addAll
    reject and the app silently loses its whole offline shell."""
    for name in ('leaflet.css', 'leaflet.js',
                 'MarkerCluster.css', 'leaflet.markercluster.js',
                 'images/marker-icon.png', 'images/marker-icon-2x.png',
                 'images/marker-shadow.png', 'images/layers.png',
                 'images/layers-2x.png'):
        path = os.path.join(VENDOR, *name.split('/'))
        assert os.path.exists(path), f'vendored asset missing: vendor/{name}'
        assert os.path.getsize(path) > 0, f'vendored asset is empty: vendor/{name}'


def test_vendored_leaflet_is_the_pinned_version():
    """The version is pinned in a comment nowhere — it is pinned by the file
    itself. If someone re-downloads a different release, the app.js API calls
    are the thing that breaks, so assert what we actually vendored."""
    assert 'Leaflet 1.9.4' in _read(VENDOR, 'leaflet.js')


def test_leaflet_css_image_paths_resolve_against_the_vendor_dir():
    """leaflet.css references its icons relatively (url(images/...)), so the
    images have to sit beside it under vendor/, not in static/images/."""
    for ref in re.findall(r'url\((images/[^)]+)\)', _read(VENDOR, 'leaflet.css')):
        assert os.path.exists(os.path.join(VENDOR, *ref.split('/'))), (
            f'leaflet.css references {ref}, which does not exist under vendor/'
        )


# ── Cache-buster ────────────────────────────────────────────────────────────

def _versions():
    """Every ?v=N in index.html and in the worker's SHELL list."""
    return {
        'index.html': sorted(set(re.findall(r'\?v=(\d+)', INDEX))),
        'sw.js SHELL': sorted(set(re.findall(r'\?v=(\d+)', SW))),
    }


def test_cache_buster_agrees_between_index_and_the_worker():
    found = _versions()
    everything = set(found['index.html']) | set(found['sw.js SHELL'])
    assert everything, 'no ?v= cache-buster found at all'
    assert len(everything) == 1, (
        'PWA cache-buster versions disagree — installed clients will precache a '
        f'bundle the page never asks for: {found}'
    )


def test_worker_precaches_everything_the_page_versions():
    """Any versioned asset in index.html should be in the offline shell, or the
    app loads online and breaks offline in a way nobody notices until a rep is
    standing in a driveway."""
    page = set(re.findall(r'/static/([\w./-]+)\?v=\d+', INDEX))
    shell = set(re.findall(r"/static/([\w./-]+)\?v=\d+'", SW))
    missing = page - shell
    assert not missing, f'versioned but not precached by sw.js: {sorted(missing)}'


def test_cache_name_is_bumped_with_the_asset_version():
    m_cache = re.search(r"CACHE\s*=\s*'p1canvasser-v(\d+)'", SW)
    assert m_cache, 'sw.js CACHE name not found or renamed'
    versions = set(re.findall(r'\?v=(\d+)', SW))
    assert versions == {m_cache.group(1)}, (
        f'CACHE is v{m_cache.group(1)} but SHELL precaches ?v={versions} — bump '
        'both together, or the worker installs files the page never requests.'
    )


# ── Service worker wiring ───────────────────────────────────────────────────

def test_worker_is_served_from_the_app_root_not_static():
    """A worker can only claim a scope at or below its own path. Served from
    /static/ it could never control /canvass/, and registration would fail."""
    app_py = _read(BASE, 'app.py')
    assert re.search(r"@app\.route\('/sw\.js'\)", app_py), (
        'the /sw.js route is gone; a worker under /static/ cannot claim /canvass/'
    )
    assert "register(base + '/sw.js'" in INDEX


def test_worker_passes_cross_origin_requests_straight_through():
    """Map tiles come back as opaque cross-origin responses, which browsers
    charge against the storage quota at a padded size — caching them blind can
    evict the app shell this worker exists to guarantee."""
    assert 'url.origin !== self.location.origin' in SW


def test_api_calls_are_network_first():
    """A canvasser acting on a cached pin list knocks doors a teammate already
    worked. Stale is worse than an honest failure here."""
    assert re.search(r"path\.startsWith\('/api/'\)", SW)
    handler = SW[SW.index("path.startsWith('/api/')"):]
    assert 'fetch(e.request).catch' in handler[:200]


def test_worker_revalidates_in_the_background():
    """The portal's /shell.js and /shell.css carry no version; plain cache-first
    would freeze the app-switcher bar at whatever was first cached."""
    code = re.sub(r'//[^\n]*', '', SW)
    assert not re.search(r'hit\s*\|\|\s*fetch\(', code)
    assert 'return hit || live;' in code


def test_service_worker_is_syntactically_valid():
    node = subprocess.run(['node', '--check', os.path.join(STATIC, 'sw.js')],
                          capture_output=True, text=True)
    if 'not found' in (node.stderr or '') and node.returncode not in (0, 1):
        pytest.skip('node not installed')
    assert node.returncode == 0, node.stderr


def test_app_bundle_is_syntactically_valid():
    node = subprocess.run(['node', '--check', os.path.join(STATIC, 'app.js')],
                          capture_output=True, text=True)
    assert node.returncode == 0, node.stderr


# ── Mobile ──────────────────────────────────────────────────────────────────

def test_viewport_opts_into_the_safe_area():
    """style.css derives --safe-top/--safe-bot from env(safe-area-inset-*) and
    uses them to keep the header out from under the notch. Those env() values
    resolve to 0 unless the viewport opts in with viewport-fit=cover, which is
    how the header ended up under the notch on every modern iPhone."""
    m = re.search(r'<meta name="viewport" content="([^"]+)"', INDEX)
    assert m, 'viewport meta tag not found'
    assert 'viewport-fit=cover' in m.group(1)
    assert 'env(safe-area-inset-top' in _read(STATIC, 'style.css')


def test_form_controls_clear_the_ios_focus_zoom_threshold():
    """Mobile Safari zooms in on focus for any control under 16px and does not
    zoom back out — a rep tapping the phone field at a door got stuck zoomed."""
    css = _read(STATIC, 'style.css')
    block = re.search(r'@media \(pointer: coarse\)\s*\{(.*?)\n\}', css, re.S)
    assert block, 'the pointer:coarse font-size rule is gone'
    assert 'font-size: 16px' in block.group(1)
