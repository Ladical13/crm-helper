"""The RoofR / Xactimate PDF import, from the rep's iPhone.

Three separate things in this path were true about a desktop browser and not
about iOS, and none of them errored anywhere a test could see — the picker
opened, the rep tapped their report, and nothing happened. Each is pinned
here against the specific iOS behaviour that breaks it.
"""
import io
import re
import os

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(HERE, rel), encoding='utf-8') as fh:
        return fh.read()


# ── 1. The accept attribute ────────────────────────────────────────────────
# iOS resolves `accept` to UTIs to decide what is selectable in the Files
# picker. A bare `.pdf` extension is handled inconsistently across iOS
# versions; the MIME type is not. The photo input on this same page has always
# spelled both, which is the working precedent this follows.

@pytest.mark.parametrize('input_id', ['roofr-pdf-input', 'xact-pdf-input'])
def test_pdf_inputs_accept_the_mime_type_not_only_the_extension(input_id):
    html = _read('static/index.html')
    m = re.search(r'<input[^>]*id="%s"[^>]*>' % re.escape(input_id), html)
    assert m, f'{input_id} is gone — if it moved, move this test with it'
    tag = m.group(0)
    accept = re.search(r'accept="([^"]*)"', tag)
    assert accept, f'{input_id} has no accept at all'
    values = {v.strip() for v in accept.group(1).split(',')}
    assert 'application/pdf' in values, (
        f'{input_id} accepts {sorted(values)} — without the MIME type iOS can '
        'grey out every PDF in the Files picker, and the rep taps their '
        'report and nothing happens')


# ── 2. The File handle iOS is allowed to take back ─────────────────────────
# Both importers held the File from <input type=file> until the rep tapped
# Apply — a minute later, across a saveEstimate() round-trip — to upload it as
# an attachment. Clearing the input in between releases WebKit's backing store,
# so that later read comes back empty: the parse looks fine and the report
# silently never lands in the customer file.

def test_importers_snapshot_the_bytes_before_clearing_the_input():
    js = _read('static/app.js')
    for fn in ('importRoofrPdf', 'importXactPdf'):
        body = re.search(r'async function %s\(input\) \{(.*?)\n\}' % fn, js, re.S)
        assert body, f'{fn} is gone or changed shape'
        src = body.group(1)
        assert 'snapshotPickedFile(input)' in src, (
            f'{fn} no longer snapshots the picked file. Reading '
            'input.files[0] and holding it past input.value = "" is the iOS '
            'bug this replaced.')
        assert 'input.files[0]' not in src, (
            f'{fn} reads input.files[0] directly again')
        assert "input.value = ''" not in src, (
            f'{fn} clears the input itself again — snapshotPickedFile clears '
            'it AFTER the read, which is the whole point')


def test_snapshot_helper_clears_only_after_reading():
    js = _read('static/app.js')
    body = re.search(r'async function snapshotPickedFile\(input\) \{(.*?)\n\}\n', js, re.S)
    assert body, 'snapshotPickedFile is gone'
    src = body.group(1)
    assert src.index('arrayBuffer()') < src.index("input.value = ''"), (
        'the input is cleared before the bytes are read, which is the iOS '
        'failure this function exists to prevent')
    # It must still clear, or a rep cannot re-pick the same file after a
    # failed parse — `change` does not fire twice for one value.
    assert src.count("input.value = ''") == 2, (
        'both the success and the failure path must clear the input')


def test_attachment_upload_sends_the_snapshot_blob():
    js = _read('static/app.js')
    sends = re.findall(r"ufd\.append\('file'[^)]*\)", js)
    assert sends, 'the attachment uploads are gone'
    for send in sends:
        assert 'file.blob' in send and 'file.name' in send, (
            f'{send} — a Blob has no filename of its own, so the name has to '
            'ride along as the third argument or the report is stored nameless')


# ── 3. The server gate ─────────────────────────────────────────────────────
# `f.filename.lower().endswith('.pdf')` is a claim about what iOS chose to
# call the file, not about the file. A report picked from iCloud Drive or a
# share sheet does not reliably carry its extension.

PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'


def _post(client, path, data, name):
    return client.post(path, data={'file': (io.BytesIO(data), name)},
                       content_type='multipart/form-data')


@pytest.mark.parametrize('path', ['/api/parse-roofr', '/api/parse-xactimate'])
def test_a_real_pdf_with_no_extension_is_not_rejected_as_not_a_pdf(client, path):
    """It will fail to PARSE — it is a stub, not a report — but it must get
    past the gate and fail on its content, which is a different message and a
    different fix for whoever reads it."""
    r = _post(client, path, PDF, 'report')
    body = r.get_json()
    assert 'not a PDF' not in (body.get('error') or ''), (
        f'{path} rejected a real PDF for its filename: {body}')
    assert 'Please choose a PDF' not in (body.get('error') or '')


@pytest.mark.parametrize('path', ['/api/parse-roofr', '/api/parse-xactimate'])
def test_a_file_that_is_not_a_pdf_is_still_refused(client, path):
    r = _post(client, path, b'this is not a pdf at all', 'report.pdf')
    assert r.status_code == 400
    assert 'not a PDF' in r.get_json()['error'], (
        'a .pdf name must not talk the server into parsing whatever it is')


@pytest.mark.parametrize('path', ['/api/parse-roofr', '/api/parse-xactimate'])
def test_an_empty_upload_says_so_rather_than_blaming_the_parser(client, path):
    """This is what a released iOS file handle looks like on this end.
    'Could not read PDF: EOF' sends the next reader into the parser."""
    r = _post(client, path, b'', 'report.pdf')
    assert r.status_code == 400
    assert 'empty' in r.get_json()['error'].lower()


@pytest.mark.parametrize('path', ['/api/parse-roofr', '/api/parse-xactimate'])
def test_no_file_at_all_is_still_a_clean_error(client, path):
    r = client.post(path, data={}, content_type='multipart/form-data')
    assert r.status_code == 400
    assert r.get_json()['error']
