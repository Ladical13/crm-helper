"""Uploaded documents: what happens to them, and what happens when they go.

The rows live in salescrm.db and are backed up nightly. The FILES live on the
volume and are not — which is the worst shape a backup gap can take, because a
restore looks like it worked and every attachment 404s the first time somebody
opens a lead.
"""
import io
import os

from conftest import signup, login, new_lead
import app as appmod


def _upload(client, lead_id, name='claim.pdf', body=b'%PDF-1.4 test'):
    return client.post(f'/api/leads/{lead_id}/documents',
                       data={'file': (io.BytesIO(body), name)},
                       content_type='multipart/form-data')


def test_a_document_round_trips(client):
    signup(client)
    lead = new_lead(client)
    assert _upload(client, lead['id']).status_code == 201
    docs = client.get(f'/api/leads/{lead["id"]}/documents').get_json()
    assert [d['orig_name'] for d in docs] == ['claim.pdf']
    assert client.get(docs[0]['url'].replace('/api', '/api')).data == b'%PDF-1.4 test'


def test_the_on_disk_name_is_never_exposed(client):
    """It is a UUID, and knowing it is the only thing between a guessed URL and
    somebody else's signed contract."""
    signup(client)
    lead = new_lead(client)
    _upload(client, lead['id'])
    doc = client.get(f'/api/leads/{lead["id"]}/documents').get_json()[0]
    assert 'filename' not in doc


def test_another_rep_cannot_download_it(client):
    signup(client, 'luke')                          # manager
    signup(client, 'casey')
    login(client, 'casey')
    mine = new_lead(client)
    _upload(client, mine['id'])
    doc = client.get(f'/api/leads/{mine["id"]}/documents').get_json()[0]
    signup(client, 'dana')
    login(client, 'dana')
    assert client.get(doc['url']).status_code == 403


def test_deleting_a_lead_takes_its_files_with_it(client):
    """They used to be left behind: rows pointing at a lead that no longer
    exists, and files sitting on the volume forever."""
    signup(client)
    lead = new_lead(client)
    _upload(client, lead['id'])
    with appmod.get_db() as db:
        stored = db.execute('SELECT filename FROM documents').fetchone()['filename']
    on_disk = os.path.join(appmod.DOCS_DIR, stored)
    assert os.path.exists(on_disk)

    client.delete(f'/api/leads/{lead["id"]}')
    with appmod.get_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM documents').fetchone()['c'] == 0
    assert not os.path.exists(on_disk)


def test_deleting_a_partner_leaves_their_referrals_findable(client):
    """A partner's referrals outlive the partner. The pointer is cleared rather
    than left aimed at a lead that is gone."""
    signup(client)
    partner = new_lead(client, lead_type='realtor')
    child = new_lead(client, referred_by=partner['id'])
    client.delete(f'/api/leads/{partner["id"]}')
    assert client.get(f'/api/leads/{child["id"]}').get_json()['referred_by'] == ''


def test_an_unsupported_file_type_is_refused(client):
    signup(client)
    lead = new_lead(client)
    assert _upload(client, lead['id'], name='payload.exe').status_code == 400


# ── The backup gap ───────────────────────────────────────────────────────────

def test_the_backup_counts_what_it_is_not_carrying(client):
    """Invisible is the dangerous part. The nightly email names the number of
    files it is NOT carrying, for the same reason it prints row counts."""
    from portal import backup as pbackup
    signup(client)
    lead = new_lead(client)
    _upload(client, lead['id'], body=b'x' * 2048)
    _path, count, total = pbackup.document_store()
    assert count == 1 and total == 2048
    assert 'NOT in this zip' in pbackup._documents_note(count, total)


def test_the_documents_zip_can_identify_what_it_holds(client):
    """On disk a document is a UUID. Without the manifest a restore is several
    hundred files nobody can match to a customer."""
    import json
    import zipfile
    from portal import backup as pbackup
    signup(client)
    lead = new_lead(client)
    _upload(client, lead['id'], name='adjuster-letter.pdf')

    data, manifest = pbackup.build_documents_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        got = json.loads(zf.read('manifest.json'))
    assert any(n.startswith('documents/') for n in names)
    assert got['documents'][0]['orig_name'] == 'adjuster-letter.pdf'
    assert got['documents'][0]['lead_id'] == lead['id']
    assert got['documents'][0]['present'] is True
