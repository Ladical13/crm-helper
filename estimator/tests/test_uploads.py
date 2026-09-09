"""The attachment endpoint, /api/uploads/<est_id>.

Both PDF imports POST the report here after parsing it, to file it in the
customer record. It used to gate on the filename's extension, so on an iOS pick
with no extension the import parsed, applied, reported success — and the PDF
never landed. Silently, because both callers wrap this in a catch that only
console.warns. Fixing the parse endpoints without fixing this leaves the flow
half-broken on the same device.
"""
import io
import os

PDF  = b'%PDF-1.7\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n'
JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF' + b'\x00' * 32
PNG  = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + b'\x00' * 32


def _post(client, name, blob, est_id='est-upload-test'):
    return client.post(f'/api/uploads/{est_id}',
                       data={'file': (io.BytesIO(blob), name)},
                       content_type='multipart/form-data')


def test_a_pdf_with_no_extension_is_stored_as_a_pdf(client):
    """The reported iOS failure. 'document' carries no extension; the bytes say
    PDF, so it is one — and it must be STORED as .pdf, since the customer view
    and the printed estimate key their PDF rendering off that."""
    r = _post(client, 'document', PDF)
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()['filename'].endswith('.pdf')


def test_a_photo_with_no_extension_keeps_its_real_type(client):
    r = _post(client, 'image', JPEG)
    assert r.status_code == 201
    assert r.get_json()['filename'].endswith('.jpg')
    r = _post(client, 'IMG_0042', PNG)
    assert r.status_code == 201
    assert r.get_json()['filename'].endswith('.png')


def test_a_normal_filename_still_wins(client):
    """Strictly additive: anything that resolved before still resolves the
    same way, so no existing attachment path changes behaviour."""
    r = _post(client, 'Report Summary.pdf', PDF)
    assert r.status_code == 201
    assert r.get_json()['filename'].endswith('.pdf')


def test_the_file_on_disk_is_the_file_that_was_sent(client):
    """The handler reads the stream to sniff it, so it must write those bytes
    rather than re-saving an already-consumed stream — that lands a 0-byte
    attachment that looks completely normal in the list."""
    r = _post(client, 'document', PDF)
    stored = r.get_json()['filename']
    import app as A
    with open(os.path.join(A.UPLOADS_DIR, stored), 'rb') as fh:
        assert fh.read() == PDF


def test_a_pdf_upload_still_gets_page_images(client):
    """Rasterizing is keyed off the resolved extension, so an extensionless
    PDF has to reach it too — otherwise the attachment renders as a bare link
    instead of a document."""
    r = _post(client, 'document', PDF)
    assert 'pages' in r.get_json()          # [] on a stub PDF, but the key proves the branch ran


def test_an_unknown_type_is_still_refused(client):
    r = _post(client, 'notes.txt', b'just some prose')
    assert r.status_code == 400
    r = _post(client, 'payload', b'MZ\x90\x00binary')
    assert r.status_code == 400


def test_an_empty_upload_says_so(client):
    r = _post(client, 'document.pdf', b'')
    assert r.status_code == 400
    assert 'empty' in r.get_json()['error'].lower()
