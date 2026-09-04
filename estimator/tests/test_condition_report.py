"""Condition report pricing — the report a rep hands a realtor.

Each recommendation used to carry a free-text cost RANGE, and the summary table
summed only the first number of each and printed a literal '+'. That is fine for
an outlook and useless as a bid, which is what reps actually do with this page.
The field is one price now and the totals are exact.

Nothing stored is rewritten: estimates saved before the change still hold
'$8,000 – $12,000', still display verbatim, and still total the low end — so the
'+' has to come back for those, and only for those.

This file also happens to be the FIRST test coverage this report has ever had.
Both renderers are hand-mirrored with no parity harness binding them
(_cv_condition_block in app.py, _printConditionHTML in app.js); these cover the
server side, which is what customers see through /sign.
"""


def _cost_table(html):
    """Every computed total in the report — the per-section subtotal rows plus
    the closing Total row when there is one.

    The priority-bucketed summary table these tests were written against is
    gone: it restated, as three sums, costs that are printed line by line a few
    inches lower, and it sat on a page of its own ahead of the findings. The
    parsing rules it protected did not go anywhere, so these tests now read the
    numbers off the rows that survived it.

    NOT locatable by 'cvcond-tbl' alone — the recommendation tables carry that
    class too, so matching on it would pick up a rep's typed cost text and any
    '+' inside it, and every one of these tests would pass on the raw input
    rather than on the sum. Only the computed rows are returned.
    """
    import re
    out = []
    for m in re.finditer(r'<tr class="(cvcond-sub|cvcond-cost-total)".*?</tr>',
                         html, re.S):
        out.append(m.group(0))
    return '\n'.join(out)


def _est(*recs, grade='C'):
    """An estimate whose Roofing section carries the given recommendations."""
    return {
        'property_condition': {
            'inspection_date': '2026-09-01',
            'executive_notes': '',
            'audience': 'homeowner',
            'sections': {
                'roof': {
                    'enabled': True, 'grade': grade, 'notes': '',
                    'findings': [],
                    'recommendations': [
                        dict(id=str(i), priority=p, description=d, cost_range=c)
                        for i, (p, d, c) in enumerate(recs)
                    ],
                },
            },
        },
    }


# ── Single prices: the report totals exactly ───────────────────────────

def test_single_prices_total_exactly_with_no_plus(A):
    costs = _cost_table(A._cv_condition_block(_est(
        ('immediate', 'Replace cracked boot', '$1,500'),
        ('soon',      'Reseal flashing',      '$2,000'),
    )))
    assert '$3,500.00' in costs
    # The '+' is what said "at least this much". With one price per line the
    # number is the number — a realtor can act on it.
    assert '+' not in costs


def test_a_bare_number_still_counts(A):
    costs = _cost_table(A._cv_condition_block(_est(('immediate', 'Replace boot', '1500'))))
    assert '$1,500.00' in costs and '+' not in costs


def test_every_priority_counts_toward_the_one_total(A):
    """Priority is still printed against each recommendation; it no longer
    splits the money into three sums the customer has to add up."""
    html = A._cv_condition_block(_est(
        ('immediate', 'Now',   '$1,000'),
        ('soon',      'Later', '$200'),
        ('monitor',   'Watch', '$30'),
    ))
    assert '$1,230.00' in _cost_table(html)
    for word in ('Immediate', '1&ndash;2 Years', 'Monitor'):
        assert word in html or word.replace('&ndash;', '–') in html


# ── Legacy ranges: unchanged, and still honest ─────────────────────────

def test_a_legacy_range_still_totals_the_low_end_and_keeps_the_plus(A):
    """Estimates saved before the change are not rewritten. Their total really
    IS a low-end sum, so dropping the '+' there would overstate precision on a
    report that may already be in a customer's hands."""
    costs = _cost_table(A._cv_condition_block(
        _est(('immediate', 'Replace deck', '$8,000 – $12,000'))))
    assert '$8,000.00+' in costs


def test_one_legacy_range_makes_the_whole_report_a_plus(A):
    # A mixed report is only as exact as its vaguest line.
    costs = _cost_table(A._cv_condition_block(_est(
        ('immediate', 'Priced',  '$1,500'),
        ('soon',      'Ranged',  '$500-$900'),
    )))
    assert '$2,000.00+' in costs


def test_a_hyphen_range_is_detected_like_an_en_dash(A):
    # Reps type whichever dash the keyboard gives them.
    for text in ('$500-$900', '$500 – $900', '$500 to $900'):
        costs = _cost_table(A._cv_condition_block(_est(('immediate', 'x', text))))
        assert '$500.00+' in costs, text


