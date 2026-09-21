"""No CLAUDE.md may describe code that no longer exists.

These files are the first thing loaded into a session's context, which makes a
stale line in one more expensive than a stale comment anywhere else: a comment
misleads whoever opens that file, this misleads everyone, immediately, before
they have read a line of code.

They go stale the same way twice over:

  * a symbol gets renamed or deleted and the doc keeps naming it. The Customer
    File modal was documented, then removed an hour later; `openCustomerFile()`
    survived only in prose, pointing at a function that was gone.
  * a hand-maintained number drifts. The per-suite test counts were wrong by
    38 (estimator) and 1 (agents) before this test existed — and a number that
    is wrong today teaches the next reader to distrust the whole file.

Neither errors. Nothing fails. The doc just quietly starts lying, which is
exactly the failure mode the rest of this repo writes tests for. This is that
test.

**It reads EVERY CLAUDE.md in the repo, not the root one.** That is the whole
point of the file it lives beside. The root file used to be 2,300 lines, of
which the estimator was more than half, and it was split into directory-scoped
files so a session working on the canvasser stops paying for the estimator's
carrier-import notes. A split that left this test pointed at the root file
would have moved ninety per cent of the content out from under its own guard
while every test stayed green — which is, precisely, the bug this repo had
already made once: `test_the_docs_do_not_hardcode_suite_counts` read CLAUDE.md
alone, so the same drifting counts sat in `requirements-dev.txt` wrong by an
order of magnitude. Scope a guard to the class, never to the instance.

What it cannot catch is prose: "a ＋ Create New Estimate button that pre-fills
from the most recent estimate" was every-symbol-correct and no longer true.
The defence there is not automated — it is keeping volatile UI detail in code
comments, where it travels in the same diff as the change, and keeping these
files for invariants and traps that survive a redesign.
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

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


def _find_docs():
    """Every CLAUDE.md in the repo, as paths relative to the root.

    Discovered, never listed. A hardcoded list is one more thing to keep in
    step, and the failure mode is silent in exactly the way this whole file
    exists to prevent: add `agents/CLAUDE.md`, forget the list, and it is
    unguarded with nothing to say so.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if 'CLAUDE.md' in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, 'CLAUDE.md'), ROOT))
    assert 'CLAUDE.md' in found, 'the root CLAUDE.md went missing'
    assert len(found) > 1, (
        'only the root CLAUDE.md was found. The per-app files are where most '
        'of the content lives — if they are gone, so is most of this coverage.')
    return sorted(found)


DOCS = _find_docs()


@pytest.fixture(scope='module', params=DOCS, ids=lambda d: d)
def doc(request):
    """One CLAUDE.md per test run. Every test below runs against all of them."""
    return _read(os.path.join(ROOT, request.param)), request.param


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
    """`foo()` in a CLAUDE.md must resolve to something in the codebase.

    This is the check that would have caught `openCustomerFile()` the moment
    the modal was deleted, instead of two commits later by eye."""
    text, rel = doc
    named = {m.group(1) for m in re.finditer(r'`([A-Za-z_][A-Za-z0-9_]*)\([^`]*\)`', text)}
    assert named, '%s names no functions at all — has the regex stopped matching?' % rel

    missing = sorted(s for s in named if s not in source and s not in GONE_ON_PURPOSE)
    assert not missing, (
        '%s names functions that no longer exist: %s\n'
        'Rename them in the doc, or add to GONE_ON_PURPOSE with a reason if the '
        'doc is deliberately describing something that was removed.' % (rel, missing))


def test_the_allowlist_does_not_outlive_its_reason():
    """An allowlisted symbol no doc mentions any more is dead weight — and the
    next person to hit a real failure will read a stale exemption as precedent.

    Checked across every CLAUDE.md, not the root one: `crm_sync` is named in
    the canvasser's file, and a root-only check would have called it orphaned
    the moment that section moved.
    """
    everything = '\n'.join(_read(os.path.join(ROOT, d)) for d in DOCS)
    for sym, reason in GONE_ON_PURPOSE.items():
        assert reason.strip(), '%s needs a reason' % sym
        assert sym in everything, (
            '%s is allowlisted as "named on purpose" but no CLAUDE.md mentions '
            'it any more — drop the entry' % sym)


# ── paths ──────────────────────────────────────────────────────────────

def _resolves(rel, doc_rel):
    """A path in a doc resolves against that doc's OWN directory, then root.

    `estimator/CLAUDE.md` says `tests/test_parity.py` and means the estimator's;
    the root file spells everything out from the repo root. Trying every app
    directory for every doc — which is what this did when there was one file —
    would let the canvasser's notes point at the estimator's `tests/test_assets.py`
    and call it resolved.
    """
    base = os.path.dirname(os.path.join(ROOT, doc_rel))
    return os.path.exists(os.path.join(base, rel)) or os.path.exists(os.path.join(ROOT, rel))


def _env_rooted(rel):
    """`DATA_DIR/sales_goals.json` names a runtime location, not a repo file —
    the directory is an env var pointing at a Railway volume."""
    return rel.split('/', 1)[0].isupper()


