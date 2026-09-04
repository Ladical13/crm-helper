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
    for m in re.finditer(r'<div class="cvcond-(?:sub|total)[^"]*">.*?</div>', html, re.S):
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
    # Priority survives as a pill on each line — see
    # test_priority_reads_as_a_pill_not_a_column for the labels themselves.
    for word in ('Now', '1–2 Yrs', 'Monitor'):
        assert word in html, word


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
    """Removing the grid must not lose the grade — it moves onto the section
    header and gets to be a mark rather than a caption."""
    html = A._cv_condition_block(_est(('immediate', 'Replace boot', '$1,500'),
                                      grade='D'))
    assert 'class="cvcond-grade"' in html
    assert '>D</span>' in html
    assert 'Poor' in html


def test_the_priority_bucket_summary_is_gone(A):
    html = A._cv_condition_block(_est(
        ('immediate', 'Now',   '$1,000'),
        ('soon',      'Later', '$200'),
    ))
    for bucket in ('Immediate repairs (D/F)', 'Short-term (C grades)',
                   'Maintenance (B grades)'):
        assert bucket not in html


def test_the_price_sits_on_the_line_it_pays_for(A):
    """Not a table column three cells away from the work — the price is on the
    same row as the recommendation, set in the serif the estimate totals use."""
    html = A._cv_condition_block(_est(('immediate', 'Replace boot', '$1,500')))
    assert 'class="cvwork-amt"' in html
    assert 'Replace boot' in html
    assert '$1,500' in html
    # None of the old hedging language survives anywhere in the report.
    for gone in ('Est. Cost', 'Estimated Repair Costs', 'Estimated Total'):
        assert gone not in html


def test_a_recommendation_with_no_price_says_so_rather_than_showing_a_dash(A):
    html = A._cv_condition_block(_est(('soon', 'Re-inspect next spring', '')))
    assert 'Quoted separately' in html
    assert 'cvwork-noamt' in html


def test_priority_reads_as_a_pill_not_a_column(A):
    html = A._cv_condition_block(_est(('immediate', 'Now',   '$1,000'),
                                      ('soon',      'Later', '$200'),
                                      ('monitor',   'Watch', '$30')))
    for cls, label in (('cvpri-immediate', 'Now'), ('cvpri-soon', '1–2 Yrs'),
                       ('cvpri-monitor', 'Monitor')):
        assert cls in html, cls
        assert label in html, label


def test_findings_carry_their_severity(A):
    est = _est(('immediate', 'Replace boot', '$1,500'))
    est['property_condition']['sections']['roof']['findings'] = [
        {'id': '1', 'area': 'North slope', 'severity': 'high',
         'description': 'Boot cracked through'}]
    html = A._cv_condition_block(est)
    assert 'What we found' in html
    assert 'North slope' in html
    assert 'cvfind-sev' in html
    assert 'High' in html


def test_each_section_subtotals_like_a_trade_does(A):
    html = A._cv_condition_block(_est(('immediate', 'Replace boot', '$1,500'),
                                      ('soon', 'Reseal flashing', '$2,000')))
    assert 'Roofing subtotal' in html
    assert '$3,500.00' in _cost_table(html)


def test_a_report_only_estimate_does_not_print_the_total_twice(A):
    """The navy repair-total bar sits directly under this block on a
    report-only estimate. The same figure inches apart is how a customer
    starts wondering which one they owe."""
    est = _est(('immediate', 'Replace boot', '$1,500'))
    assert A._is_report_only(est) is True
    assert 'cvcond-total' not in A._cv_condition_block(est)
    # …but the subtotal, which is per section rather than the bid, stays.
    assert 'Roofing subtotal' in A._cv_condition_block(est)


def test_a_priced_estimate_closes_its_report_with_a_total(A):
    """Here nothing else states it — the estimate's own total is for the
    replacement, not for the repairs the report recommends."""
    est = _est(('immediate', 'Replace boot', '$1,500'))
    est['trades'] = {'roofing': {'enabled': True, 'line_items': [{
        'name': 'Shingles', 'quantity': 30,
        'tiers': {'better': {'material_unit_cost': 100, 'labor_unit_cost': 50}}}]}}
    assert A._is_report_only(est) is False
    html = A._cv_condition_block(est)
    assert 'cvcond-total' in html
    assert '>Total<' in html


# ── The two renderers have to stay one document ──────────────────────
# _cv_condition_block (app.py) is what the customer opens from the link;
# _printConditionHTML (app.js) is what the PDF and the print view produce.
# They are hand-mirrored with no parity harness, so the pieces that can drift
# silently are pinned here.

