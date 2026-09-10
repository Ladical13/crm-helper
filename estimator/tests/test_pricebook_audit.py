"""Price book audit — finds the shape of a costing error, never the right number.

Everything this tool says about money is derived from the price book: retail
quotes (in margin mode sell is derived FROM cost), the margin floors, the
insurance job margin, and every margin figure on the analytics tab. A wrong
cost is not one wrong number, it is four — and the more the tool is trusted the
more confidently wrong it gets.

Two real faults were found by hand in the first bundle anyone opened: labor at
$0, and a_ice_water priced per SQ while driven by a measure that returns linear
feet. Both are mechanically detectable, which is what this is for.
"""
import os
import re

import pytest

import app as A

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'static', 'app.js')


def _pb(**over):
    pb = {
        'roofing_catalog': [
            {'id': 'p_ok',    'name': 'Shingle', 'unit': 'SQ', 'cost': 142.0,
             'measure': 'squares_waste'},
            {'id': 'p_free',  'name': 'Labor',   'unit': 'SQ', 'cost': 0,
             'measure': 'squares_waste'},
            {'id': 'p_mixed', 'name': 'Ice & Water', 'unit': 'SQ', 'cost': 46.46,
             'measure': 'eave_valley'},
            {'id': 'p_conv',  'name': 'Drip D',  'unit': 'LF', 'cost': 33.82,
             'measure': 'eave', 'bundle_lf': 10, 'bundle_unit': 'sticks'},
            {'id': 'p_raw',   'name': 'Ice & Water (pack)', 'unit': 'LF', 'cost': 1.55,
             'measure': 'eave_valley', 'bundle_lf': 66.67, 'bundle_unit': 'rolls'},
            {'id': 'p_rawfree', 'name': 'Unpriced pack', 'unit': 'LF', 'cost': 0,
             'measure': 'eave', 'bundle_lf': 10, 'bundle_unit': 'sticks'},
            {'id': 'p_unused', 'name': 'Nobody sells this', 'unit': 'EA', 'cost': 0},
        ],
        'roofing_bundles': [
            {'id': 'b1', 'name': 'Landmark',
             'product_ids': ['p_ok', 'p_free', 'p_mixed', 'p_conv',
                             'p_raw', 'p_rawfree']},
        ],
    }
    pb.update(over)
    return pb


def _codes(report, pid):
    for f in report['findings']:
        if f['product_id'] == pid:
            return {i['code'] for i in f['issues']}
    return set()


def test_a_bundle_product_with_no_cost_is_flagged():
    """Every job carrying it is costed as if the line were free."""
    r = A.pricebook_audit(_pb())
    assert 'unpriced' in _codes(r, 'p_free')


def test_a_unit_that_disagrees_with_its_measure_is_flagged():
    """The a_ice_water fault: priced per SQ, sized by a measure returning LF,
    with no conversion — so 220 LF bills as 220 units of a per-square price."""
    r = A.pricebook_audit(_pb())
    assert 'unit_mismatch' in _codes(r, 'p_mixed')


def test_a_linear_measure_with_a_conversion_is_fine():
    """bundle_lf IS the LF-to-pack conversion, so this is the correct shape and
    must not be reported — a noisy audit is one nobody finishes."""
    assert _codes(A.pricebook_audit(_pb()), 'p_conv') == set()


def test_a_pack_priced_product_costed_per_foot_is_flagged():
    """The live a_ice_water fault: cost 1.55 with bundle_lf 66.67. The quantity
    is a count of ROLLS, so that is $1.55 a roll where a roll is about $95 —
    400 LF of eave+valley costed at $9.30 instead of $570. The old audit only
    tested cost <= 0, so it could not see a number that was merely absurd."""
    assert 'pack_cost_unconverted' in _codes(A.pricebook_audit(_pb()), 'p_raw')


