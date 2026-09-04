"""A condition report with nothing priced behind it IS the estimate.

A rep who inspects a roof and writes up the report has produced a bid — the
recommendations carry prices and they add up. Nothing else on the estimate does,
because a new estimate ships with Roofing ENABLED and empty. So the customer got
a Good/Better/Best comparison of three $0 columns and a "Project Total $0"
printed underneath a report that had just quoted thousands of dollars of repairs.

When no trade carries scope, the recommendations are the price: _is_report_only
says so, _estimate_total reads it (which is what the list, the funnel, the Den
push and the change-order math all quote), and the customer view drops the
package cards for an itemized repair table.

The client half is hand-mirrored in app.js (pcRepairLines / pcRepairTotals /
isReportOnly) with no parity harness binding the two — change both together.
"""


def _report(*recs, grade='C', **est):
    """An estimate whose Roofing section carries the given (priority, text,
    price) recommendations, with the default empty-but-enabled Roofing trade."""
    doc = {
        'estimate_type': 'retail',
        'trades': {
            'roofing':   {'enabled': True,  'line_items': [], 'mode': 'gbb'},
            'siding':    {'enabled': False, 'line_items': []},
            'insurance': {'enabled': False, 'sections': [{'id': 's1', 'name': '', 'items': []}]},
        },
        'property_condition': {
            'inspection_date': '2026-09-01',
            'audience': 'homeowner',
            'sections': {
                'roof': {
                    'enabled': True, 'grade': grade, 'findings': [],
                    'recommendations': [
                        dict(id=str(i), priority=p, description=d, cost_range=c)
                        for i, (p, d, c) in enumerate(recs)
                    ],
                },
            },
        },
    }
    doc.update(est)
    return doc


_RECS = (('immediate', 'Replace cracked pipe boot', '$1,500'),
         ('soon',      'Reseal step flashing',      '$2,000'),
         ('monitor',   'Clear gutters annually',    '$400'))


# ── The price ────────────────────────────────────────────────────────

def test_the_recommended_repairs_price_the_estimate(A):
    est = _report(*_RECS)
    assert A._is_report_only(est) is True
    assert A._estimate_total(est) == 3900


def test_an_empty_but_enabled_roofing_trade_does_not_block_it(A):
    """The bug this exists for: 'enabled' is every new estimate, not scope."""
    est = _report(*_RECS)
    assert est['trades']['roofing']['enabled'] is True
    assert A._has_priced_scope(est) is False


def test_a_priced_trade_wins_and_the_repairs_stop_pricing_anything(A):
    est = _report(*_RECS)
    est['trades']['roofing']['line_items'] = [{
        'name': 'Architectural shingles', 'quantity': 30, 'unit': 'SQ',
        'tiers': {'better': {'material_unit_cost': 100, 'labor_unit_cost': 50}},
    }]
    est['selected_tier'] = 'better'
    assert A._is_report_only(est) is False
    # Whatever the tier math makes of it, it is NOT the repair total.
    assert A._estimate_total(est) != 3900
    assert A._estimate_total(est) > 0


def test_priority_buckets_sum_into_one_total(A):
    imm, soon, mon, total, any_range = A._pc_repair_totals(_report(*_RECS))
    assert (imm, soon, mon) == (1500, 2000, 400)
    assert total == 3900
    assert any_range is False


def test_a_legacy_range_totals_its_low_end_and_is_flagged(A):
    est = _report(('immediate', 'Full replacement', '$8,000 – $12,000'))
    assert A._estimate_total(est) == 8000
    assert A._pc_repair_totals(est)[4] is True


# ── When it must NOT engage ──────────────────────────────────────────

def test_an_unpriced_report_stays_a_zero_dollar_estimate(A):
    """No price on any recommendation is not a free job — it is no bid yet."""
    est = _report(('immediate', 'Replace cracked pipe boot', ''))
    assert A._is_report_only(est) is False
    assert A._estimate_total(est) == 0


