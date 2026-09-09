"""What an uploaded file IS, decided by its bytes rather than by its name.

Every upload path in this repo used to gate on the filename's extension. That
is a property of whatever handed us the file, not of the file — and iOS hands
over files with no usable extension whenever they come from anywhere but a
plain download: the Files app, a share sheet, iCloud, Google Drive, a text
message. Reps were told a genuine carrier estimate was "not a PDF", with
nothing they could do about it, because the name had lost its `.pdf`.

The filename stays authoritative when it carries a known extension. These
helpers are the FALLBACK for when it doesn't — never a replacement, because of
the zip problem below.

**The zip abstention.** `.docx`, `.xlsx`, `.pptx` and `.zip` are all
`PK\\x03\\x04`; nothing in the leading bytes tells them apart, and reading the
archive's contents to guess is a lot of machinery to answer a question the
filename already answers correctly. `sniff_ext` returns '' for them rather than
picking one. A caller that allows office documents must therefore consult the
filename FIRST and fall back to sniffing, not the other way round.
"""
import os

# Readers tolerate junk ahead of a PDF's header, so scan a window rather than
# demanding byte 0. Also the window every other check below reads from.
MAGIC_WINDOW = 1024


def looks_like_pdf(raw):
    """True when these bytes open a PDF."""
    return b'%PDF-' in (raw or b'')[:MAGIC_WINDOW]


def _is_png(head):
    return head.startswith(b'\x89PNG\r\n\x1a\n')


def _is_jpeg(head):
    return head.startswith(b'\xff\xd8\xff')


def _is_gif(head):
    return head.startswith((b'GIF87a', b'GIF89a'))


def _is_webp(head):
    # RIFF container whose form type is WEBP: 'RIFF' <4-byte size> 'WEBP'.
    return head.startswith(b'RIFF') and head[8:12] == b'WEBP'


# ISO base-media brands that mean "this is a HEIF still", as written by every
# iPhone since the format became the camera default. `mif1`/`msf1` are the
# generic image/sequence brands Apple also emits.
_HEIF_BRANDS = {b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1',
                b'heim', b'heis', b'avif'}


def _is_heif(head):
    # 4-byte box size, then 'ftyp', then the major brand.
    return head[4:8] == b'ftyp' and head[8:12] in _HEIF_BRANDS


# Ordered most-specific first. Each entry is (extension, predicate).
_SNIFFERS = (
    ('.png',  _is_png),
    ('.jpg',  _is_jpeg),
    ('.gif',  _is_gif),
    ('.webp', _is_webp),
    ('.heic', _is_heif),
)


def sniff_ext(raw):
    """The extension these bytes deserve ('.pdf', '.jpg', …), or '' when they
    don't identify a type we can name confidently.

    '' is a real answer, not a failure: see the zip abstention above. Callers
    treat it as "the bytes have no opinion" and fall back to the filename.
    """
    head = (raw or b'')[:MAGIC_WINDOW]
    if not head:
        return ''
    if looks_like_pdf(head):
        return '.pdf'
    for ext, matches in _SNIFFERS:
        if matches(head):
            return ext
    return ''


def resolve_ext(filename, raw, allowed):
    """Decide an upload's extension, or '' if it isn't an allowed type.

    `allowed` is a set of dotted, lowercase extensions ({'.pdf', '.jpg', …}).

    Filename first — it is right almost always, and it is the ONLY thing that
    can tell a .docx from a .xlsx. Bytes second, for the iOS case where the
    name arrived without one. Deliberately additive: any upload that worked
    before this function existed still resolves to exactly the extension it
    used to.
    """
    ext = os.path.splitext(filename or '')[1].lower()
    if ext in allowed:
        return ext
    sniffed = sniff_ext(raw)
    return sniffed if sniffed in allowed else ''
