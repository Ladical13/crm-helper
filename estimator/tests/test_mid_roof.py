"""Mid-roof inspections: looked up once per jurisdiction, remembered, and told
to everyone who needs it.

Some building departments want an inspector on the roof partway through, some
want only a final, and some want a mid-roof only for tile or battened roofs. A
crew that shingles past the window takes roofing back off to pass the final.

What these tests hold down:

- **The memory.** A jurisdiction is asked about once. The second job there
  reads the stored answer and the AI is not called again.
- **A manager's answer wins.** The AI never overwrites a confirmed record, not
  even one that lands after the lookup started.
- **It fails safe.** "Required" from the AI is trusted. "Not required" from
  the AI alone is printed as "office to confirm", because the costly mistake
  is skipping an inspection the city wanted. An answer with no authoritative
  citation is stored as unknown.
- **One verdict, three documents.** The permit packet, the work order and the
  rep's email say the same thing about one job.
"""
import io
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import app as A
import inspections as I
import jurisdiction_prompts as jp


JID = 'loveland'     # exists in the shipped jurisdictions.json


@pytest.fixture(autouse=True)
def memory_file(tmp_path, monkeypatch):
    """Every test gets its own memory file, so none inherits another's."""
    path = str(tmp_path / 'jurisdiction_inspections.json')
    monkeypatch.setattr(A, 'INSPECTIONS_FILE', path)
    monkeypatch.delenv('PERPLEXITY_API_KEY', raising=False)
    return path


@pytest.fixture
def rep_client(app):
    from portal import users as portal_users
    if not portal_users.get('nomgr'):
        portal_users.create('nomgr', password='x', role='rep', full_name='No Manager')
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = 'nomgr'
    return c


def _pdf_text(raw):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(raw))
    return '\n'.join(p.extract_text() or '' for p in r.pages)


