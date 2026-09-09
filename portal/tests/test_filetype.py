"""The shared content sniffer.

Exists because every upload path in the repo used to gate on the filename's
extension, and iOS routinely hands over files without one — so reps were told a
genuine PDF was not a PDF.
"""
from portal import filetype as ft

PDF  = b'%PDF-1.7\n1 0 obj\n'
PNG  = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF'
GIF  = b'GIF89a\x01\x00'
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 '
HEIC = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00'
ZIP  = b'PK\x03\x04\x14\x00\x00\x00'


def test_each_type_is_recognised_by_its_bytes():
    assert ft.sniff_ext(PDF)  == '.pdf'
    assert ft.sniff_ext(PNG)  == '.png'
    assert ft.sniff_ext(JPEG) == '.jpg'
    assert ft.sniff_ext(GIF)  == '.gif'
    assert ft.sniff_ext(WEBP) == '.webp'
    assert ft.sniff_ext(HEIC) == '.heic'


def test_a_pdf_header_behind_leading_junk_still_counts():
    """Readers tolerate bytes ahead of %PDF- and so do we — a strict byte-0
    check would reject files every PDF viewer opens."""
    assert ft.looks_like_pdf(b'\n\n   ' + PDF)
    assert ft.sniff_ext(b'garbage' * 20 + PDF) == '.pdf'


def test_a_header_past_the_window_does_not_count():
    assert not ft.looks_like_pdf(b'\x00' * (ft.MAGIC_WINDOW + 8) + PDF)


def test_zip_family_gets_no_opinion():
    """.docx, .xlsx and .zip are all PK\\x03\\x04. Guessing one would mislabel
    the other two, so the sniffer abstains and the filename decides."""
    assert ft.sniff_ext(ZIP) == ''


def test_nothing_and_junk_are_answered_honestly():
    assert ft.sniff_ext(b'') == ''
    assert ft.sniff_ext(None) == ''
    assert ft.sniff_ext(b'this is just prose') == ''
    assert not ft.looks_like_pdf(b'')


def test_resolve_prefers_the_filename():
    """The name is right almost always, and for the zip formats it is the only
    thing that CAN be right — so an upload that resolved before this function
    existed still resolves to exactly the same extension."""
    assert ft.resolve_ext('estimate.docx', ZIP, {'.docx', '.pdf'}) == '.docx'
    assert ft.resolve_ext('sheet.xlsx', ZIP, {'.xlsx'}) == '.xlsx'
    assert ft.resolve_ext('report.PDF', PDF, {'.pdf'}) == '.pdf'


def test_resolve_falls_back_to_the_bytes():
    """The iOS case: Files, a share sheet or a mail attachment hands over a
    name with no extension at all."""
    assert ft.resolve_ext('document', PDF, {'.pdf'}) == '.pdf'
    assert ft.resolve_ext('', JPEG, {'.jpg', '.pdf'}) == '.jpg'
    assert ft.resolve_ext(None, PNG, {'.png'}) == '.png'


def test_resolve_refuses_what_is_not_allowed():
    assert ft.resolve_ext('note.txt', b'hello', {'.pdf'}) == ''
    assert ft.resolve_ext('document', PNG, {'.pdf'}) == ''      # sniffed, not allowed
    assert ft.resolve_ext('thing.exe', b'MZ\x90\x00', {'.pdf'}) == ''


def test_a_misnamed_file_is_still_caught_by_its_bytes():
    """Naming a text file report.pdf does not make it one — the filename tier
    only accepts extensions in the allow-list, and .txt here is not."""
    assert ft.resolve_ext('report.txt', b'not a pdf at all', {'.pdf'}) == ''
