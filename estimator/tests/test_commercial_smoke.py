"""End-to-end smoke of the commercial sell, run against the seeded price book.

test_commercial.py holds down the pieces. This drives the whole thing once, the
way a rep does it: pick the default system, enter a real building, let the
measurements fill the quantities, price it, and render every artifact the job
produces. It prints the bid so a failure shows the actual numbers rather than
just a red assertion.
"""
import json


BUILDING = {
    # A 200' x 120' single-story warehouse, 22' to the deck — 240 SQ.
    'comm_squares': 240, 'comm_waste_pct': 10,
    'comm_length_ft': 200, 'comm_width_ft': 120, 'comm_height_ft': 22,
    'comm_uplift': 60,
    'comm_perimeter_lf': 640, 'comm_parapet_lf': 640,
    'comm_penetrations': 14, 'comm_drains': 6, 'comm_curbs': 4,
    'comm_skylights': 2, 'comm_pitch_pans': 3, 'comm_walkway_pads': 12,
    'comm_work_type': 0, 'comm_sections': 1,
}


def _attach_profile(bundle, cat):
    """Mirror of _commAttachProfile in app.js: the membrane's `attach` tag
    decides which fastener layers a system buys. Coating fastens nothing,
    mechanical buys both layers, adhered buys insulation only. The client
    stores the answer as measurements, which is what commercial_fastening
    reads — so a membrane with no tag silently costs a bid its seam screws."""
    kinds = [cat[pid].get('attach') for pid in bundle['product_ids'] if cat[pid].get('attach')]
    if 'coating' in kinds:
        return {'comm_insul_attach': 0, 'comm_seam_attach': 0}
    if 'mechanical' in kinds:
        return {'comm_insul_attach': 1, 'comm_seam_attach': 1}
    return {'comm_insul_attach': 1, 'comm_seam_attach': 0}   # adhered / unknown


def test_every_bundle_tags_its_membrane_so_the_fastener_calc_can_answer(client):
    """Every system has to resolve to a definite attachment. An untagged
    membrane falls through to the 'unknown' branch, which fails closed on seam
    — thousands of screws quietly drop off a mechanically attached bid."""
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    for b in pb['commercial_bundles']:
        kinds = [cat[pid].get('attach') for pid in b['product_ids'] if cat[pid].get('attach')]
        assert kinds, f'{b["id"]} has no membrane carrying an attach tag'
    # Mechanically fastened packages buy seam fasteners; adhered ones do not.
    expected = {'cb_tpo_ma': 1, 'cb_tpo_lo_mf': 1,
                'cb_epdm_mf': 1, 'cb_epdm_lo_mf': 1,
                'cb_tpo_fa': 0, 'cb_tpo_lo_fa': 0,
                'cb_epdm': 0, 'cb_epdm_lo_fa': 0,
                'cb_modbit': 0, 'cb_coating': 0}
    for b in pb['commercial_bundles']:
        got = _attach_profile(b, cat)['comm_seam_attach']
        assert got == expected[b['id']], f'{b["id"]} seam fastening resolved to {got}'


def _measured_qty(A, measure, m, table):
    """Mirror of the client's measuredQty for the commercial measures."""
    sq_waste = m['comm_squares'] * (1 + m['comm_waste_pct'] / 100.0)
    fz = A.commercial_fastening(m, table)
    return {
        'comm_sq': m['comm_squares'],
        'comm_sq_waste': sq_waste,
        'comm_perimeter': m['comm_perimeter_lf'],
        'comm_parapet': m['comm_parapet_lf'],
        'comm_penetrations': m['comm_penetrations'],
        'comm_drains': m['comm_drains'],
        'comm_curbs': m['comm_curbs'] + m['comm_skylights'],
        'comm_pitch_pans': m['comm_pitch_pans'],
        'comm_walkway_pads': m['comm_walkway_pads'],
        'comm_labor_reroof': 0 if m['comm_work_type'] else m['comm_squares'],
        'comm_labor_new': m['comm_squares'] if m['comm_work_type'] else 0,
        'comm_fast_insul': fz['insul']['total'],
        'comm_fast_seam': fz['seam']['total'],
    }.get(measure, 0)