def _signed(covering=('CertainTeed', 'Landmark'), jid=JID, **over):
    est = {
        'estimate_id': 'mr1-abc', 'estimate_number': 1042,
        'estimate_type': 'retail', 'selected_tier': 'better',
        'salesperson': 'bryan',
        'customer': {'name': 'Ada Lovelace', 'phone': '9705550100',
                     'address': {'street': '12 Analytical Way', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'signature': {'signed_at': '2026-09-01T10:00:00Z', 'selected_tier': 'better'},
        'measurements': {'roof_squares': 30, 'waste_pct': 10},
        'permit_jurisdiction': {'selected_id': jid} if jid else {},
        'trades': {'roofing': {'enabled': True, 'mode': 'simple',
                               'colors': ({'manufacturer': covering[0],
                                           'product_line': covering[1]}
                                          if covering else {}),
                               'line_items': [{'name': 'Roof', 'quantity': 32,
                                               'unit': 'SQ', 'cost': 142}]}},
    }
    est.update(over)
    return est


def _store(path, status='confirmed', **raw):
    base = {'mid_roof': 'required', 'timing': 'underlayment on, 25-75% installed'}
    base.update(raw)
    return I.put(path, JID, I.make_record(base, status, 'luke' if status == 'confirmed' else 'ai'))


def _pplx(answer, citations=('https://www.lovgov.org/building/reroof',)):
    return {'answer': '', 'data': answer, 'citations': list(citations), 'cached': False}


# ── Which roof is this ────────────────────────────────────────────────────

@pytest.mark.parametrize('name, kind', [
    ('CertainTeed Landmark (Architectural Shingle)', 'asphalt'),
    ('CertainTeed Northgate (Impact-Resistant Shingle)', 'asphalt'),
    ('IKO Nordic (Impact-Resistant Shingle)', 'asphalt'),
    ('CertainTeed Presidential Shake', 'asphalt'),   # a shingle, not a shake
    ('EDCO Steel Shingle', 'metal'),
    ('Stone-Coated Steel', 'stone_coated'),
    ('Standing Seam Metal (24ga)', 'metal'),
    ('PBR Exposed Fastener Metal (26ga)', 'metal'),
    ('Euroshield (Rubber)', 'synthetic'),
    ('DaVinci Synthetic Slate', 'synthetic'),
    ('Natural Vermont Slate', 'slate'),
    ('Concrete Tile', 'tile'),
    ('Cedar Shake', 'wood_shake'),
    ('TPO Membrane', 'low_slope'),
])
def test_the_covering_name_says_what_the_roof_is(name, kind):
    assert I.roof_kind(name) == kind


def test_a_name_that_does_not_say_is_not_guessed_to_be_asphalt():
    """Whether the crew stops for an inspector turns on this. A guess of
    asphalt would wave a tile roof through a tile-only rule."""
    assert I.roof_kind('Better Package') == ''
    assert I.roof_kind('') == ''


# ── Cleaning an answer ────────────────────────────────────────────────────

def test_a_garbled_answer_becomes_less_certain_never_more():
    rec = I.clean({'mid_roof': 'probably?', 'applies_to': ['tile'],
                   'source_url': 'javascript:alert(1)', 'photos_accepted': 'maybe'})
    assert rec['mid_roof'] == 'unknown'
    assert rec['applies_to'] == []          # only kept for a conditional rule
    assert rec['source_url'] == ''
    assert rec['photos_accepted'] is None


def test_a_conditional_rule_keeps_only_known_roof_kinds():
    rec = I.clean({'mid_roof': 'Conditional', 'applies_to': ['Tile', 'battens', 'gold leaf']})
    assert rec['mid_roof'] == 'conditional'
    assert rec['applies_to'] == ['tile', 'battens']


# ── The verdict for one job ───────────────────────────────────────────────

def test_nothing_known_says_ask_before_scheduling():
    v = I.resolve(None, 'asphalt')
    assert v['state'] == 'confirm'
    assert 'NOT CONFIRMED' in v['line']


def test_an_ai_required_is_trusted_as_required():
    """The worst a wrong 'required' costs is one phone call."""
    v = I.resolve(I.make_record({'mid_roof': 'required'}, 'ai', 'ai'), 'asphalt')
    assert v['state'] == 'required'
    assert v['line'].startswith('REQUIRED')
    assert 'not yet confirmed' in v['basis']


def test_an_ai_not_required_is_not_trusted_on_its_own():
    """A wrong 'not required' costs a tear-off at the final."""
    v = I.resolve(I.make_record({'mid_roof': 'not_required'}, 'ai', 'ai'), 'asphalt')
    assert v['state'] == 'confirm'
    assert 'office to confirm' in v['line']


def test_a_confirmed_not_required_is_not_required():
    v = I.resolve(I.make_record({'mid_roof': 'not_required'}, 'confirmed', 'luke'), 'asphalt')
    assert v['state'] == 'not_required'


@pytest.mark.parametrize('kind, state', [
    ('asphalt', 'not_required'),      # Douglas County: asphalt needs none
    ('tile', 'required'),
    ('stone_coated', 'required'),     # caught by "battens"
    ('', 'confirm'),                  # cannot tell what the roof is: ask
])
def test_a_rule_for_some_roofs_is_matched_against_this_roof(kind, state):
    rec = I.make_record({'mid_roof': 'conditional', 'applies_to': ['tile', 'slate', 'battens']},
                        'confirmed', 'luke')
    assert I.resolve(rec, kind)['state'] == state


# ── The memory ────────────────────────────────────────────────────────────

def test_the_ai_never_overwrites_a_confirmed_answer(memory_file):
    _store(memory_file, 'confirmed', mid_roof='not_required')
    I.put(memory_file, JID, I.make_record({'mid_roof': 'required'}, 'ai', 'ai'))
    assert I.get(memory_file, JID)['mid_roof'] == 'not_required'


def test_an_unknown_is_asked_again_only_after_a_while():
    rec = I.make_record({'mid_roof': 'unknown'}, 'ai', 'ai')
    assert not I.needs_lookup(rec)
    later = datetime.utcnow() + timedelta(days=I.UNKNOWN_RETRY_DAYS + 1)
    assert I.needs_lookup(rec, now=later)
    assert I.needs_lookup(None)
    assert not I.needs_lookup(I.make_record({'mid_roof': 'unknown'}, 'confirmed', 'x'), now=later)


def test_a_jurisdiction_is_looked_up_once_and_then_remembered(memory_file, monkeypatch):
    monkeypatch.setenv('PERPLEXITY_API_KEY', 'test')
    answer = _pplx({'mid_roof': 'required', 'timing': '25-75% installed',
                    'photos_accepted': True,
                    'source_url': 'https://www.lovgov.org/building/reroof'})
    with patch('agents.perplexity.search_json', return_value=answer) as call:
        first = A._mid_roof_for_estimate(_signed(), lookup=True)
        second = A._mid_roof_for_estimate(_signed(), lookup=True)
    assert call.call_count == 1
    assert first['state'] == second['state'] == 'required'
    assert I.get(memory_file, JID)['status'] == 'ai'


def test_the_inspection_question_is_its_own_call_with_a_closed_vocabulary(monkeypatch):
    """Bolting it onto the code-year prompt would risk that prompt's tuning."""
    monkeypatch.setenv('PERPLEXITY_API_KEY', 'test')
    with patch('agents.perplexity.search_json',
               return_value=_pplx({'mid_roof': 'unknown'})) as call:
        A._ensure_inspection_memory(JID)
    prompt = call.call_args[0][0]
    assert 'MID-ROOF' in prompt and 'adopted_code' not in prompt
    for k in I.ROOF_KINDS:
        assert k in prompt


def test_an_answer_with_no_official_citation_is_stored_as_unknown(memory_file, monkeypatch):
    monkeypatch.setenv('PERPLEXITY_API_KEY', 'test')
    blog = _pplx({'mid_roof': 'not_required'},
                 citations=['https://best-roofer-blog.com/loveland'])
    with patch('agents.perplexity.search_json', return_value=blog):
        A._ensure_inspection_memory(JID)
    assert I.get(memory_file, JID)['mid_roof'] == 'unknown'


def test_no_key_means_no_lookup_and_nothing_remembered(memory_file):
    with patch('agents.perplexity.search_json') as call:
        assert A._ensure_inspection_memory(JID) is None
    call.assert_not_called()
    assert I.get(memory_file, JID) is None


# ── The API the estimate's card uses ──────────────────────────────────────

def test_a_manager_answer_is_stored_as_confirmed(client, memory_file):
    r = client.put(f'/api/jurisdictions/{JID}/inspections',
                   json={'mid_roof': 'required', 'timing': '25-75% installed',
                         'photos_accepted': True})
    assert r.status_code == 200
    rec = I.get(memory_file, JID)
    assert rec['status'] == 'confirmed' and rec['updated_by'] == 'luke'


def test_confirming_nobody_knows_is_refused(client):
    r = client.put(f'/api/jurisdictions/{JID}/inspections', json={'mid_roof': 'unknown'})
    assert r.status_code == 400
    r = client.put(f'/api/jurisdictions/{JID}/inspections',
                   json={'mid_roof': 'conditional', 'applies_to': []})
    assert r.status_code == 400


def test_only_managers_write_or_clear(rep_client):
    assert rep_client.put(f'/api/jurisdictions/{JID}/inspections',
                          json={'mid_roof': 'required'}).status_code == 403
    assert rep_client.delete(f'/api/jurisdictions/{JID}/inspections').status_code == 403
    assert rep_client.post(f'/api/jurisdictions/{JID}/inspections/lookup',
                           json={'force': True}).status_code == 403


def test_a_rep_can_ask_about_a_jurisdiction_nobody_has(rep_client, monkeypatch):
    monkeypatch.setenv('PERPLEXITY_API_KEY', 'test')
    with patch('agents.perplexity.search_json',
               return_value=_pplx({'mid_roof': 'required'})):
        r = rep_client.post(f'/api/jurisdictions/{JID}/inspections/lookup', json={})
    assert r.status_code == 200 and r.get_json()['record']['mid_roof'] == 'required'


def test_a_lookup_on_a_confirmed_answer_is_refused(client, memory_file):
    _store(memory_file, 'confirmed')
    with patch('agents.perplexity.search_json') as call:
        r = client.post(f'/api/jurisdictions/{JID}/inspections/lookup', json={'force': True})
    assert r.status_code == 409
    call.assert_not_called()


def test_a_lookup_without_the_key_says_so(client):
    r = client.post(f'/api/jurisdictions/{JID}/inspections/lookup', json={})
    assert r.status_code == 503
    assert 'by hand' in r.get_json()['error']


def test_an_unknown_jurisdiction_is_a_404(client):
    assert client.get('/api/jurisdictions/atlantis/inspections').status_code == 404


def test_the_memory_is_in_the_backup():
    src = open(os.path.join(os.path.dirname(A.__file__), 'app.py'), encoding='utf-8').read()
    block = src[src.index('def _build_backup_zip'):]
    assert "'jurisdiction_inspections.json'" in block[:block.index('\ndef ', 10)]


# ── One verdict, three documents ──────────────────────────────────────────

def test_the_permit_packet_prints_the_mid_roof_requirement(memory_file):
    _store(memory_file, 'confirmed', photos_accepted=True, notes='Ladder must be on site.')
    txt = _pdf_text(A.build_permit_packet_pdf(_signed()))
    assert 'Mid-roof Inspection' in txt
    assert 'REQUIRED - underlayment on, 25-75% installed' in txt
    assert 'Accepted in place of a site visit' in txt
    assert 'Ladder must be on site.' in txt


def test_an_unknown_tells_the_office_to_ask_and_record_it():
    txt = _pdf_text(A.build_permit_packet_pdf(_signed()))
    assert 'NOT CONFIRMED' in txt
    assert 'record the answer' in txt


def test_the_work_order_tells_the_crew_in_both_languages(memory_file, monkeypatch):
    monkeypatch.setattr(A, '_bilingual_work_order', lambda: True)
    monkeypatch.setattr(A, '_work_order_notes_es', lambda t: '')
    _store(memory_file, 'confirmed')
    txt = _pdf_text(A.build_work_order_pdf(_signed()))
    assert 'REQUIRED - call the office before finishing the roof' in txt
    assert 'REQUERIDA' in txt
    assert '25-75% installed' in txt


def test_a_roof_the_rule_does_not_cover_says_not_required(memory_file):
    _store(memory_file, 'confirmed', mid_roof='conditional', applies_to=['tile', 'battens'])
    txt = _pdf_text(A.build_work_order_pdf(_signed()))
    assert 'Not required' in txt
    assert 'REQUIRED - call the office' not in txt


def test_a_repair_only_job_gets_no_mid_roof_line(monkeypatch):
    monkeypatch.setattr(A, '_is_report_only', lambda est: True)
    assert 'Mid-roof' not in _pdf_text(A.build_permit_packet_pdf(_signed()))


def test_a_job_with_no_roof_on_it_gets_no_line_and_no_email(outbox):
    """A gutters-only job asked to "confirm the mid-roof inspection" teaches
    everyone to ignore the email that matters."""
    est = _signed(trades={'gutters': {'enabled': True, 'mode': 'simple',
                                      'line_items': [{'name': 'Gutters', 'quantity': 120,
                                                      'unit': 'LF', 'cost': 9}]}})
    assert 'Mid-roof' not in _pdf_text(A.build_work_order_pdf(est))
    assert not A.send_inspection_heads_up(est)
    assert outbox == []


# ── Telling the rep ───────────────────────────────────────────────────────

@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def _fake(subject, html, to_addr, cc=None, attachments=None, bcc=None):
        sent.append({'subject': subject, 'html': html, 'to': to_addr, 'bcc': bcc})
        return True

    monkeypatch.setattr(A, '_send_email', _fake)
    monkeypatch.delenv('OWNER_NOTIFY_EMAIL', raising=False)
    monkeypatch.delenv('NOTIFY_CC', raising=False)
    return sent


def test_the_rep_is_told_when_it_is_required_and_the_owner_is_copied(outbox, memory_file):
    _store(memory_file, 'confirmed')
    assert A.send_inspection_heads_up(_signed())
    m = outbox[0]
    assert m['to'] == 'bryan@projectoneroofing.com'
    assert m['bcc'] == 'luke@projectoneroofing.com'
    assert 'required' in m['subject'] and 'Loveland' in m['subject']
    assert '25-75% installed' in m['html']


def test_the_rep_is_told_when_nobody_knows_yet(outbox):
    assert A.send_inspection_heads_up(_signed())
    assert 'Confirm mid-roof' in outbox[0]['subject']


def test_nothing_is_sent_when_it_is_confirmed_not_required(outbox, memory_file):
    _store(memory_file, 'confirmed', mid_roof='not_required')
    assert not A.send_inspection_heads_up(_signed())
    assert outbox == []


def test_repairs_and_the_demo_send_nothing(outbox, monkeypatch):
    monkeypatch.setattr(A, '_is_report_only', lambda est: True)
    assert not A.send_inspection_heads_up(_signed())
    monkeypatch.setattr(A, '_is_report_only', lambda est: False)
    monkeypatch.setattr(A.demo, 'is_demo_doc', lambda est: True)
    assert not A.send_inspection_heads_up(_signed())
    assert outbox == []


def test_the_prompt_template_formats():
    p = jp.build_inspection_prompt({'name': 'Loveland', 'kind': 'city'}, I.ROOF_KINDS)
    assert 'Jurisdiction: Loveland' in p and '"battens"' in p
