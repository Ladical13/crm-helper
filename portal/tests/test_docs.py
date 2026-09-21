"""CLAUDE.md must not describe code that no longer exists.

CLAUDE.md is the first thing loaded into every session's context, which makes
a stale line there more expensive than a stale comment anywhere else: a
comment misleads whoever opens that file, this misleads everyone, immediately,
before they have read a line of code.

It goes stale the same way twice over:

  * a symbol gets renamed or deleted and the doc keeps naming it. The Customer
    File modal was documented, then removed an hour later; `openCustomerFile()`
    survived only in prose, pointing at a function that was gone.
  * a hand-maintained number drifts. The per-suite test counts were wrong by
    38 (estimator) and 1 (agents) before this test existed — and a number that
    is wrong today teaches the next reader to distrust the whole file.

Neither errors. Nothing fails. The doc just quietly starts lying, which is
exactly the failure mode the rest of this repo writes tests for. This is that
test.

What it cannot catch is prose: "a ＋ Create New Estimate button that pre-fills
from the most recent estimate" was every-symbol-correct and no longer true.
The defence there is not automated — it is keeping volatile UI detail in code
comments, where it travels in the same diff as the change, and keeping this
file for invariants and traps that survive a redesign.
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DOC = os.path.join(ROOT, 'CLAUDE.md')

SOURCE_EXTS = ('.py', '.js', '.html', '.css', '.json', '.yml', '.yaml', '.cfg', '.ini')

# Data, dependencies and generated output — none of it is where a symbol named
# in the architecture notes would live, and some of it is enormous.
SKIP_DIRS = {
    '.git', '.github', '__pycache__', '.pytest_cache', 'node_modules',
    '.venv', 'venv', 'env', 'estimates', 'uploads', 'inbox',
    'reminder_locks', 'vendor', '.claude', 'scratchpad',
}

# Symbols CLAUDE.md names deliberately even though they are gone. Each needs a
# reason, so "add it to the allowlist" stays a decision rather than a reflex.
GONE_ON_PURPOSE = {
    'crm_sync': 'the canvasser note describes what REPLACED it, in past tense',
}


def _read(path):
    with open(path, encoding='utf-8', errors='ignore') as f:
        return f.read()


@pytest.fixture(scope='module')
def doc():
    return _read(DOC)


_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


@pytest.fixture(scope='module')
def source():
    """Every source file in the repo, concatenated, with quoted strings
    stripped.

    Stripping quotes is what makes this check honest. A test that asserts a
    symbol is GONE names it in a string literal — `assert 'function
    openCustomerFile' not in src` — and a plain substring search over the repo
    then finds the very name it is proving dead, so the doc keeps pointing at
    it and nothing complains. That was not hypothetical: this test failed to
    catch a deliberately reintroduced `openCustomerFile()` for exactly that
    reason. Any function CLAUDE.md names will have an unquoted definition
    somewhere if it is really alive.

    This file is excluded outright — it cites dead symbols as examples by
    design, and would vouch for every one of them.
    """
    chunks = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(SOURCE_EXTS) and os.path.join(dirpath, fn) != os.path.abspath(__file__):
                chunks.append(_read(os.path.join(dirpath, fn)))
    blob = _QUOTED.sub(' ', '\n'.join(chunks))
    assert len(blob) > 100_000, 'source scan found almost nothing — check SKIP_DIRS'
    return blob


# ── symbols ────────────────────────────────────────────────────────────

def test_every_function_the_docs_name_still_exists(doc, source):
    """`foo()` in CLAUDE.md must resolve to something in the codebase.

    This is the check that would have caught `openCustomerFile()` the moment
    the modal was deleted, instead of two commits later by eye."""
    named = {m.group(1) for m in re.finditer(r'`([A-Za-z_][A-Za-z0-9_]*)\([^`]*\)`', doc)}
    assert len(named) > 20, 'the reference-extracting regex stopped matching'

    missing = sorted(s for s in named if s not in source and s not in GONE_ON_PURPOSE)
    assert not missing, (
        'CLAUDE.md names functions that no longer exist: %s\n'
        'Rename them in the doc, or add to GONE_ON_PURPOSE with a reason if the '
        'doc is deliberately describing something that was removed.' % missing)


def test_the_allowlist_does_not_outlive_its_reason(doc):
    """An allowlisted symbol that the doc no longer mentions is dead weight —
    and the next person to hit a real failure will read a stale exemption as
    precedent."""
    for sym, reason in GONE_ON_PURPOSE.items():
        assert reason.strip(), '%s needs a reason' % sym
        assert sym in doc, (
            '%s is allowlisted as "named on purpose" but CLAUDE.md no longer '
            'mentions it — drop the entry' % sym)


# ── paths ──────────────────────────────────────────────────────────────

# The doc is written in app-scoped sections, so inside the estimator's notes
# `tests/test_parity.py` is unambiguous and fully qualifying every path would
# be noise. Resolve against the repo root and each app in turn.
APP_DIRS = ('', 'estimator', 'salescrm', 'portal', 'canvasser', 'agents', 'prospector')


def _resolves(rel):
    return any(os.path.exists(os.path.join(ROOT, app, rel)) for app in APP_DIRS)


def _env_rooted(rel):
    """`DATA_DIR/sales_goals.json` names a runtime location, not a repo file —
    the directory is an env var pointing at a Railway volume."""
    return rel.split('/', 1)[0].isupper()


def test_every_file_the_docs_point_at_exists(doc):
    """Bare filenames (`app.js`) and URL routes (`/shell.js`) are prose, not
    paths, and are deliberately not checked."""
    refs = set(re.findall(r'`([a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+\.[a-z]{2,5})`', doc))
    candidates = {r for r in refs if not r.startswith('/') and not _env_rooted(r)}
    assert len(candidates) > 10, 'the path-extracting regex stopped matching'

    missing = sorted(p for p in candidates if not _resolves(p))
    assert not missing, 'CLAUDE.md points at files that do not exist: %s' % missing


def test_every_directory_the_docs_point_at_exists(doc):
    refs = set(re.findall(r'`([a-z][a-z0-9_]*/)`', doc))
    missing = sorted(d for d in refs if not any(
        os.path.isdir(os.path.join(ROOT, app, d)) for app in APP_DIRS) and not
        # `vendor/` is named as "beside leaflet.css under static/vendor" —
        # a relative aside within a sentence, not a path from anywhere.
        any(os.path.isdir(os.path.join(dp, d.rstrip('/')))
            for dp, dn, fn in os.walk(ROOT) if '.git' not in dp))
    assert not missing, 'CLAUDE.md points at directories that do not exist: %s' % missing


# ── numbers that cannot help drifting ──────────────────────────────────

# The three files that tell a reader how to run the tests. They drift apart
# because a new suite is added in one place and described in the other two.
ONBOARDING_FILES = (
    'CLAUDE.md',
    'run_tests.py',
    os.path.join('.github', 'workflows', 'tests.yml'),
)


def test_the_docs_do_not_hardcode_suite_counts():
    """The per-suite test counts were wrong by 38 before this test existed.

    They drift on every commit that adds a test, and nobody notices, because
    nothing reads them. A number that is wrong today is worse than no number:
    it is the first thing a reader checks and the first thing that teaches
    them the file cannot be trusted. Describe what a suite guards instead.

    This used to read CLAUDE.md alone, which is how the same habit survived in
    `requirements-dev.txt` — `# 220`, `# 18`, `# 28` beside three of the seven
    suites, against real counts of 2008, 256 and 228. The lesson had been
    learned and applied to exactly one file. So it reads every file that tells
    someone how to run the tests.
    """
    offenders = []
    for rel in ONBOARDING_FILES + ('requirements-dev.txt',):
        for hit in re.findall(r'pytest[^\n]*#\s*~?\d{2,}', _read(os.path.join(ROOT, rel))):
            offenders.append('%s: %s' % (rel, hit))
    assert not offenders, (
        'a hardcoded test count will silently drift: %s\n'
        'Say what the suite guards, not how many tests it has.' % offenders)


def _declared_suite_count():
    """How many suites `run_tests.py` actually runs — read, never restated."""
    src = _read(os.path.join(ROOT, 'run_tests.py'))
    block = re.search(r'SUITES = \[(.*?)\n\]', src, re.S)
    assert block, 'run_tests.py no longer has a SUITES list this test can read'
    return len(re.findall(r'^\s*\(', block.group(1), re.M))


# 'seven suites', 'all six test suites', 'the same six commands CI runs' …
_SPELLED = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
            'eight': 8, 'nine': 9, 'ten': 10}
#
# The number, then at most three ordinary lowercase words, then the noun. That
# middle group is deliberately `[a-z]+` and nothing else: a looser pattern
# matched `[0] for s in SUITES` in run_tests.py's own code and failed on it,
# which is a test that cries wolf about the file it is meant to protect.
_COUNT_CLAIM = re.compile(
    r'\b(%s|\d+)\s+(?:[a-z]+\s+){0,3}(suites?|commands|invocations)\b'
    % '|'.join(_SPELLED))


def test_nothing_miscounts_the_test_suites():
    """Every "N suites" claim must match the number `run_tests.py` runs.

    `hail` was added as the seventh suite. CLAUDE.md said "seven" in three
    places and "six" in a fourth; `run_tests.py`'s own docstring said six while
    listing seven; the workflow said six on line 1 and seven on line 3. Nothing
    failed, because a prose number is not executable — which is the whole
    reason this file has a test at all.

    The count is DERIVED from the SUITES list, never restated here: a number
    written into this test would be one more thing to drift, and it would drift
    silently for exactly the same reason.
    """
    n = _declared_suite_count()
    wrong = []
    for rel in ONBOARDING_FILES:
        for m in _COUNT_CLAIM.finditer(_read(os.path.join(ROOT, rel))):
            word = m.group(1).lower()
            said = _SPELLED.get(word, int(word) if word.isdigit() else None)
            if said is not None and said != n:
                wrong.append('%s: "%s" (there are %d)' % (rel, m.group(0).strip(), n))
    assert not wrong, (
        'the suite count has drifted out of the files that onboard a reader: '
        '%s\nAdd the suite everywhere, or describe the set without counting '
        'it.' % wrong)
