"""Static-asset invariants — the failures nobody sees.

Every one of these breaks silently: the page keeps loading, it just serves last
week's bundle, or loses its offline shell, or works on the laptop and nowhere
else. Same class of trap the canvasser's and the CRM's suites hold down.
"""
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, 'static')


def _read(*parts):
    with open(os.path.join(*parts), encoding='utf-8') as fh:
        return fh.read()


INDEX = _read(STATIC, 'index.html')
SW = _read(STATIC, 'sw.js')
APP = _read(BASE, 'app.py')


# ── Cache-buster ────────────────────────────────────────────────────────────

def test_the_cache_buster_agrees_everywhere():
    """index.html and sw.js must carry the same ?v=N, and CACHE must match it.

    There is no bump_version.py here (same as the canvasser), so this is bumped
    by hand — which is exactly how the CRM's drifted to CACHE v13 precaching
    ?v=12, leaving the bundle the page actually requests out of the shell.
    """
    versions = set(re.findall(r'\?v=(\d+)', INDEX)) | set(re.findall(r'\?v=(\d+)', SW))
    cache = re.search(r"const CACHE = 'p1lift-v(\d+)'", SW)
    assert cache, "sw.js must declare CACHE as 'p1lift-vN'"
    versions.add(cache.group(1))
    assert len(versions) == 1, (
        f'cache-buster disagrees across index.html and sw.js: {sorted(versions)}. '
        'Bump all of them together — index.html (style.css, app.js), sw.js CACHE '
        'and every SHELL entry.')


def test_every_versioned_asset_is_in_the_offline_shell():
    """A file the page requests but the worker does not precache is a file that
    is missing the moment the gym has no signal."""
    for asset in ('style.css', 'app.js'):
        assert asset in INDEX, f'{asset} is not loaded by index.html'
        assert re.search(r'/static/' + asset + r'\?v=\d+', SW), (
            f'{asset} is requested by the page but missing from SHELL in sw.js')


# ── Mounting ────────────────────────────────────────────────────────────────

def test_the_page_does_not_hardcode_a_mount_prefix():
    """The other three apps' index.html files hardcode /crm, /canvass and
    /estimate on their asset tags, which is why serving them at the root gives
    an unstyled page. This one uses relative hrefs so it works standalone AND
    mounted — the only reason it can be developed without the portal running."""
    for tag in re.findall(r'(?:href|src)="([^"]+)"', INDEX):
        assert not tag.startswith('/'), (
            f'{tag} is rooted. Use a relative path so the page works at / and '
            'under a mount prefix.')


def test_the_front_end_derives_its_own_base():
    app_js = _read(STATIC, 'app.js')
    assert 'location.pathname' in app_js, (
        'app.js must derive BASE from the URL, like the other three apps')
    assert 'self.registration.scope' in SW, (
        'sw.js must derive its BASE from its own scope — a hardcoded scope is '
        "what makes two workers on one origin fight")


def test_the_service_worker_is_served_from_the_app_root():
    """A worker can only claim a scope at or below its own path, so one served
    from /static/ could never control the app."""
    assert "@app.route('/sw.js')" in APP
    assert "@app.route('/static/sw.js')" not in APP


def test_nothing_is_loaded_from_a_cdn():
    """No build step and no third-party runtime dependency: the app must come
    up in a basement with no signal."""
    for host in ('unpkg.com', 'cdn.jsdelivr.net', 'cdnjs.cloudflare.com',
                 'fonts.googleapis.com'):
        assert host not in INDEX, f'index.html loads something from {host}'


# ── Worker behaviour ────────────────────────────────────────────────────────

def test_the_api_is_network_first():
    """Cache-first on /api/ would show sets that are not really stored. A log
    that claims work which never got written is worse than no log."""
    api_block = SW.split("path.startsWith('/api/')")[1].split('return;')[0]
    assert api_block.index('fetch(e.request)') < api_block.index('caches.match'), (
        'the /api/ branch must try the network before the cache')


def test_the_shell_revalidates_in_the_background():
    """Plain `hit || fetch(...)` never refetches, so an asset whose ?v= was not
    bumped is stale forever rather than one load behind — the bug the CRM's
    worker shipped with."""
    assert 'caches.open(CACHE).then((c) => c.put(e.request, copy))' in SW


# ── Safe areas ──────────────────────────────────────────────────────────────

def test_viewport_fit_cover_is_present():
    """style.css derives --safe-top/--safe-bot from env(safe-area-inset-*) and
    uses them on the header, tab bar, rest timer and sheet. Without this meta
    they resolve to 0 and the tab bar sits under the home indicator — which is
    where the thumb lands."""
    assert 'viewport-fit=cover' in INDEX
    css = _read(STATIC, 'style.css')
    assert 'env(safe-area-inset-top' in css and 'env(safe-area-inset-bottom' in css
