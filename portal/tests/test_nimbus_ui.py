import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='node required for Nimbus polling checks')
def test_polling_tracks_exact_job_and_ignores_old_responses():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('nimbus_ui_runner.js'))],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
