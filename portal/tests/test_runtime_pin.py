"""The Python version production runs on — `.python-version`.

Nothing in this repo used to pin it. Railway's builder picked the interpreter,
CI picked its own in `.github/workflows/tests.yml`, and the two agreeing was a
coincidence nobody was checking. That is a bad way to hold a runtime, because
the failure is not a red test: the repo is 3.12-only and a 3.11 builder cannot
PARSE `estimator/app.py`, so the site would fail at import with all seven
suites green. A deploy that ships a syntactically invalid module is not
something the suite can catch after the fact — the pin is the only guard.

So three things are asserted here, and each is a different way to lose it:

* the pin EXISTS and is readable, or Railway is choosing again;
* it MATCHES CI, or production runs an interpreter nothing tested;
* it is at least the floor the source actually needs, or both could agree on a
  version that cannot run the code.

One caveat this file cannot test. `NIXPACKS_PYTHON_VERSION` set as a Railway
variable OUTRANKS `.python-version` (nixpacks reads the env var first), so a
stale variable makes this whole pin inert while every test here still passes.
If production disagrees with this file, look there first.

Deliberately NOT asserted: the version of the interpreter running this test.
Contributors run other versions on their own laptops, and a suite that goes red
on a developer's machine for a reason production does not care about is how
people learn to ignore the suite.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# The floor the SOURCE needs, independent of what anyone pinned. PEP 701 landed
# in 3.12 and `estimator/app.py` relies on it — backslash escapes inside
# f-string expressions, which earlier versions reject at parse time. Raise this
# only alongside a real reason, and never lower it to make a build go green.
MIN_SUPPORTED = (3, 12)


def _version_tuple(text, source):
    """(major, minor) from a pin, tolerating the forms nixpacks accepts."""
    m = re.search(r'(\d+)\.(\d+)', text)
    assert m, f'No version found in {source}: {text!r}'
    return int(m.group(1)), int(m.group(2))


def _pinned():
    path = REPO / '.python-version'
    assert path.is_file(), (
        'No .python-version at the repo root. Without it Railway\'s builder '
        'chooses the interpreter for production, and the repo is 3.12-only — '
        'an older builder cannot parse estimator/app.py, so the site fails at '
        'import while every test here stays green.')
    return _version_tuple(path.read_text(), '.python-version')


def _ci():
    path = REPO / '.github' / 'workflows' / 'tests.yml'
    assert path.is_file(), 'The CI workflow has moved; update this test.'
    m = re.search(r'python-version:\s*[\'"]?([0-9.]+)', path.read_text())
    assert m, 'No python-version in the CI workflow; update this test.'
    return _version_tuple(m.group(1), 'the CI workflow')


def test_the_python_version_is_pinned():
    """A pin exists at all, which is what stops Railway choosing."""
    assert _pinned()


def test_production_runs_what_ci_tested():
    """`.python-version` and the CI workflow name the same interpreter.

    These are two files nobody reads together, and they drift silently: the
    green tick on a pull request would go on meaning 'passed on 3.12' long
    after production had moved somewhere else.
    """
    pinned, ci = _pinned(), _ci()
    assert pinned == ci, (
        f'.python-version pins {pinned[0]}.{pinned[1]} but CI runs '
        f'{ci[0]}.{ci[1]}. Whichever is wrong, production and the test suite '
        f'are no longer running the same interpreter — change both together.')


def test_the_pin_can_actually_run_this_code():
    """The pin is at or above the floor the source needs.

    Without this, the two files above can agree perfectly on a version that
    cannot parse `estimator/app.py`, and the matching-pins test would pass on
    the way to a broken deploy.
    """
    pinned = _pinned()
    assert pinned >= MIN_SUPPORTED, (
        f'.python-version pins {pinned[0]}.{pinned[1]}, below the '
        f'{MIN_SUPPORTED[0]}.{MIN_SUPPORTED[1]} this source needs (PEP 701 '
        f'f-strings in estimator/app.py). That build cannot parse the app.')


@pytest.mark.parametrize('rel', ['estimator/app.py', 'salescrm/app.py',
                                 'canvasser/app.py', 'portal/app.py'])
def test_every_app_parses_under_the_pinned_version(rel):
    """Each app compiles on the interpreter this repo claims to need.

    This is the assertion that gives MIN_SUPPORTED its meaning. It runs under
    whatever interpreter is executing the suite, so on CI — which the test
    above holds to the pin — it is a direct check that the pinned version can
    parse the code that ships.
    """
    import sys
    if sys.version_info[:2] < MIN_SUPPORTED:
        pytest.skip(f'Running {sys.version_info.major}.{sys.version_info.minor}, '
                    f'below the supported floor; CI runs the pin.')
    src = (REPO / rel).read_text(encoding='utf-8')
    compile(src, rel, 'exec')     # SyntaxError here is the failure
