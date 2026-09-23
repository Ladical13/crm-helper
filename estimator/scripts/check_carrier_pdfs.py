"""Run every real carrier PDF through the importer and say which still read.

    python estimator/scripts/check_carrier_pdfs.py             # every PDF in carrier_samples
    python estimator/scripts/check_carrier_pdfs.py claim.pdf   # or name the files

The committed tests are synthetic on purpose: a real estimate carries a
homeowner's name, address and claim number, so the real ones live in the
gitignored estimator/carrier_samples folder and are checked here instead.
Run it before AND after any change to the Xactimate or Symbility parser. A
layout that was PASS and is now FAIL is a regression no synthetic fixture was
going to catch.

PASS means the same thing the review modal's green banner means: every line's
RCV = ACV + depreciation, and the lines add up to the carrier's own total.

Exits 1 if anything does not reconcile, so it can gate a commit.
"""
import glob
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ESTIMATOR = os.path.dirname(HERE)
REPO = os.path.dirname(ESTIMATOR)
SAMPLES = os.path.join(ESTIMATOR, 'carrier_samples')


def money(v):
    return '-' if v is None else f'${v:,.2f}'


def main(paths):
    # Loading the app seeds config files into DATA_DIR; this is a read-only
    # check, so give it somewhere disposable.
    os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='carrier-check-')
    sys.path.insert(0, REPO)
    from portal.wsgi import load_app
    load_app('carrier_check_estimator', 'estimator')
    est = sys.modules['carrier_check_estimator']

    if not paths:
        # A set, because Windows globs case-insensitively and would list
        # every file twice.
        paths = sorted({*glob.glob(os.path.join(SAMPLES, '*.pdf')),
                        *glob.glob(os.path.join(SAMPLES, '*.PDF'))})
    if not paths:
        print(f'No PDFs in {SAMPLES} - drop real carrier estimates there (it is gitignored).')
        return 0

    failed = 0
    for path in paths:
        name = os.path.basename(path)
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            fmt = est._detect_carrier_format(raw)
            data = (est._parse_symbility_pdf if fmt == 'symbility'
                    else est._parse_xactimate_pdf)(raw)
            data.setdefault('format', fmt)
            rec = est._carrier_reconcile(data)
        except Exception as e:
            failed += 1
            print(f'FAIL  {name}\n      could not parse: {e}')
            continue
        if not rec['ok']:
            failed += 1
        lines = sum(len(s.get('items') or []) for s in data['sections'])
        carrier = (data.get('meta') or {}).get('carrier') or '?'
        print(f"{'PASS' if rec['ok'] else 'FAIL'}  {name}  [{fmt}] {carrier}")
        print(f"      {lines} lines | read {money(rec['parsed_rcv'])} of the carrier's "
              f"{money(rec['carrier_rcv'])} | {rec['status']}")
        header = (data.get('layout') or {}).get('header')
        if header:
            print(f'      columns: {header}')
        for h in rec['unknown_headers']:
            print(f'      unknown columns: {h}')
        for s in rec['sections_off']:
            print(f"      section {s['name']}: read {money(s['parsed_rcv'])} "
                  f"of {money(s['carrier_rcv'])}")
        if rec['lines_off']:
            print(f"      lines where RCV != ACV + depreciation: {rec['lines_off']}")

    print(f'\n{len(paths) - failed} of {len(paths)} reconcile.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
