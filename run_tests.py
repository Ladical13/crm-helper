#!/usr/bin/env python
"""Run all five test suites, the same way CI does. Use this before committing.

    python run_tests.py            # everything
    python run_tests.py estimator  # just one (or several) by name

Why five separate pytest runs rather than one: each app has its own
tests/conftest.py, and pytest imports them by the bare module name `conftest`,
so collecting two apps together collides and errors out.

Exits non-zero if anything fails, so it can be wired into a git hook later.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (name, working directory, pytest args). Mirrors .github/workflows/tests.yml.
#
# `agents` was written with its own pytest.ini and a passing suite but was
# never listed here or in the workflow, so it ran only if someone invoked it by
# hand — including the test that asserts the Perplexity spend cap actually
# blocks further live calls, which is the one guarding a metered API.
SUITES = [
    ('salescrm',   ROOT / 'salescrm',  []),
    ('portal',     ROOT / 'portal',    []),
    ('estimator',  ROOT / 'estimator', []),
    ('prospector', ROOT,               ['prospector/tests']),
    ('agents',     ROOT / 'agents',    []),
]


def run(name, cwd, args):
    """Run one suite. Returns (ok, skipped, summary_line, output)."""
    # No -q here on purpose: salescrm/pytest.ini and portal/pytest.ini already
    # set `addopts = -q`, and a second -q drops pytest's final count line
    # entirely ("85 passed in 1.5s"), leaving nothing to report.
    proc = subprocess.run(
        [sys.executable, '-m', 'pytest', *args, '-rs', '--tb=short'],
        cwd=str(cwd), capture_output=True, text=True,
    )
    out = (proc.stdout or '') + (proc.stderr or '')
    # pytest's own summary is the last non-blank line with a count in it.
    summary = next((ln.strip(' =\t') for ln in reversed(out.splitlines())
                    if ln.strip() and ('passed' in ln or 'failed' in ln
                                       or 'error' in ln.lower())), '')
    return proc.returncode == 0, 'skipped' in out, summary, out


def main(argv):
    wanted = [a.lower() for a in argv[1:]]
    suites = [s for s in SUITES if not wanted or s[0] in wanted]
    if not suites:
        print(f'No suite matched {wanted}. Known: '
              f'{", ".join(s[0] for s in SUITES)}')
        return 2

    # The estimator prices the same fixtures in app.js (under node) and app.py
    # and fails if they disagree by a cent -- but those tests SKIP themselves
    # when node is missing, so without this warning the run looks green while
    # the money math goes unchecked.
    node = shutil.which('node')
    if not node and any(s[0] == 'estimator' for s in suites):
        print('WARNING: node is not installed. The estimator pricing-parity and\n'
              '         fastener tests will SKIP rather than fail. Install node\n'
              '         to actually check them.\n')

    failed, skipped, t0 = [], [], time.time()
    for name, cwd, args in suites:
        print(f'--- {name} '.ljust(60, '-'))
        ok, was_skipped, summary, out = run(name, cwd, args)
        if not ok:
            failed.append(name)
            print(out.rstrip() or '(no output)')
        else:
            print(summary or 'passed')
        if was_skipped:
            skipped.append(name)
        print()

    print('=' * 60)
    if failed:
        print(f'FAILED: {", ".join(failed)}')
    else:
        print(f'All suites passed in {time.time() - t0:.1f}s.')
    if skipped:
        # Not fatal locally (CI is the strict gate), but always worth seeing:
        # a skip means something quietly stopped being tested.
        print(f'NOTE: tests were skipped in {", ".join(skipped)} — run that '
              f'suite with -rs to see why.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
