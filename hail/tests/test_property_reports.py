import datetime as dt
import io
from types import SimpleNamespace

import pytest
from flask import Flask
from pypdf import PdfReader
from hail import property as prop, storms, grid, backfill
from hail.report_pdf import build, date_ranges
from hail.property_routes import register
from portal import users

POINT = (40.5853, -105.0844)
CANDIDATE = {'label': '123 Example Street, Fort Collins, Colorado', 'lat': POINT[0], 'lng': POINT[1],
             'address': {'street': '123 Example Street', 'city': 'Fort Collins', 'state': 'CO', 'zip': '80521'}}


@pytest.fixture
def report(monkeypatch, tmp_path):
    monkeypatch.setenv('PORTAL_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(prop.clock, 'company_today', lambda: dt.date(2026, 9, 28))
    swath = grid.swath_from_points([(*POINT, 1.75)])
    storms.record('2026-09-26', swath)
    return prop.snapshot(CANDIDATE, *POINT, days=3)


def test_saved_report_survives_archive_change_and_is_private(report):
    prop.save(report, 'rep1')
    storms.record('2026-09-26', grid.Swath())
    assert prop.load(report['id'], 'rep1') == report
    assert prop.load(report['id'], 'rep2') is None
    assert prop.load(report['id'], 'manager', True) == report
    assert report['storms'][0]['size'] == 1.75
    assert report['coverage']['days_missing'] == 3  # synthetic data has no decoded coverage
    assert 'MDT' in report['storms'][0]['local_window']
    assert report['map']['cells'][0][-1] == 1.75


@pytest.mark.parametrize('point', [(32.35, -95.3), (float('nan'), -105), (40, float('inf')), (42, -105)])
def test_only_finite_colorado_points(point):
    with pytest.raises(ValueError):
        prop.point(*point)


def test_geocoder_is_bounded_to_colorado(monkeypatch):
    import portal.interactive_geo as geo
    def fake(http, operation, params, user_agent):
        assert params['bounded'] == 1 and params['countrycodes'] == 'us'
        assert params['viewbox'] == '-109.06,41.01,-102.04,36.99'
        return [{'lat': '40.58', 'lon': '-105.08', 'display_name': 'CO address', 'address': {'state': 'Colorado'}},
                {'lat': '32.35', 'lon': '-95.3', 'display_name': 'TX address', 'address': {'state': 'Texas'}}]
    monkeypatch.setattr(geo, 'request', fake)
    assert [r['label'] for r in prop.candidates(None, '123 Main Street')] == ['CO address']


def test_pdf_includes_evidence_and_compact_coverage_gaps(report):
    raw = build(report)
    pdf = PdfReader(io.BytesIO(raw))
    text = '\n'.join(p.extract_text() for p in pdf.pages)
    assert 'Property Hail History' in text and '1.75 inches' in text
    assert '2026-09-25 through 2026-09-27' in text
    assert 'not measured hail at the roof' in text
    assert any(a.get_object().get('/A', {}).get('/URI', '').startswith('https://noaa-mrms-pds')
               for page in pdf.pages for a in page.get('/Annots', []))
    assert len(pdf.pages) <= 2


def test_date_ranges_preserve_holes():
    assert date_ranges(['2026-01-01', '2026-01-02', '2026-01-04']) == '2026-01-01 through 2026-01-02, 2026-01-04'


def test_report_routes_require_signed_confirmation_and_ownership(report, monkeypatch):
    app = Flask(__name__); app.secret_key = 'test-secret'; app.config['TESTING'] = True
    register(app, None)
    users.create('rep1', password='example-password', role='rep')
    users.create('rep2', password='example-password', role='rep')
    monkeypatch.setattr(prop, 'candidates', lambda *_: [dict(CANDIDATE)])
    c = app.test_client()
    assert c.get('/api/hail/properties?q=123').status_code == 401
    with c.session_transaction() as session: session['username'] = 'rep1'
    token = c.get('/api/hail/properties?q=123').json['candidates'][0]['token']
    payload = dict(token=token, confirmed=True, lat=POINT[0], lng=POINT[1], days=3)
    assert c.post('/api/hail/reports', json={**payload, 'confirmed': False}).status_code == 400
    assert c.post('/api/hail/reports', json={**payload, 'token': 'forged'}).status_code == 400
    assert c.post('/api/hail/reports', json={**payload, 'lat': 32}).status_code == 400
    response = c.post('/api/hail/reports', json=payload)
    assert response.status_code == 201
    rid = response.json['id']
    assert c.get(f'/api/hail/reports/{rid}/pdf').data.startswith(b'%PDF')
    with c.session_transaction() as session: session['username'] = 'rep2'
    assert c.get(f'/api/hail/reports/{rid}').status_code == 404
    assert c.get(f'/api/hail/reports/{rid}/pdf').status_code == 404
    assert c.post('/api/hail/reports', json=payload).status_code == 403


def test_backfill_stops_before_disk_exhaustion(monkeypatch):
    monkeypatch.setattr(backfill.shutil, 'disk_usage', lambda _: SimpleNamespace(free=20 * 1024 * 1024))
    monkeypatch.setattr(backfill.ingest, 'swath_for', lambda *_: pytest.fail('Must not fetch with low disk'))
    with pytest.raises(RuntimeError, match='25 MB'):
        backfill.run([dt.date(2026, 9, 27)])