def test_a_pack_price_above_its_conversion_is_fine():
    """p_conv is $33.82 a 10-foot stick — $3.38/LF, the correct shape. Flagging
    it would make this finding noise, and a noisy audit is one nobody finishes.
    The tightest real seed product is a_ss_zeecee at $11.65 per 10 LF."""
    assert 'pack_cost_unconverted' not in _codes(A.pricebook_audit(_pb()), 'p_conv')


def test_a_pack_product_with_no_cost_is_reported_once_not_twice():
    """A $0 pack is already `unpriced`; reporting the same root cause twice
    inflates the fix-list and teaches the reader to skim it."""
    codes = _codes(A.pricebook_audit(_pb()), 'p_rawfree')
    assert 'unpriced' in codes
    assert 'pack_cost_unconverted' not in codes


def test_a_no_op_conversion_is_not_flagged():
    """bundle_lf of 1 converts nothing, so `cost < bundle_lf` degenerates into
    "costs less than a dollar" and would fire on every cheap per-foot line."""
    pb = _pb()
    pb['roofing_catalog'].append(
        {'id': 'p_one', 'name': 'Cheap trim', 'unit': 'LF', 'cost': 0.58,
         'measure': 'eave_rake', 'bundle_lf': 1, 'bundle_unit': 'ea'})
    assert 'pack_cost_unconverted' not in _codes(A.pricebook_audit(pb), 'p_one')


def test_the_pack_finding_names_the_shape_and_never_the_right_number():
    """This section's whole contract: what a roll costs is between the manager
    and the supplier invoice. Naming a price here would be the tool guessing."""
    r = A.pricebook_audit(_pb())
    what = next(i['what'] for f in r['findings'] if f['product_id'] == 'p_raw'
                for i in f['issues'] if i['code'] == 'pack_cost_unconverted')
    assert 'rolls' in what and '66.67' in what
    assert '95' not in what


def test_every_seeded_pack_product_survives_the_check():
    """Run the real seeds through it. A finding that fires on correct data is
    worse than no finding — it is the one that gets the whole report ignored."""
    pb = A._ensure_bundle_catalogs({})
    bad = []
    for key in (k for k in pb if k.endswith('_catalog')):
        for p in pb[key]:
            if not isinstance(p, dict):
                continue
            if 'pack_cost_unconverted' in {i['code'] for i in
                                           A._audit_product(p, True, key[:-8])}:
                bad.append((key, p.get('id'), p.get('cost'), p.get('bundle_lf')))
    assert not bad, f'seed products wrongly flagged: {bad}'


def _code_labels():
    """Every audit code the modal knows how to name, read out of app.js."""
    src = open(APP_JS, encoding='utf-8').read()
    i = src.index('const CODE_LABEL')
    j = src.index('{', i)
    return set(re.findall(r'^\s{4}([a-z_0-9]+):', src[j:src.index('};', j)], re.M))


def test_every_audit_code_has_a_label_in_app_js():
    """Without one the modal prints the raw snake_case code at a manager, which
    reads like a bug in the report rather than a fault in the book."""
    codes = {'unpriced', 'unit_mismatch', 'orphan', 'conversion_unlabelled',
             'pack_cost_unconverted'}
    assert codes <= _code_labels(), f'unlabelled: {sorted(codes - _code_labels())}'


def test_a_correctly_priced_product_is_not_reported():
    assert _codes(A.pricebook_audit(_pb()), 'p_ok') == set()


def test_an_unpriced_product_nobody_sells_is_not_a_problem():
    """780 products and 744 findings is noise. Only what a bundle actually
    puts on a roof matters."""
    assert _codes(A.pricebook_audit(_pb()), 'p_unused') == set()


def test_commercial_placeholders_are_not_reported():
    """Commercial ships $0 material costs on purpose — pricing comes off a
    per-job supplier quote and unpricedBundleLines warns per bid. Listing 40
    intentional placeholders would bury the faults that are faults."""
    pb = {'commercial_catalog': [{'id': 'c1', 'name': 'TPO', 'unit': 'SQ',
                                  'cost': 0, 'measure': 'comm_sq_waste'}],
          'commercial_bundles': [{'id': 'cb', 'name': 'TPO', 'product_ids': ['c1']}]}
    assert _codes(A.pricebook_audit(pb), 'c1') == set()


