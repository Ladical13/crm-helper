"""The three internal documents: what prints on them, and what files itself.

Every assertion here is a bug that shipped. The squares line vanished on any
job measured outside the steep-slope namespace; the work order printed the
homeowner's sales copy at the crew; and the material order — the one document
the supplier actually needs — had no path to the Den at all, automatic or
manual.
"""
import io

import pytest


def _pdf_text(raw):
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    r = PdfReader(io.BytesIO(raw))
    return '\n'.join(p.extract_text() or '' for p in r.pages)


def _signed(**over):
    est = {
        'estimate_number': 1042,
        'estimate_type': 'retail',
        'selected_tier': 'better',
        'customer': {'name': 'Ada Lovelace', 'phone': '9705550100',
                     'address': {'street': '12 Analytical Way', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'signature': {'signed_at': '2026-09-01T10:00:00Z', 'selected_tier': 'better'},
        'measurements': {'roof_squares': 30, 'low_slope_squares': 2, 'waste_pct': 10},
        'trades': {'roofing': {'enabled': True, 'mode': 'simple',
                               'line_items': [{'name': 'Shingles', 'quantity': 32,
                                               'unit': 'SQ', 'cost': 142}]}},
    }
    est.update(over)
    return est


def _commercial(**over):
    """A flat roof measured only in the comm_* namespace — no roof_squares at
    all, which is exactly the shape that printed no squares line."""
    est = _signed(estimate_type='commercial',
                  measurements={'comm_squares': 48, 'comm_waste_pct': 5},
                  trades={'commercial': {'enabled': True, 'mode': 'simple',
                                         'line_items': [{'name': 'TPO Membrane',
                                                         'quantity': 50, 'unit': 'SQ',
                                                         'cost': 80}]}})
    est.update(over)
    return est


# ── Squares ───────────────────────────────────────────────────────────────

BUILDERS = ('build_work_order_pdf', 'build_material_order_pdf',
            'build_permit_packet_pdf')


@pytest.mark.parametrize('builder', BUILDERS)
def test_a_steep_slope_job_prints_the_installed_squares(A, builder):
    text = _pdf_text(getattr(A, builder)(_signed())).upper()
    assert 'SQUARES TO INSTALL' in text
    # (30 - 2 low-slope) x 1.10 — the same squares_waste the material was
    # bought at, not the raw roof area.
    assert '30.8 SQ' in text


@pytest.mark.parametrize('builder', BUILDERS)
def test_a_flat_roof_prints_the_installed_squares_too(A, builder):
    """comm_squares is a separate namespace from roof_squares on purpose. All
    three documents read roof_squares only, so a commercial job printed no
    squares line anywhere and nobody noticed — there was no line to be wrong."""
    text = _pdf_text(getattr(A, builder)(_commercial())).upper()
    assert 'SQUARES TO INSTALL' in text
    assert '50.4 SQ' in text, 'expected 48 SQ + 5% waste'


def test_an_unmeasured_commercial_roof_uses_the_priced_waste_default(A):
    """app.js prices comm_sq_waste at 10% when no waste is entered. Reporting
    0% here would under-report the squares the material was bought at."""
    est = _commercial(measurements={'comm_squares': 100})
    (_lbl, sq, detail), = A.installed_squares_rows(est)
    assert sq == pytest.approx(110.0)
    assert '10% waste' in detail


def test_every_building_on_a_complex_is_counted_and_named(A):
    est = _commercial(measurements={}, structures=[
        {'name': 'Building 3', 'trade': 'commercial',
         'measurements': {'comm_squares': 20, 'comm_waste_pct': 0}},
        {'name': 'Building 5', 'trade': 'commercial',
         'measurements': {'comm_squares': 30, 'comm_waste_pct': 0}}])
    rows = dict((lbl, val) for lbl, val in A.installed_squares_kv(est))
    assert '20.0 SQ' in rows['Squares - Building 3']
    assert '30.0 SQ' in rows['Squares - Building 5']
    assert '50.0 SQ total' in rows['Squares to Install']


@pytest.mark.parametrize('builder', BUILDERS)
def test_an_unmeasured_job_gets_a_blank_not_a_confident_zero(A, builder):
    """A crew standing on a roof with no number needs somewhere to write one.
    '0.0 SQ' is worse than nothing — it reads as a measured answer."""
    text = _pdf_text(getattr(A, builder)(_signed(measurements={}))).upper()
    assert 'SQUARES TO INSTALL' in text
    assert '0.0 SQ' not in text


# ── Notes ─────────────────────────────────────────────────────────────────

def test_the_work_order_carries_crew_notes_and_not_the_customers(A):
    """notes_customer is sales copy written for the homeowner. On the job card
    it reads as an instruction to the crew, which is not what it is."""
    est = _signed(notes_customer='We will treat your property like our own.',
                  notes_internal='Gate code 4412. Dog in the back yard.')
    text = _pdf_text(A.build_work_order_pdf(est))
    assert 'Gate code 4412' in text
    assert 'treat your property' not in text


def test_a_work_order_with_only_customer_notes_has_no_notes_section(A):
    est = _signed(notes_customer='We will treat your property like our own.')
    assert 'Notes' not in _pdf_text(A.build_work_order_pdf(est))


# ── What files itself to the Den ──────────────────────────────────────────

def _sign_and_generate(client, A, monkeypatch, **kwargs):
    """A signed estimate linked to a Den job, with _crm_file_document stubbed.
    Returns (est_id, list of (doc_type, doc_name) actually filed)."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    doc = A.est_load(eid)
    doc.update(_signed())
    doc['customer']['crm_project_id'] = 'proj_123'
    A.est_save(doc)

    filed = []

    def _fake(est, pdf_bytes, upload_name, hosted_url, doc_name, doc_type,
              description):
        filed.append((doc_type, doc_name))
        return f'den_{doc_type}', None

    monkeypatch.setattr(A, '_crm_file_document', _fake)
    A.generate_production_packet(eid, **kwargs)
    return eid, filed


def test_signing_files_the_material_order_and_holds_the_work_order(client, A,
                                                                   monkeypatch):
    """The buy list is derived wholly from the signed contract, so it goes out
    on its own. The work order waits for the scheduled date, tear-off layers
    and dish the rep fills in afterwards."""
    _eid, filed = _sign_and_generate(client, A, monkeypatch, push_material=True)
    assert [d for d, _n in filed] == ['material_order']


def test_the_work_order_push_does_not_drag_the_material_order_with_it(client, A,
                                                                      monkeypatch):
    """Base44 has no upsert — every call creates a new Document. The rep
    regenerates repeatedly while filling the form, so a work-order push that
    also re-filed the buy list would leave the branch guessing which of six
    material orders is current."""
    _eid, filed = _sign_and_generate(client, A, monkeypatch, push_to_crm=True)
    assert [d for d, _n in filed] == ['work_order']


def test_generating_the_packet_files_nothing_by_default(client, A, monkeypatch):
    _eid, filed = _sign_and_generate(client, A, monkeypatch)
    assert filed == []


def test_the_material_order_push_records_its_den_id_on_the_right_row(client, A,
                                                                     monkeypatch):
    """The CRM chip reads crm_document_id off the attachment. Stamping it on
    the work order instead would tell the rep the buy list was filed when it
    was the job card that went."""
    eid, _filed = _sign_and_generate(client, A, monkeypatch, push_material=True)
    atts = {a['doc_type']: a for a in (A.est_load(eid).get('attachments') or [])
            if a.get('server_generated')}
    assert atts['material_order'].get('crm_document_id') == 'den_material_order'
    assert not atts['work_order'].get('crm_document_id')


def test_the_regenerate_endpoint_returns_both_packet_rows(client, A, monkeypatch):
    """The Documents tab replaces its whole packet pair from this response.
    Handing back only the work order dropped the Material Order off the tab
    until the next page load."""
    eid, _filed = _sign_and_generate(client, A, monkeypatch)
    r = client.post(f'/api/estimates/{eid}/production-packet', json={})
    assert r.status_code == 200
    types = {a['doc_type'] for a in r.get_json()['attachments']}
    assert types == {'work_order', 'material_order'}
