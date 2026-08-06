"""Isolate every agents/ test in its own scratch data dir."""
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_agents_dir(monkeypatch, tmp_path):
    d = tmp_path / 'nimbus'
    d.mkdir()
    monkeypatch.setenv('AGENTS_DATA_DIR', str(d))
    monkeypatch.delenv('PERPLEXITY_API_KEY', raising=False)
    yield d