def test_an_open_ended_range_keeps_the_plus(A):
    # "$500 to" has one number but promises a second.
    costs = _cost_table(A._cv_condition_block(_est(('immediate', 'x', '$500 to'))))
    assert '$500.00+' in costs


def test_a_price_with_cents_is_not_mistaken_for_a_range(A):
    """'$1,500.50' holds one number written with a decimal point. Reading it as
    two would put a spurious '+' on an otherwise exact report."""
    costs = _cost_table(A._cv_condition_block(_est(('immediate', 'x', '$1,500.50'))))
    assert '$1,500.50' in costs
    assert '+' not in costs


# ── The footer has to agree with the numbers above it ──────────────────

def test_footer_no_longer_calls_the_figures_ranges(A):
    """It used to read 'Cost estimates are approximate ranges and do not
    constitute a formal bid' — directly contradicting a page of firm prices."""
    html = A._cv_condition_block(_est(('immediate', 'x', '$1,500')))
    assert 'approximate ranges' not in html
    assert 'do not constitute a formal bid' not in html
    assert '30 days' in html
    assert 'change order' in html


# ── Gates that must keep working ───────────────────────────────────────

def test_hidden_when_the_print_chip_is_off(A):
    est = _est(('immediate', 'x', '$1,500'))
    est['page_visibility'] = {'report': False}
    assert A._cv_condition_block(est) == ''


def test_no_totals_at_all_when_nothing_is_priced(A):
    html = A._cv_condition_block(_est(('monitor', 'Keep an eye on it', '')))
    assert _cost_table(html) == ''


# ── The summary page is gone ───────────────────────────────────────────
# It sat ahead of the findings and said nothing the findings do not: every
# grade it gridded reappears on its own section header, and its three
# priority sums were sums of costs printed line by line on the next page.

def test_the_condition_snapshot_grid_is_gone(A):
    html = A._cv_condition_block(_est(('immediate', 'Replace boot', '$1,500')))
    assert 'cvcond-grid' not in html
    assert 'cvcond-letter' not in html


def test_the_grade_is_still_shown_on_the_section_itself(A):
    """Removing the grid must not lose the grade — it moves, it does not go."""
    html = A._cv_condition_block(_est(('immediate', 'Replace boot', '$1,500'),
                                      grade='D'))
    assert 'Grade D' in html
    assert 'Poor' in html


def test_the_priority_bucket_summary_is_gone(A):
    html = A._cv_condition_block(_est(
        ('immediate', 'Now',   '$1,000'),
        ('soon',      'Later', '$200'),
    ))
    for bucket in ('Immediate repairs (D/F)', 'Short-term (C grades)',
                   'Maintenance (B grades)'):
        assert bucket not in html


def test_the_cost_column_is_headed_cost_not_estimated_cost(A):
    """Named the way an estimate names it."""
    html = A._cv_condition_block(_est(('immediate', 'Replace boot', '$1,500')))
    assert '<th scope="col">Cost</th>' in html
    assert 'Est. Cost' not in html
    assert 'Estimated Repair Costs' not in html
    assert 'Estimated Total' not in html


def test_each_section_subtotals_like_a_trade_does(A):
    html = A._cv_condition_block(_est(('immediate', 'Replace boot', '$1,500'),
                                      ('soon', 'Reseal flashing', '$2,000')))
    assert 'Roofing Subtotal' in html
    assert '$3,500.00' in _cost_table(html)


def test_a_report_only_estimate_does_not_print_the_total_twice(A):
    """The navy repair-total bar sits directly under this block on a
    report-only estimate. The same figure inches apart is how a customer
    starts wondering which one they owe."""
    est = _est(('immediate', 'Replace boot', '$1,500'))
    assert A._is_report_only(est) is True
    assert 'cvcond-cost-total' not in A._cv_condition_block(est)
    # …but the subtotal, which is per section rather than the bid, stays.
    assert 'Roofing Subtotal' in A._cv_condition_block(est)


def test_a_priced_estimate_closes_its_report_with_a_total(A):
    """Here nothing else states it — the estimate's own total is for the
    replacement, not for the repairs the report recommends."""
    est = _est(('immediate', 'Replace boot', '$1,500'))
    est['trades'] = {'roofing': {'enabled': True, 'line_items': [{
        'name': 'Shingles', 'quantity': 30,
        'tiers': {'better': {'material_unit_cost': 100, 'labor_unit_cost': 50}}}]}}
    assert A._is_report_only(est) is False
    html = A._cv_condition_block(est)
    assert 'cvcond-cost-total' in html
    assert '>Total<' in html
