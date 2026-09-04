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
    """Just the cost summary table.

    NOT locatable by 'cvcond-tbl' — the per-section recommendation table carries
    that class too, so matching on it picks up a rep's typed text and any '+' in
    it. The cost table is the one holding the 'cvcond-cost-total' row.
    """
    end = html.find('cvcond-cost-total')
    if end < 0:
        return ''
    start = html.rfind('<table', 0, end)
    return html[start:html.find('</table>', end)]


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


def test_priority_buckets_stay_separate(A):
    costs = _cost_table(A._cv_condition_block(_est(
        ('immediate', 'Now',   '$1,000'),
        ('soon',      'Later', '$200'),
        ('monitor',   'Watch', '$30'),
    )))
    for amount in ('$1,000.00', '$200.00', '$30.00', '$1,230.00'):
        assert amount in costs


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


def test_no_cost_table_when_nothing_is_priced(A):
    html = A._cv_condition_block(_est(('monitor', 'Keep an eye on it', '')))
    assert 'cvcond-cost-total' not in html