def test_a_full_commercial_bid_builds_prices_and_renders(client, A):
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    bundle = next(b for b in pb['commercial_bundles']
                  if b['id'] == pb['commercial_simple_default'])
    table = A._load_commercial_fastening()

    # The client resolves attachment from the bundle and stores it as a
    # measurement; commercial_fastening reads it back. Do the same here.
    m = dict(BUILDING, **_attach_profile(bundle, cat))

    # ── the wind-zone fastener calculator has to actually answer
    fz = A.commercial_fastening(m, table)
    assert fz['ok'], f"fastener calc bailed: {fz.get('reason')}"
    assert fz['insul']['total'] > 0
    assert fz['seam']['total'] > 0, 'mechanically attached roof got no seam fasteners'

    # ── build the line items off the bundle, exactly like Load Defaults
    items, cost = [], 0.0
    for pid in bundle['product_ids']:
        p = cat[pid]
        qty = _measured_qty(A, p.get('measure'), m, table)
        line_cost = qty * p['cost']
        cost += line_cost
        items.append({'id': pid, 'catalog_id': pid, 'name': p['name'], 'unit': p['unit'],
                      'quantity': qty, 'unit_cost': p['cost'],
                      'unit_price': round(p['cost'] / (1 - 0.29), 2) if p['cost'] else 0,
                      'customer_visible': True})

    est = {
        'estimate_type': 'commercial',
        'customer': {'name': 'Northgate Business Park', 'email': 'pm@example.com',
                     'phone': '9705550123',
                     'address': {'street': '100 Industrial Way', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80538'}},
        'measurements': dict(m),
        'commercial': {'flags': {'heavy_hvac': True, 'penetrations_10plus': True},
                       'notes': 'Occupied building - staged by section.'},
        'pricing': {'mode': 'margin', 'global_rate': 35,
                    'tier_rates': {'good': 35, 'better': 35, 'best': 35},
                    'trade_rates': {'commercial': {'simple': 29, 'good': 29,
                                                   'better': 29, 'best': 29}},
                    'per_trade_overrides': {}},
        'selected_tier': 'better',
        'salesperson': 'luke',
        'shingle_selection': {'enabled': False, 'options': [], 'chosen': ''},
        'trades': {'commercial': {'enabled': True, 'mode': 'simple',
                                  'simple_bundle': bundle['id'], 'line_items': items}},
    }

    total = A.calc_selected_total(est)

    # ── report, so a regression shows its work
    out = ['', f"SYSTEM: {bundle['name']}   ({BUILDING['comm_squares']} SQ re-roof)",
           f"zone width a={fz['a']:.1f} ft   uplift {fz['rating']} psf"
           f"   insul {fz['insul']['total']:,} / seam {fz['seam']['total']:,} fasteners", '']
    for it in items:
        if it['quantity']:
            out.append(f"  {it['name'][:52]:<52} {it['quantity']:>10,.1f} {it['unit']:<3}"
                       f" x {it['unit_cost']:>8,.2f} = {it['quantity']*it['unit_cost']:>12,.2f}")
    out += ['', f"  {'MATERIAL + LABOR COST':<52} {cost:>42,.2f}",
            f"  {'SELL (29% margin)':<52} {total:>42,.2f}",
            f"  {'GROSS PROFIT':<52} {total-cost:>42,.2f}", '']
    print('\n'.join(out))

    # ── the things that must be true
    assert cost > 0 and total > cost
    # Everything a MEASUREMENT drives must have picked up a quantity. The only
    # blanks allowed are the lump sums a rep types per job (which carry no
    # measure at all) and the new-construction labor line, which comm_work_type
    # zeroes on a re-roof. Anything else blank means a measurement stopped
    # reaching its line item.
    blank = [i['name'] for i in items
             if not i['quantity'] and cat[i['catalog_id']].get('measure')
             and i['catalog_id'] != 'cl_labor_new']
    assert blank == [], f'measured lines that never got a quantity: {blank}'
    unpriced = [i['name'] for i in items if i['quantity'] and not i['unit_cost']]
    assert unpriced == [], f'lines with quantity but no cost: {unpriced}'

    # ── every artifact the job produces has to render
    est_id = client.post('/api/estimates', json=est).get_json()['estimate_id']

    # The customer view is the share link, and it has to show the bid BEFORE a
    # signature exists — once signed, /sign/<token> serves the confirmation.
    share = client.post(f'/api/estimates/{est_id}/share').get_json()
    view = client.get(share['url'])
    assert view.status_code == 200, view.status_code
    html = view.get_data(as_text=True)
    assert 'Northgate Business Park' in html
    assert 'TPO' in html
    # A commercial bid must never render the shingle-colour block a steep-slope
    # roof needs — there is no shingle on a flat roof. (The class only appears
    # in the shared stylesheet; the BLOCK is what must be absent.)
    assert 'class="cv-shingle"' not in html
    # The sell price reaches the page the customer actually reads.
    assert f'{total:,.0f}'.lstrip('$') in html.replace('$', '')

    saved = A.est_load(est_id)
    saved['signature'] = {'name': 'Dana Reyes', 'email': 'pm@example.com',
                          'signed_at': '2026-08-18T12:00:00Z', 'selected_tier': 'better',
                          'data': 'data:image/png;base64,AAA'}
    A.est_save(saved)

    signed = client.get(share['url'])
    assert signed.status_code == 200

    packet = A.build_production_packet_pdf(saved)
    assert packet[:4] == b'%PDF' and len(packet) > 5000


def test_every_package_builds_and_prices_on_the_same_building(client, A):
    """All eight packages, same 240 SQ warehouse, side by side. This is the one
    that would have caught the layover packages billing new insulation, or the
    EPDM ones quietly welding seams they cannot weld.

    Run it with `-s` to read the comparison table."""
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    bundles = {b['id']: b for b in pb['commercial_bundles']}
    table = A._load_commercial_fastening()

    order = ['cb_tpo_ma', 'cb_tpo_fa', 'cb_tpo_lo_mf', 'cb_tpo_lo_fa',
             'cb_epdm_mf', 'cb_epdm', 'cb_epdm_lo_mf', 'cb_epdm_lo_fa']
    rows, unpriced_pkgs = [], []
    for bid in order:
        b = bundles[bid]
        m = dict(BUILDING, **_attach_profile(b, cat))
        fz = A.commercial_fastening(m, table)
        assert fz['ok'], f'{bid}: fastener calc bailed ({fz.get("reason")})'

        cost = 0.0
        blanks = []
        for pid in b['product_ids']:
            p = cat[pid]
            qty = _measured_qty(A, p.get('measure'), m, table)
            cost += qty * p['cost']
            if qty and not p['cost']:
                blanks.append(p['name'])

        # A mechanically fastened package MUST come out with seam fasteners on
        # it; an adhered one must not. This is the assertion that catches a
        # membrane losing its attach tag in a future edit.
        fastened = cat[next(p for p in b['product_ids']
                            if p.startswith('cm_'))].get('attach') == 'mechanical'
        assert (fz['seam']['total'] > 0) == fastened, bid
        assert fz['insul']['total'] > 0, f'{bid} fastens nothing to the deck'

        rows.append((b['name'], cost, len(blanks)))
        if blanks:
            unpriced_pkgs.append((bid, blanks))

    width = max(len(n) for n, _, _ in rows)
    out = ['', f'  {"PACKAGE":<{width}}  {"COST":>14}  {"$/SQ":>9}  UNPRICED LINES', '']
    for name, cost, nblank in rows:
        sq = cost / BUILDING['comm_squares']
        out.append(f'  {name:<{width}}  {cost:>14,.2f}  {sq:>9,.2f}  {nblank or "":>4}')
    out.append('')
    print('\n'.join(out))

    # Complete today: both TPO tear-offs, and — since the Carlisle quote landed
    # — EPDM tear-off fully adhered. That last one is the milestone: before
    # Carlisle there was no priced EPDM roof at all.
    priced = {name for name, cost, nblank in rows if not nblank}
    assert priced == {'TPO - Tear-Off, Mechanically Fastened',
                      'TPO - Tear-Off, Fully Adhered',
                      'EPDM - Tear-Off, Fully Adhered'}, sorted(priced)

    # Asserted EXACTLY, not as a subset: this is a live checklist. A line that
    # silently loses its cost in a refactor fails it, and so does one that gets
    # filled in without being struck off here.
    expected_open = {
        # Carlisle quoted only NON-reinforced EPDM, which is specified for
        # adhered and ballasted work. Fastened needs Sure-Tough reinforced.
        'EPDM Membrane 60-mil Reinforced (Mechanically Fastened)',
        # Neither sheet carries a 1/4" board.
        'Cover Board 1/4" (Layover / Recover)',
        # The four rates Luke is getting from his crew.
        'Layover Prep & Install Labor - TPO Mechanically Fastened',
        'Layover Prep & Install Labor - TPO Fully Adhered',
        'Layover Prep & Install Labor - EPDM Mechanically Fastened',
        'Layover Prep & Install Labor - EPDM Fully Adhered',
    }
    seen = {n for _, names in unpriced_pkgs for n in names}
    assert seen == expected_open, (
        f'newly unpriced: {sorted(seen - expected_open)}; '
        f'now priced, strike off: {sorted(expected_open - seen)}')


def test_a_layover_bid_renders_its_requirements_for_the_crew(client, A):
    """End to end on the package Luke will actually reach for on an occupied
    building: it has to price, render to the customer, and put the recover
    rules in front of the crew."""
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    bundle = next(b for b in pb['commercial_bundles'] if b['id'] == 'cb_tpo_lo_mf')
    table = A._load_commercial_fastening()
    m = dict(BUILDING, **_attach_profile(bundle, cat))

    items = []
    for pid in bundle['product_ids']:
        p = cat[pid]
        qty = _measured_qty(A, p.get('measure'), m, table)
        items.append({'id': pid, 'catalog_id': pid, 'name': p['name'], 'unit': p['unit'],
                      'quantity': qty, 'unit_cost': p['cost'],
                      'unit_price': round(p['cost'] / (1 - 0.29), 2) if p['cost'] else 0,
                      'customer_visible': True})

    # The layover keeps the building's insulation: no new polyiso on the bid.
    assert not any(i['catalog_id'] == 'ca_iso' for i in items)
    assert any(i['catalog_id'] == 'ca_cover_quarter' and i['quantity'] for i in items)

    est = {
        'estimate_type': 'commercial',
        'customer': {'name': 'Northgate Business Park', 'email': 'pm@example.com',
                     'phone': '9705550123',
                     'address': {'street': '100 Industrial Way', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80538'}},
        'measurements': dict(m),
        'commercial': {'flags': {'heavy_hvac': True}, 'notes': ''},
        'pricing': {'mode': 'margin', 'global_rate': 35,
                    'tier_rates': {'good': 35, 'better': 35, 'best': 35},
                    'trade_rates': {'commercial': {'simple': 29, 'good': 29,
                                                   'better': 29, 'best': 29}},
                    'per_trade_overrides': {}},
        'selected_tier': 'better', 'salesperson': 'luke',
        'shingle_selection': {'enabled': False, 'options': [], 'chosen': ''},
        'trades': {'commercial': {'enabled': True, 'mode': 'simple',
                                  'simple_bundle': bundle['id'], 'line_items': items}},
    }
    est_id = client.post('/api/estimates', json=est).get_json()['estimate_id']
    share = client.post(f'/api/estimates/{est_id}/share').get_json()
    html = client.get(share['url']).get_data(as_text=True)
    assert view_ok(html)

    saved = A.est_load(est_id)
    assert A._est_is_layover(saved)
    packet = A.build_production_packet_pdf(saved)
    assert packet[:4] == b'%PDF' and len(packet) > 5000


def view_ok(html):
    return ('Northgate Business Park' in html and 'TPO' in html
            and 'class="cv-shingle"' not in html)