def test_every_file_the_docs_point_at_exists(doc):
    """Bare filenames (`app.js`) and URL routes (`/shell.js`) are prose, not
    paths, and are deliberately not checked."""
    text, rel = doc
    refs = set(re.findall(r'`([a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+\.[a-z]{2,5})`', text))
    candidates = {r for r in refs if not r.startswith('/') and not _env_rooted(r)}

    missing = sorted(p for p in candidates if not _resolves(p, rel))
    assert not missing, '%s points at files that do not exist: %s' % (rel, missing)


def test_every_directory_the_docs_point_at_exists(doc):
    text, rel = doc
    base = os.path.dirname(os.path.join(ROOT, rel))
    refs = set(re.findall(r'`([a-z][a-z0-9_]*/)`', text))
    missing = sorted(d for d in refs if not (
        os.path.isdir(os.path.join(base, d)) or os.path.isdir(os.path.join(ROOT, d)) or
        # `vendor/` is named as "beside leaflet.css under static/vendor" —
        # a relative aside within a sentence, not a path from anywhere.
        any(os.path.isdir(os.path.join(dp, d.rstrip('/')))
            for dp, dn, fn in os.walk(base) if '.git' not in dp)))
    assert not missing, '%s points at directories that do not exist: %s' % (rel, missing)


# ── the guards the docs claim to have ──────────────────────────────────

_PINNED = re.compile(r'(?:Pinned|Guarded) by\s+`([^`]+\.py)`')


def test_every_claim_that_says_it_is_pinned_names_a_real_test_file(doc):
    """"Pinned by `x`" is the strongest sentence in these files.

    It is what tells a reader which claims are load-bearing enough that
    something checks them, and therefore which ones they can act on without
    re-deriving. A pin pointing at a file that has moved is worse than no pin:
    it buys the claim credibility it has not got. The path check above proves
    the file exists; this proves it is a test.
    """
    text, rel = doc
    base = os.path.dirname(os.path.join(ROOT, rel))
    bad = []
    for m in _PINNED.finditer(text):
        ref = m.group(1)
        if not _resolves(ref, rel):
            bad.append('%s (missing)' % ref)
        elif 'test' not in os.path.basename(ref):
            bad.append('%s (not a test file)' % ref)
    assert not bad, (
        '%s says these guard a claim, but they are not tests that could: %s'
        % (rel, bad))


def test_every_named_test_function_still_exists(doc):
    """`test_leaflet_is_not_loaded_from_a_cdn` in prose must be a real test.

    Ten claims are backed by naming the test FUNCTION rather than its file,
    and nothing checked those: the symbol rule above only fires on `foo()`
    with parentheses, and these are written bare. So the most specific,
    most trustworthy-looking citations in these files were the least
    verified — rename one of those tests and the doc would go on quoting it
    forever.
    """
    text, rel = doc
    named = set(re.findall(r'`(test_[a-z0-9_]+)`', text))
    tests_src = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.startswith('test_') and fn.endswith('.py'):
                tests_src.append(_read(os.path.join(dirpath, fn)))
    blob = '\n'.join(tests_src)
    missing = sorted(t for t in named if ('def %s(' % t) not in blob)
    assert not missing, (
        '%s cites tests that do not exist: %s\n'
        'A citation nobody can follow is worse than none — it lends the claim '
        'credibility it has not got.' % (rel, missing))


# ── numbers that cannot help drifting ──────────────────────────────────

# The three files that tell a reader how to run the tests. They drift apart
# because a new suite is added in one place and described in the other two.
ONBOARDING_FILES = tuple(DOCS) + (
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


# ── headings are not a changelog ───────────────────────────────────────

# "Known gaps, from the 2026-09-06 review" keeps its date on purpose: that
# section is explicitly a snapshot of one reading of the app, and the date is
# what scopes the claim. Every other dated heading was a note about when it was
# written.
_DATED_HEADING_OK = ('Known gaps',)
_HEADING = re.compile(r'^#{1,6} .*$', re.M)
_DATE = re.compile(r'20\d\d-\d\d-\d\d')


def test_no_heading_carries_a_date(doc):
    """A date in a heading says WHEN a note was written, which git knows.

    These files are for invariants and traps that survive a redesign. Dating
    the headings turns them into a diary that only grows, and it ages the
    content in the reader's eye for no reason — `### Insurance job margin
    (2026-09-05)` reads as stale where `### Insurance job margin` reads as
    true. A date INSIDE the prose is different and welcome: "Fixed 2026-09-20"
    dates an event, which is a fact about the world rather than about the file.
    """
    text, rel = doc
    dated = [h for h in _HEADING.findall(text)
             if _DATE.search(h) and not any(k in h for k in _DATED_HEADING_OK)]
    assert not dated, (
        '%s dates its headings: %s\n'
        'Put the date in the prose, where it dates an event, or drop it — git '
        'already knows when the section was written.' % (rel, dated))
