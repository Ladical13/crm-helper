import copy
import datetime as dt
import io
import uuid
import pytest
from pypdf import PdfReader
from hail import property as prop, storms, grid
from portal import users


@pytest.fixture
def hail_report(tmp_path, monkeypatch):
    monkeypatch.setenv('HAIL_DATA_DIR', str(tmp_path))
    storms.reset_cache()
    monkeypatch.setattr(prop.clock, 'company_today', lambda: dt.date(2026, 9, 28))
    point = (40.5853, -105.0844)
    storms.record('2026-09-26', grid.swath_from_points([(*point, 1.75)]))
    report = prop.snapshot({'label': '123 Example Street, Fort Collins, CO',
        'address': {'street': '123 Example Street', 'city': 'Fort Collins', 'state': 'CO', 'zip': '80521'}}, *point, days=3)
    prop.save(report, 'luke')
    yield report
    storms.reset_cache()


def test_attach_retry_and_ordinary_save_preserve_evidence(client, A, hail_report):
    eid = str(uuid.uuid4())
    original = {'estimate_id': eid, 'salesperson': 'luke', 'customer': {'name': 'Example', 'address': hail_report['address']}}
    A.est_save(original)
    url = f'/api/estimates/{eid}/hail-report'
    assert client.post(url, json={'report_id': hail_report['id']}).status_code == 400
    payload = {'report_id': hail_report['id'], 'confirmed_property': True}
    result = client.post(url, json=payload)
    assert result.status_code == 200, result.json
    assert result.json['attachment']['pages']
    assert client.post(url, json=payload).status_code == 200
    attached = A.est_load(eid)
    assert len(attached['hail_reports']) == len(attached['attachments']) == 1
    forged = copy.deepcopy(attached)
    forged['hail_reports'][0]['max_size'] = 99
    forged['attachments'][0].update(filename='forged.pdf', show_in_estimate=False)
    assert client.put(f'/api/estimates/{eid}', json=forged).status_code == 200
    saved = A.est_load(eid)
    assert saved['hail_reports'][0]['max_size'] == 1.75
    assert saved['attachments'][0]['filename'] == attached['attachments'][0]['filename']
    assert saved['attachments'][0]['show_in_estimate'] is False
    assert client.put(f'/api/estimates/{eid}', json=original).status_code == 200
    assert A.est_load(eid)['hail_reports'] == saved['hail_reports']
    assert A.est_load(eid)['attachments'][0]['show_in_estimate'] is False


def test_pdf_includes_selected_saved_report(client, A, hail_report):
    eid = str(uuid.uuid4())
    A.est_save({'estimate_id': eid, 'salesperson': 'luke', 'customer': {'name': 'Example'}, 'trades': {}})
    assert client.post(f'/api/estimates/{eid}/hail-report', json={'report_id': hail_report['id'], 'confirmed_property': True}).status_code == 200
    est = A.est_load(eid)
    storms.record('2026-09-26', grid.Swath())
    text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(A.build_signed_pdf(est))).pages)
    assert 'Property Hail History' in text and '1.75 inches' in text
    est['attachments'][0]['show_in_estimate'] = False
    text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(A.build_signed_pdf(est))).pages)
    assert 'Property Hail History' not in text


def test_forged_evidence_cannot_be_created_by_generic_save(client, A):
    eid = str(uuid.uuid4())
    data = {'estimate_id': eid, 'hail_reports': [{'id': 'invented'}],
            'attachments': [{'id': 'invented', 'doc_type': 'hail_report', 'hail_report_id': 'invented'}]}
    assert client.put(f'/api/estimates/{eid}', json=data).status_code == 200
    assert not A.est_load(eid).get('hail_reports')
    assert not A.est_load(eid)['attachments']
    eid2 = str(uuid.uuid4()); data['estimate_id'] = eid2
    assert client.post('/api/estimates', json=data).status_code == 201
    assert not A.est_load(eid2).get('hail_reports')


def test_other_rep_cannot_attach_private_report_or_access_estimate(app, A, hail_report):
    if not users.get('hailrep'):
        users.create('hailrep', password='example-password', role='rep')
    c = app.test_client()
    with c.session_transaction() as session: session['user'] = 'hailrep'
    eid = str(uuid.uuid4())
    A.est_save({'estimate_id': eid, 'salesperson': 'hailrep'})
    payload = {'report_id': hail_report['id'], 'confirmed_property': True}
    assert c.post(f'/api/estimates/{eid}/hail-report', json=payload).status_code == 404
    A.est_save({'estimate_id': eid, 'salesperson': 'luke'})
    assert c.post(f'/api/estimates/{eid}/hail-report', json=payload).status_code == 403


def test_post_to_existing_estimate_cannot_erase_evidence(client, A, hail_report):
    eid = str(uuid.uuid4())
    A.est_save({'estimate_id': eid, 'salesperson': 'luke'})
    assert client.post(f'/api/estimates/{eid}/hail-report', json={'report_id': hail_report['id'], 'confirmed_property': True}).status_code == 200
    response = client.post('/api/estimates', json={'estimate_id': eid, 'hail_reports': []})
    assert response.status_code == 201
    assert A.est_load(eid)['hail_reports'][0]['id'] == hail_report['id']


def test_hail_browser_handoff_returns_to_the_saved_estimate():
    import subprocess
    from pathlib import Path
    here = Path(__file__).parent
    result = subprocess.run(['node', str(here / 'hail_handoff_runner.cjs'),
        str(here.parent / 'static/hail-report.js')], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