def test_a_hidden_report_cannot_price_the_estimate(A):
    """The Roof Health chip gates the customer's only explanation of the
    number. Turn the report off and there is nothing to justify a total."""
    est = _report(*_RECS, page_visibility={'report': False})
    assert A._is_report_only(est) is False
    assert A._estimate_total(est) == 0


def test_an_insurance_estimate_is_never_report_only(A):
    est = _report(*_RECS, estimate_type='insurance')
    assert A._is_report_only(est) is False


def test_an_enabled_insurance_trade_counts_as_priced_scope(A):
    est = _report(*_RECS)
    est['trades']['insurance']['enabled'] = True
    assert A._has_priced_scope(est) is True
    assert A._is_report_only(est) is False


def test_a_section_that_is_off_contributes_nothing(A):
    est = _report(*_RECS)
    est['property_condition']['sections']['roof']['enabled'] = False
    assert A._pc_repair_totals(est)[3] == 0
    assert A._is_report_only(est) is False


def test_a_section_with_no_grade_contributes_nothing(A):
    """Same rule the printed report uses — an ungraded section is not in it."""
    est = _report(*_RECS, )
    est['property_condition']['sections']['roof']['grade'] = ''
    assert A._pc_repair_totals(est)[3] == 0


# ── What the customer gets ───────────────────────────────────────────

def test_the_customer_view_drops_the_empty_package_comparison(A):
    html = A.build_customer_view(_report(*_RECS), 'tok')
    assert 'Home Condition Report' in html
    # The G/B/B chooser and its tier cards have no place on a report-only bid.
    # Match the ATTRIBUTE, not the class name: _CV_CSS ships a .cv-tier-cards
    # rule into every page's <head>, so a bare substring is in the stylesheet
    # whether or not a single card was rendered. The guard test below proves
    # this marker really appears on an estimate that has packages.
    assert 'class="cv-tier-cards"' not in html
    assert 'Choose Your Package' not in html


def test_the_package_comparison_markers_are_real(A):
    """Guards the test above: the same estimate WITH scope still ships them."""
    est = _report(*_RECS)
    est['trades']['roofing']['line_items'] = [{
        'name': 'Architectural shingles', 'quantity': 30, 'unit': 'SQ',
        'tiers': {t: {'material_unit_cost': 100, 'labor_unit_cost': 50}
                  for t in ('good', 'better', 'best')},
    }]
    html = A.build_customer_view(est, 'tok')
    assert 'class="cv-tier-cards"' in html
    assert 'Choose Your Package' in html


def test_the_customer_view_totals_the_repairs_as_the_price(A):
    html = A.build_customer_view(_report(*_RECS), 'tok')
    # The condition report is the itemization — it is on this page already.
    assert 'Replace cracked pipe boot' in html
    assert 'Reseal step flashing' in html
    assert 'Estimated Repair Total' in html
    assert '$3,900.00' in html


def test_the_repairs_are_listed_once_not_twice(A):
    """The condition report lists every recommendation with its price. A second
    itemization under its own heading is the same three lines again."""
    html = A.build_customer_view(_report(*_RECS), 'tok')
    assert html.count('Replace cracked pipe boot') == 1


def test_nothing_on_the_page_describes_a_replacement_nobody_quoted(A):
    """The permit card promises the permit is priced in and the 'What's
    Included' card walks through package warranties and a complete tear-off to
    the deck. Neither is true of a repair bid."""
    html = A.build_customer_view(_report(*_RECS), 'tok')
    assert 'Who Pulls Your Permit' not in html
    assert 'tear-off' not in html.lower()
    # The glance must not quote a package warranty either — there is no package
    # to have chosen. The company's own warranty line still shows; that is
    # about the company, not this scope.
    assert '5-year Project One workmanship warranty' not in html


