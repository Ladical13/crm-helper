"""Static asset / service-worker invariants.

The PWA cache-buster has to match in five places across index.html and sw.js.
Missing one ships a stale bundle to installed clients against a new server —
a silent failure that looks like "works on my machine". This test is the
backstop for that, and bump_version.py is the tool that prevents it.
"""
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import bump_version


def test_cache_buster_agrees_everywhere():
    ok, version, versions = bump_version.check()
    assert ok, (
        'PWA cache-buster versions disagree — installed clients will serve a '
        'stale bundle:\n' +
        '\n'.join(f'  v{v}  {desc}' for desc, v, _ in versions) +
        '\n\nRun: python bump_version.py'
    )
    assert version is not None


def test_every_expected_spot_is_still_found():
    """If markup changes so a pattern stops matching, bump_version would
    silently skip that spot. find_versions() raises instead — assert it finds
    exactly the five we expect."""
    versions = bump_version.find_versions()
    assert len(versions) == len(bump_version.PATTERNS) == 5


def test_check_mode_exits_zero_when_consistent():
    r = subprocess.run([sys.executable, os.path.join(BASE, 'bump_version.py'), '--check'],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_bump_refuses_to_move_version_backwards():
    """Going backwards would leave clients pinned to a newer cached version."""
    _ok, current, _ = bump_version.check()
    r = subprocess.run([sys.executable, os.path.join(BASE, 'bump_version.py'), str(current - 1)],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert 'refusing' in r.stderr.lower()


def test_service_worker_is_syntactically_valid():
    """sw.js is served to every client; a syntax error breaks the whole PWA."""
    node = subprocess.run(['node', '--check', os.path.join(BASE, 'static', 'sw.js')],
                          capture_output=True, text=True)
    if 'not found' in (node.stderr or '') and node.returncode not in (0, 1):
        pytest.skip('node not installed')
    assert node.returncode == 0, node.stderr


def test_app_bundle_is_syntactically_valid():
    node = subprocess.run(['node', '--check', os.path.join(BASE, 'static', 'app.js')],
                          capture_output=True, text=True)
    assert node.returncode == 0, node.stderr


def test_dashboard_backup_link_respects_estimator_mount():
    """The portal mounts this app at /estimate, so root-relative API links
    bypass the estimator and land on the portal instead."""
    with open(os.path.join(BASE, 'static', 'app.js'), encoding='utf-8') as fh:
        source = fh.read()

    assert 'href="${BASE}/api/backup" class="dash-backup-link"' in source
    assert 'href="/api/backup" class="dash-backup-link"' not in source
