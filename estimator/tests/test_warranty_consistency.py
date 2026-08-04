"""The workmanship warranty is stated in FOUR places today:
    1. company_content.json (trust-blocks copy on /sign)
    2. DEFAULT_CONTRACT in static/app.js (retail T&C)
    3. DEFAULT_INSURANCE_CONTRACT in static/app.js (insurance T&C)
    4. tier_defaults.json (per-tier feature bullets)
    5. _WARRANTY_BY_TIER in app.py (structured manifest → JSON-LD + PDF)

They must all describe the same tiered structure: 5-year on Good/Better,
Lifetime on Best. Because the customer sees more than one of these on the
same document, disagreement between them is an actual customer complaint,
not a docs nit."""
import json
import os
import re


HERE   = os.path.dirname(os.path.abspath(__file__))
EST    = os.path.dirname(HERE)
APP_JS = os.path.join(EST, 'static', 'app.js')
CC     = os.path.join(EST, 'company_content.json')


def test_company_content_warranty_mentions_both_tiers():
    with open(CC, encoding='utf-8') as f:
        body = ((json.load(f).get('warranty') or {}).get('body') or '').lower()
    assert '5-year' in body or '5 year' in body
    assert 'lifetime' in body
    assert 'best' in body     # explicit that Lifetime is the Best-package promise


def test_default_contract_matches_tiered_structure():
    with open(APP_JS, encoding='utf-8') as f:
        js = f.read()
    # DEFAULT_CONTRACT is the retail T&C block, DEFAULT_INSURANCE_CONTRACT the
    # insurance one. Both must state Lifetime somewhere in their warranty clause.
    for name in ('DEFAULT_CONTRACT', 'DEFAULT_INSURANCE_CONTRACT'):
        m = re.search(rf'{name}\s*=\s*`([\s\S]*?)`;', js)
        assert m, f'{name} not found'
        block = m.group(1).lower()
        # Both blocks must acknowledge lifetime-on-best AND 5-year — otherwise
        # the sign page and the T&C tell the customer different things.
        assert 'lifetime' in block, f'{name} does not mention Lifetime warranty'
        assert '5 year' in block or '5-year' in block, \
            f'{name} does not mention 5-year warranty'


def test_manifest_warranty_matches_data_files(A):
    """_WARRANTY_BY_TIER in app.py drives JSON-LD + the PDF details page.
    Cannot drift from what company_content.json / T&C says."""
    w = A._WARRANTY_BY_TIER
    assert '5-year' in w['good']
    assert '5-year' in w['better']
    assert 'lifetime' in w['best'].lower()
