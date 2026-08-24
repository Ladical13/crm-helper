"""This app must stay a folder you can pick up and move.

It began life inside a repo full of other apps, borrowing their session, their
user store and their SQLite tuning. It does not any more — and the way that
stays true is a test, because an `from portal import ...` added for one small
convenience is invisible until the day somebody tries to deploy this on its own
and it will not even import.
"""
import ast
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BASE)


def _python_files():
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git')]
        for f in files:
            if f.endswith('.py'):
                yield os.path.join(root, f)


def _imported_names(path):
    with open(path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # a relative import stays inside
                continue
            if node.module:
                yield node.module.split('.')[0]


# Everything this app is allowed to import: the standard library, its two
# dependencies, its own modules, and pytest in the tests.
OWN_MODULES = {'app', 'auth', 'conftest'}
THIRD_PARTY = {'flask', 'werkzeug', 'gunicorn', 'pytest'}


def test_nothing_is_imported_from_outside_this_folder():
    sibling_packages = {
        d for d in os.listdir(REPO_ROOT)
        if os.path.isdir(os.path.join(REPO_ROOT, d)) and not d.startswith('.')
    } - {os.path.basename(BASE)}
    for path in _python_files():
        for name in _imported_names(path):
            assert name not in sibling_packages, (
                f'{os.path.relpath(path, BASE)} imports `{name}` from the rest '
                'of the repo. This app deploys on its own — an import from a '
                'sibling folder means it cannot even start there.')
            assert (name in OWN_MODULES or name in THIRD_PARTY
                    or name in sys.stdlib_module_names), (
                f'{os.path.relpath(path, BASE)} imports `{name}`, which is '
                'neither stdlib, nor a listed dependency, nor part of this app. '
                'Add it to requirements.txt if it is genuinely needed.')


def test_it_imports_with_the_rest_of_the_repo_off_the_path():
    """The real check, and the one the folder-import test cannot fake: run
    Python from inside this folder with an empty PYTHONPATH, so the repo root
    is nowhere on sys.path, and import the app. This is exactly the situation
    on a host that deploys `workout/` as its root directory."""
    proc = subprocess.run(
        [sys.executable, '-c',
         'import sys, app; '
         'assert not any(p.rstrip("/").endswith("crm-helper") for p in sys.path), sys.path; '
         'print(app.app.name)'],
        cwd=BASE, capture_output=True, text=True,
        env={**os.environ, 'PYTHONPATH': ''},
    )
    assert proc.returncode == 0, (
        'the app does not import on its own:\n' + proc.stdout + proc.stderr)
    assert 'app' in proc.stdout


def test_the_dependencies_are_declared():
    with open(os.path.join(BASE, 'requirements.txt'), encoding='utf-8') as fh:
        declared = fh.read().lower()
    for dep in ('flask', 'gunicorn'):
        assert dep in declared, f'{dep} is used but not in requirements.txt'


def test_there_is_something_to_deploy_with():
    """A Procfile and a README, because "its own app" includes being runnable
    by somebody who has not read app.py."""
    procfile = os.path.join(BASE, 'Procfile')
    assert os.path.exists(procfile)
    with open(procfile, encoding='utf-8') as fh:
        assert 'gunicorn' in fh.read()
    readme = os.path.join(BASE, 'README.md')
    assert os.path.exists(readme)
    with open(readme, encoding='utf-8') as fh:
        text = fh.read()
    # The three variables that make the difference between working and losing
    # everything must be written down.
    for var in ('WORKOUT_PASSWORD', 'WORKOUT_DATA_DIR', 'WORKOUT_SESSION_SECRET'):
        assert var in text, f'{var} is not documented in the README'