def test_that_replacement_language_is_real_on_an_ordinary_estimate(A):
    """Guards the test above against passing by finding nothing."""
    est = _report(*_RECS)
    est['trades']['roofing']['line_items'] = [{
        'name': 'Architectural shingles', 'quantity': 30, 'unit': 'SQ',
        'tiers': {t: {'material_unit_cost': 100, 'labor_unit_cost': 50}
                  for t in ('good', 'better', 'best')},
    }]
    html = A.build_customer_view(est, 'tok')
    assert 'Who Pulls Your Permit' in html
    assert 'tear-off' in html.lower()


def test_the_customer_view_can_still_be_signed(A):
    html = A.build_customer_view(_report(*_RECS), 'tok')
    assert '/sign/tok' in html
    assert 'Approve These Repairs' in html


def test_an_hoa_report_keeps_its_own_wording(A):
    est = _report(*_RECS)
    est['property_condition']['audience'] = 'hoa'
    html = A.build_customer_view(est, 'tok')
    assert 'Estimated Repair Investment' in html


def test_a_recommendation_with_no_price_still_lists_but_adds_nothing(A):
    est = _report(('immediate', 'Replace cracked pipe boot', '$1,500'),
                  ('monitor',   'Watch the north valley',    ''))
    html = A.build_customer_view(est, 'tok')
    assert 'Watch the north valley' in html
    assert A._estimate_total(est) == 1500


# ── The two implementations, held to the same numbers ────────────────
# The browser prints this total on the PDF; the server puts it on the sign
# page, the estimate list, the funnel and the Den push. They are hand-mirrored
# with no parity harness of their own, so the runner below is it.

import json
import os
import shutil
import subprocess

import pytest

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'report_only_runner.js')


def _legacy_report():
    """A roof_health estimate saved before the Condition tab existed. Both
    sides migrate it on READ (pcGet / _cv_condition_pc) and must agree."""
    return {
        'estimate_type': 'retail',
        'trades': {'roofing': {'enabled': True, 'line_items': [], 'mode': 'gbb'}},
        'roof_health': {
            'condition': 'poor', 'summary': '', 'findings': [],
            'recommendations': [
                {'priority': 'immediate', 'description': 'Full replacement',
                 'cost_range': '$8,000 – $12,000'},
                {'priority': 'monitor', 'description': 'Reseal skylight',
                 'cost_range': '$650'},
            ],
        },
    }


def _with_scope(est):
    est = json.loads(json.dumps(est))
    est['trades']['roofing']['line_items'] = [{
        'name': 'Architectural shingles', 'quantity': 30, 'unit': 'SQ',
        'tiers': {'better': {'material_unit_cost': 100, 'labor_unit_cost': 50}},
    }]
    return est


CASES = [
    _report(*_RECS),                                        # the ordinary case
    _report(('immediate', 'Full replacement', '$8,000 – $12,000')),   # legacy range
    _report(('immediate', 'Boot', '$1,500.50'), ('soon', 'Flashing', '900')),
    _report(('immediate', 'Replace cracked pipe boot', '')),          # unpriced
    _report(*_RECS, page_visibility={'report': False}),
    _report(*_RECS, estimate_type='insurance'),
    _with_scope(_report(*_RECS)),
    _legacy_report(),
    {'trades': {}, 'property_condition': None},             # an empty estimate
]

_OFF = json.loads(json.dumps(_report(*_RECS)))
_OFF['property_condition']['sections']['roof']['enabled'] = False
CASES.append(_OFF)

_UNGRADED = json.loads(json.dumps(_report(*_RECS)))
_UNGRADED['property_condition']['sections']['roof']['grade'] = ''
CASES.append(_UNGRADED)

# The Report type, on both paths: priced, unpriced (still a report), hidden
# report (not a bid), and one that has since been given scope (not a report any
# more). The type is read by both sides, so it has to be mirrored like the rest.
_TYPED = json.loads(json.dumps(_report(*_RECS)))
_TYPED['estimate_type'] = 'report'
for _td in _TYPED['trades'].values():
    _td['enabled'] = False
