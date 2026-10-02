"""The customer Roof Health tab must finish loading and keep report ownership."""
from pathlib import Path
import subprocess

def test_condition_tab_async_loading():
    here = Path(__file__).parent
    result = subprocess.run(['node', str(here / 'condition_tab_runner.cjs'),
        str(here.parent / 'static/app.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
