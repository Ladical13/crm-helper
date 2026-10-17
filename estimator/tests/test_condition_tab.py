"""The customer Roof Health tab must finish loading, and keep each estimate's
report its own.

It used to sit on "Loading…" for good: loadConditionReport() redrew only when
_docGenerator was 'condition', a state that stopped existing when the report
moved to its own customer tab. The runner drives the real function under node
against a fake fetch: the tab draws when its data lands, a failure is shown
rather than swallowed, a slow answer for the estimate the rep already left
never replaces the one on screen, and a response arriving while the rep is on
another tab does not drag them back.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node not installed — app.js cannot be run')


def test_condition_tab_async_loading():
    here = Path(__file__).parent
    result = subprocess.run(['node', str(here / 'condition_tab_runner.cjs'),
                             str(here.parent / 'static' / 'app.js')],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