CASES.append(_TYPED)

_TYPED_UNPRICED = json.loads(json.dumps(_TYPED))
for _r in _TYPED_UNPRICED['property_condition']['sections']['roof']['recommendations']:
    _r['cost_range'] = ''
CASES.append(_TYPED_UNPRICED)

_TYPED_HIDDEN = json.loads(json.dumps(_TYPED))
_TYPED_HIDDEN['page_visibility'] = {'report': False}
CASES.append(_TYPED_HIDDEN)

_TYPED_WITH_SCOPE = json.loads(json.dumps(_TYPED))
_TYPED_WITH_SCOPE['trades']['roofing'].update(enabled=True, line_items=[{
    'name': 'Architectural shingles', 'quantity': 30, 'unit': 'SQ',
    'tiers': {'better': {'material_unit_cost': 100, 'labor_unit_cost': 50}},
}])
CASES.append(_TYPED_WITH_SCOPE)


@pytest.fixture(scope='module')
def js(tmp_path_factory):
    if shutil.which('node') is None:
        pytest.skip('node not installed')
    d = tmp_path_factory.mktemp('reportonly')
    fx, out = d / 'fixtures.json', d / 'js.json'
    fx.write_text(json.dumps({'cases': CASES}), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, 'report_only_runner.js failed:\n%s' % proc.stderr
    return json.loads(out.read_text(encoding='utf-8'))


def test_the_runner_actually_covered_every_case(js):
    assert len(js) == len(CASES)


@pytest.mark.parametrize('i', range(len(CASES)))
def test_both_sides_price_the_repairs_identically(A, js, i):
    est = CASES[i]
    imm, soon, mon, total, any_range = A._pc_repair_totals(est)
    got = js[i]
    assert round(got['immediate'], 2) == round(imm, 2)
    assert round(got['soon'], 2) == round(soon, 2)
    assert round(got['monitor'], 2) == round(mon, 2)
    assert round(got['total'], 2) == round(total, 2)
    assert got['anyRange'] is any_range


@pytest.mark.parametrize('i', range(len(CASES)))
def test_both_sides_agree_on_whether_this_is_a_report_only_estimate(A, js, i):
    est = CASES[i]
    assert js[i]['hasScope'] is A._has_priced_scope(est)
    assert js[i]['reportOnly'] is A._is_report_only(est)


def test_the_legacy_roof_health_path_is_actually_exercised(js):
    """Guards the case above: a migrated report really does carry a price, so
    the agreement is not two implementations agreeing on zero."""
    legacy = js[CASES.index(_legacy_report())]
    assert legacy['total'] == 8650
    assert legacy['anyRange'] is True
    assert legacy['reportOnly'] is True


# ── The Report estimate type ─────────────────────────────────────────
# The shape rule above is a rescue: it catches an inspection written on top of
# the default estimate. The type is the fix — it turns every trade OFF, so the
# empty-Roofing trap cannot happen in the first place, and it lands the rep on
# the Condition tab, which is the only page a condition report is written on.

def _typed_report(*recs, **est):
    """A Report-type estimate: every trade off, which is what the type does."""
    doc = _report(*recs, **est)
    doc['estimate_type'] = 'report'
    for td in doc['trades'].values():
        td['enabled'] = False
    return doc


def test_the_report_type_is_priced_by_its_repairs(A):
    est = _typed_report(*_RECS)
    assert A._is_report_only(est) is True
    assert A._estimate_total(est) == 3900


def test_an_unpriced_report_type_renders_as_a_report_and_still_totals_zero(A):
    """The type says what this estimate IS, so it must not fall back to three
    empty package columns — but it must not invent a price either."""
    est = _typed_report(('immediate', 'Replace cracked pipe boot', ''))
    assert A._is_report_only(est) is True
    assert A._estimate_total(est) == 0
    html = A.build_customer_view(est, 'tok')
    assert 'class="cv-tier-cards"' not in html


def test_pricing_a_trade_on_a_report_estimate_makes_it_an_ordinary_estimate(A):
    """The inspect → report → 'yes, replace it' path. The type is a starting
    posture, not a lock: scope is tested BEFORE the type, so the rep never has
    to remember to switch it back."""
    est = _typed_report(*_RECS)
    est['trades']['roofing'].update(enabled=True, line_items=[{
        'name': 'Architectural shingles', 'quantity': 30, 'unit': 'SQ',
        'tiers': {'better': {'material_unit_cost': 100, 'labor_unit_cost': 50}},
    }])
    est['selected_tier'] = 'better'
    assert A._is_report_only(est) is False
    assert A._estimate_total(est) != 3900
    assert A._estimate_total(est) > 0


def test_a_hidden_report_still_wins_over_the_type(A):
    """Nothing on the page would explain the number."""
    est = _typed_report(*_RECS, page_visibility={'report': False})
    assert A._is_report_only(est) is False


def test_the_signed_notification_does_not_name_a_package(A):
    """The tier fallback would email the rep 'Better' for a repair bid that
    never offered a Better."""
    est = _typed_report(*_RECS)
    est['signature'] = {'name': 'Jon Smith', 'signed_at': '2026-09-02T10:00:00Z'}
    assert A._pick_summary_label(est) == ''      # no package trade to name
    assert A._is_report_only(est) is True


# ── Every type reaches every place that offers a type ────────────────

def _src(name):
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(os.path.dirname(here), 'static', name), encoding='utf-8') as f:
        return f.read()


