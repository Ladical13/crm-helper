"""The workmanship warranty is stated in five places today:
    1. company_content.json (trust-blocks copy on /sign)
    2. DEFAULT_CONTRACT in static/app.js (retail T&C)
    3. DEFAULT_INSURANCE_CONTRACT in static/app.js (insurance T&C)
    4. tier_defaults.json (per-tier feature bullets)
    5. _WARRANTY_BY_TIER in app.py (structured manifest → JSON-LD + PDF)

The RETAIL/COMMERCIAL side must describe one tiered structure everywhere:
5-year on Good/Better, Lifetime on Best. Because the customer sees more than
one of these on the same document, disagreement between them is an actual
customer complaint, not a docs nit.

INSURANCE is deliberately NOT tiered. A claim sells the one scope the carrier
approved, so its T&C states a flat 5-year term with upgrades available, and
must never name a package — the old wording promised lifetime coverage "when
the Best package is selected", a condition the customer could not satisfy and
a choice the /sign page no longer offers them either (warranty_by_tier is
empty on insurance; see app.py's _build_estimate_manifest)."""
import json
import os
import re


from conftest import company_content_source

HERE   = os.path.dirname(os.path.abspath(__file__))
EST    = os.path.dirname(HERE)
APP_JS = os.path.join(EST, 'static', 'app.js')
# The real file when the machine has one, the test fixture otherwise — see
# conftest. Hardcoding the repo path made this test pass only on machines that
# had the gitignored file, which is every machine except CI's.
CC     = company_content_source()


def test_company_content_warranty_mentions_both_tiers():
    with open(CC, encoding='utf-8') as f:
        body = ((json.load(f).get('warranty') or {}).get('body') or '').lower()
    assert '5-year' in body or '5 year' in body
    assert 'lifetime' in body
    assert 'best' in body     # explicit that Lifetime is the Best-package promise


def test_default_contract_matches_tiered_structure():
    with open(APP_JS, encoding='utf-8') as f:
        js = f.read()
    # The RETAIL block must acknowledge lifetime-on-best AND 5-year — otherwise
    # the sign page and the T&C tell the customer different things.
    m = re.search(r'DEFAULT_CONTRACT\s*=\s*`([\s\S]*?)`;', js)
    assert m, 'DEFAULT_CONTRACT not found'
    block = m.group(1).lower()
    assert 'lifetime' in block, 'DEFAULT_CONTRACT does not mention Lifetime warranty'
    assert '5 year' in block or '5-year' in block, \
        'DEFAULT_CONTRACT does not mention 5-year warranty'


def test_insurance_contract_states_a_flat_term_and_names_no_package():
    """An insurance claim has no Good/Better/Best, so its T&C must state the
    workmanship term outright. Naming a package here promised coverage the
    customer had no way to elect."""
    with open(APP_JS, encoding='utf-8') as f:
        js = f.read()
    m = re.search(r'DEFAULT_INSURANCE_CONTRACT\s*=\s*`([\s\S]*?)`;', js)
    assert m, 'DEFAULT_INSURANCE_CONTRACT not found'
    block = m.group(1).lower()
    assert '5 year' in block or '5-year' in block, \
        'DEFAULT_INSURANCE_CONTRACT does not state a workmanship term'
    for tier in ('best package', 'better package', 'good package'):
        assert tier not in block, \
            f'DEFAULT_INSURANCE_CONTRACT still names the {tier}'


def test_manifest_warranty_matches_data_files(A):
    """_WARRANTY_BY_TIER in app.py drives JSON-LD + the PDF details page.
    Cannot drift from what company_content.json / T&C says."""
    w = A._WARRANTY_BY_TIER
    assert '5-year' in w['good']
    assert '5-year' in w['better']
    assert 'lifetime' in w['best'].lower()
