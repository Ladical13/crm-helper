#!/usr/bin/env python3
"""Bump the PWA cache-buster in every place it has to match.

The version appears in five spots across two files. Miss one and PWA clients
keep serving a stale bundle against a new server — the failure is silent and
only shows up as "it works on my machine".

    python bump_version.py          # current version -> next (v102 -> v103)
    python bump_version.py 120      # set an explicit version
    python bump_version.py --check  # verify all spots agree; non-zero if not

--check is what tests/test_assets.py runs.
"""
import argparse
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(BASE, 'static', 'index.html')
SW_JS = os.path.join(BASE, 'static', 'sw.js')

# (path, compiled pattern, human description). Each pattern captures the version
# in group 1 and is rewritten via the group's span, so surrounding text is kept.
PATTERNS = [
    (INDEX_HTML, re.compile(r'style\.css\?v=(\d+)'), 'index.html stylesheet link'),
    (INDEX_HTML, re.compile(r'app\.js\?v=(\d+)'), 'index.html script tag'),
    (SW_JS, re.compile(r"CACHE\s*=\s*'po-v(\d+)'"), 'sw.js CACHE name'),
    (SW_JS, re.compile(r'style\.css\?v=(\d+)'), 'sw.js SHELL stylesheet'),
    (SW_JS, re.compile(r'app\.js\?v=(\d+)'), 'sw.js SHELL script'),
]


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def find_versions():
    """[(description, version, path)] for every spot the version appears."""
    out = []
    cache = {}
    for path, pat, desc in PATTERNS:
        src = cache.setdefault(path, _read(path))
        found = pat.findall(src)
        if not found:
            raise SystemExit(f'bump_version: pattern for "{desc}" matched nothing in '
                             f'{os.path.relpath(path, BASE)} — did the markup change?')
        for v in found:
            out.append((desc, int(v), path))
    return out


def check():
    """Return (ok, version, versions). ok is False when the spots disagree."""
    versions = find_versions()
    distinct = {v for _, v, _ in versions}
    return len(distinct) == 1, (max(distinct) if distinct else None), versions


def bump(target=None):
    ok, current, versions = check()
    if not ok:
        print('Cache-buster versions disagree before bumping:', file=sys.stderr)
        for desc, v, _ in versions:
            print(f'  v{v:<5} {desc}', file=sys.stderr)
        print('Bumping anyway will bring them all into line.', file=sys.stderr)

    new = target if target is not None else current + 1
    if target is not None and target <= current:
        print(f'bump_version: refusing to set v{target}; current is v{current}. '
              'The version must move forward or clients keep the stale cache.',
              file=sys.stderr)
        return 1

    edited = {}
    for path, pat, _desc in PATTERNS:
        src = edited.get(path) or _read(path)
        # Rewrite only the captured digits so surrounding markup is untouched.
        def _sub(m):
            s, e = m.span(1)
            return m.group(0)[:s - m.start()] + str(new) + m.group(0)[e - m.start():]
        edited[path] = pat.sub(_sub, src)

    for path, content in edited.items():
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(content)

    print(f'Bumped cache-buster v{current} -> v{new} in {len(PATTERNS)} spots:')
    for _p, _pat, desc in PATTERNS:
        print(f'  v{new} {desc}')
    print('\nService worker + index.html are in sync. Commit both.')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('version', nargs='?', type=int, help='explicit version to set')
    ap.add_argument('--check', action='store_true',
                    help='verify every spot agrees; exit 1 if not')
    args = ap.parse_args()

    if args.check:
        ok, version, versions = check()
        if ok:
            print(f'Cache-buster is consistent at v{version} across {len(versions)} spots.')
            return 0
        print('Cache-buster versions DISAGREE — PWA clients will serve a stale bundle:',
              file=sys.stderr)
        for desc, v, path in versions:
            print(f'  v{v:<5} {desc}  ({os.path.relpath(path, BASE)})', file=sys.stderr)
        print('\nRun `python bump_version.py` to bring them into line.', file=sys.stderr)
        return 1

    return bump(args.version)


if __name__ == '__main__':
    sys.exit(main())