def _estimate_types():
    """The real ESTIMATE_TYPES list out of app.js."""
    import re
    m = re.search(r'const ESTIMATE_TYPES = \[([^\]]*)\]', _src('app.js'))
    assert m, 'ESTIMATE_TYPES not found in app.js'
    return re.findall(r"'([a-z_]+)'", m.group(1))


def test_report_is_an_estimate_type():
    assert 'report' in _estimate_types()


def test_every_estimate_type_has_a_sidebar_button():
    """Commercial once reached the sidebar and missed the create dialog. Both
    lists are driven off ESTIMATE_TYPES now; these hold the buttons to it."""
    html = _src('index.html')
    for t in _estimate_types():
        assert f'id="type-{t}"' in html, f'no sidebar button for estimate type {t!r}'


def test_every_estimate_type_has_a_create_dialog_button():
    js = _src('app.js')
    for t in _estimate_types():
        assert f'id="doc-type-{t}"' in js, f'no create-dialog button for estimate type {t!r}'


def test_the_report_type_turns_every_trade_off():
    """The one thing the type exists to guarantee. Read off setEstimateType's
    report branch rather than restated, so deleting the line fails here."""
    js = _src('app.js')
    branch = js[js.index("if (type === 'report') {"):js.index("} else if (type === 'insurance') {")]
    assert 'S.trades[t].enabled = false' in branch
    assert "S.page_visibility.report = true" in branch


def test_the_report_type_lands_the_rep_on_the_condition_tab():
    js = _src('app.js')
    assert "else if (type === 'report') switchPage('report');" in js


def test_the_report_type_cases_are_not_all_the_same_answer(js):
    """Guards the mirror above: the four Report-type cases must actually
    disagree with each other, or 'both sides agree' means nothing."""
    typed, unpriced, hidden, with_scope = js[-4], js[-3], js[-2], js[-1]
    assert (typed['reportOnly'], typed['total'])        == (True, 3900)
    assert (unpriced['reportOnly'], unpriced['total'])  == (True, 0)
    assert hidden['reportOnly'] is False
    assert with_scope['reportOnly'] is False and with_scope['hasScope'] is True
