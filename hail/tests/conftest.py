import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from hail import storms  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_hail_db(tmp_path, monkeypatch):
    monkeypatch.setenv('HAIL_DATA_DIR', str(tmp_path))
    storms.reset_cache()
    yield
    storms.reset_cache()