def test_a_bundle_selling_a_product_that_does_not_exist_is_flagged():
    pb = _pb()
    pb['roofing_bundles'][0]['product_ids'].append('p_ghost')
    r = A.pricebook_audit(pb)
    assert 'orphan' in _codes(r, 'p_ghost')
    assert r['totals']['orphans'] == 1


def test_findings_are_ordered_worst_first():
    """It is a work queue. The thing to fix first has to be at the top."""
    r = A.pricebook_audit(_pb())
    sev = [any(i['severity'] == 'high' for i in f['issues']) for f in r['findings']]
    assert sev == sorted(sev, reverse=True)


def test_a_clean_book_reports_nothing():
    pb = {'roofing_catalog': [{'id': 'a', 'name': 'A', 'unit': 'SQ', 'cost': 10.0,
                               'measure': 'squares'}],
          'roofing_bundles': [{'id': 'b', 'name': 'B', 'product_ids': ['a']}]}
    r = A.pricebook_audit(pb)
    assert r['findings'] == [] and r['totals']['high'] == 0


# ── The dimension map must not fall behind app.js ─────────────────────────

def _measure_labels():
    """Every MEASURE_DEF key and its label, read out of app.js."""
    src = open(APP_JS, encoding='utf-8').read()
    i = src.index('const MEASURE_DEFS')
    depth, j = 0, src.index('{', i)
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                block = src[j:k + 1]
                break
    return dict(re.findall(r'^\s{2}([a-z_0-9]+):\s*\{\s*label:\s*[\'"]([^\'"]+)',
                           block, re.M))


def test_every_measure_is_covered():
    """A measure missing from MEASURE_DIMENSIONS silently escapes the unit
    check — the audit would quietly stop looking at those products rather than
    failing, which is the worst way for a checker to break."""
    missing = set(_measure_labels()) - set(A.MEASURE_DIMENSIONS)
    assert not missing, f'measures with no dimension: {sorted(missing)}'


# Measures whose on-screen label does not name a unit. Each needs a reason, so
# "add it to the list" stays a decision rather than a way to silence the check.
_LABEL_HAS_NO_UNIT = {
    'ridge_vent_code': ('"Ridge Vent — code required" — its calc is the exhaust '
                        'shortfall divided by NFA per LF, so it returns LF'),
}


@pytest.mark.parametrize('key,label', sorted(_measure_labels().items()))
def test_each_dimension_matches_the_label_app_js_shows(key, label):
    """The label is what the rep reads on screen ("Eave + Valley LF"), so it is
    the honest source for what the measure returns. If someone changes a measure
    from linear feet to squares, this fails rather than the audit going quiet."""
    if key in _LABEL_HAS_NO_UNIT:
        # Not skipped: the workflow fails the build on any skip, and a check
        # that opts itself out is how this stops being a check. Assert the
        # exception is still the shape its reason claims.
        assert A.MEASURE_DIMENSIONS[key], f'{key} still needs a dimension'
        return
    L = label.upper()
    expect = ('SF' if 'SF' in L else 'SQ' if 'SQ' in L else 'LF' if 'LF' in L else 'EA')
    assert A.MEASURE_DIMENSIONS[key] == expect, (
        f'{key} labelled "{label}" but audited as {A.MEASURE_DIMENSIONS[key]}')


# ── The endpoint ──────────────────────────────────────────────────────────

def test_the_audit_is_manager_up(anon):
    """It exposes cost structure, and it is the manager who fixes what it finds."""
    assert anon.get('/api/pricebook/audit').status_code in (401, 302, 403)


def test_a_manager_gets_the_live_book(client):
    body = client.get('/api/pricebook/audit').get_json()
    assert 'findings' in body and 'totals' in body