def _appjs():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(os.path.dirname(here), 'static', 'app.js'),
              encoding='utf-8') as f:
        return f.read()


def test_the_priority_pill_labels_match_on_both_sides(A):
    """A pill reading "Now" on screen and "Immediate" on paper is two
    documents, and the customer has both."""
    import re
    src = _appjs()
    m = re.search(r'const PC_PRI_SHORT\s*=\s*\{([^}]*)\}', src)
    assert m, 'PC_PRI_SHORT not found in app.js'
    js = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", m.group(1)))
    assert js == A._PC_PRI_SHORT, f'{js!r} != {A._PC_PRI_SHORT!r}'


def test_the_print_report_has_no_summary_page_left(A):
    """The cover page went with the grid and the bucket table. What is left
    must not quietly grow one back."""
    src = _appjs()
    assert 'p-cond-cover' not in src
    assert 'Condition Snapshot' not in src
    assert 'p-cond-grade-grid' not in src


def test_the_print_report_prices_each_line_and_subtotals(A):
    src = _appjs()
    for marker in ('p-work-amt', 'p-work-sub-amt', 'p-cond-grade', 'p-find-sev'):
        assert marker in src, marker


# ── Photos beside the finding they document ──────────────────────────
# A finding used to be a sentence, and the photograph of the damage sat in a
# gallery pages away. A homeowner read "rubber collar has split at the base"
# and then, much later, met an unlabelled close-up of a pipe boot and had to
# join the two up. A finding can carry photo_ids now, and they print on the
# line that describes them.

def _est_with_photos(*photo_ids, show_in_estimate=True):
    est = _est(('immediate', 'Replace boot', '$1,500'))
    est['photos'] = [
        {'id': 'p1', 'filename': 'a.jpg', 'caption': 'Cracked boot, north slope',
         'show_in_estimate': show_in_estimate},
        {'id': 'p2', 'filename': 'b.jpg', 'caption': 'Ridge cap',
         'show_in_estimate': show_in_estimate},
    ]
    est['property_condition']['sections']['roof']['findings'] = [
        {'id': 'f1', 'area': 'North slope', 'severity': 'high',
         'description': 'Pipe boot cracked through', 'photo_ids': list(photo_ids)}]
    return est


def test_a_findings_photo_renders_on_the_finding(A):
    html = A._cv_condition_block(_est_with_photos('p1'))
    assert 'cvfind-shots' in html
    assert '/uploads/a.jpg' in html
    # The finding text is the caption here, so the photo does not carry a
    # second one under it.
    assert 'figcaption' not in html


def test_the_photo_report_drops_what_a_finding_already_shows(A):
    """One photograph, one place. The gallery copy had no explanation beside
    it, so it was the worse of the two."""
    est = _est_with_photos('p1')
    gallery = A._cv_photos_block(est)
    assert '/uploads/a.jpg' not in gallery
    assert '/uploads/b.jpg' in gallery, 'an unattached photo still belongs in the gallery'


def test_a_photo_on_no_finding_is_untouched(A):
    est = _est_with_photos()
    gallery = A._cv_photos_block(est)
    assert '/uploads/a.jpg' in gallery and '/uploads/b.jpg' in gallery


def test_an_attached_photo_prints_even_when_it_is_out_of_the_gallery(A):
    """show_in_estimate governs the GALLERY. A rep who attached a photo to a
    finding has already said they want it shown."""
    est = _est_with_photos('p1', show_in_estimate=False)
    assert '/uploads/a.jpg' in A._cv_condition_block(est)


def test_a_dangling_photo_id_is_skipped_not_crashed(A):
    """Deleting a photo must not take the whole report down with it."""
    est = _est_with_photos('p1')
    est['photos'] = [p for p in est['photos'] if p['id'] != 'p1']
    html = A._cv_condition_block(est)
    assert 'Pipe boot cracked through' in html
    assert 'cvfind-shots' not in html


def test_findings_from_before_photos_existed_still_render(A):
    est = _est_with_photos()
    del est['property_condition']['sections']['roof']['findings'][0]['photo_ids']
    assert 'Pipe boot cracked through' in A._cv_condition_block(est)


def test_the_two_renderers_subtract_the_same_photos(A):
    """_cv_photos_block (app.py) and buildPrintContent (app.js) each drop the
    finding photos from the gallery. Different answers means the PDF and the
    web page show a different number of photographs."""
    src = _appjs()
    assert 'function pcFindingPhotoIds()' in src
    assert '_findingShots.has(p.id)' in src, 'the printed gallery must subtract them too'
    assert 'pcFindingPhotoIds().forEach(id => needed.add(id))' in src, \
        'an attached photo must be baked into the print cache or it prints as a gap'
