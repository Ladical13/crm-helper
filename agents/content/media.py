"""Private marketing assets. Images are decoded and re-encoded without metadata."""
import io
import uuid
import warnings
from contextlib import closing
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .. import config
from .studio import SERVICES, _text


def directory():
    path = Path(config.data_dir()) / 'marketing-media'
    path.mkdir(parents=True, exist_ok=True)
    return path


def get(media_id):
    with closing(config.get_cache_db()) as db:
        row = db.execute('SELECT * FROM marketing_media WHERE id=?', (media_id,)).fetchone()
    if not row:
        raise LookupError('Asset not found')
    return dict(row)


def listing():
    with closing(config.get_cache_db()) as db:
        return [dict(r) for r in db.execute('SELECT * FROM marketing_media ORDER BY created_at DESC,id LIMIT 500')]


def save(upload, data, username):
    title = _text(data.get('title', ''), 150, True)
    alt = _text(data.get('alt', ''), 500)
    rights = _text(data.get('rights', ''), 500)
    cleared = data.get('cleared') in ('true', True)
    origin = data.get('origin', 'project')
    if origin not in ('project', 'graphic', 'illustration', 'licensed'):
        raise ValueError('Choose the asset source')
    service = _text(data.get('service', ''), 40)
    if service and service not in SERVICES:
        raise ValueError('Choose a supported service')
    if cleared and not rights:
        raise ValueError('Record permission or usage rights before clearing an asset')
    city = _text(data.get('city', ''), 100)
    project = _text(data.get('project', ''), 120)
    phase = _text(data.get('phase', ''), 30)
    raw = upload.read(25 * 1024 * 1024 + 1)
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError('Files must be 25 MB or smaller')
    # MP4 clips are stored as originals and downloadable; no transcoding claimed.
    if len(raw) > 12 and raw[4:8] == b'ftyp' and str(upload.filename).lower().endswith('.mp4'):
        kind, suffix, content = 'video', '.mp4', raw
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as source:
                    if source.format not in ('JPEG', 'PNG', 'WEBP') or source.width * source.height > 30_000_000:
                        raise ValueError('Use a JPEG, PNG or WebP image up to 30 megapixels')
                    pic = ImageOps.exif_transpose(source).convert('RGB')
                    pic.thumbnail((4096, 4096))
                    output = io.BytesIO()
                    pic.save(output, format='PNG')
                    content = output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError('Use a valid JPEG, PNG, WebP image or MP4 clip') from exc
        kind, suffix = 'image', '.png'
    mid = uuid.uuid4().hex
    filename = mid + suffix
    path = directory() / filename
    path.write_bytes(content)
    try:
        with closing(config.get_cache_db()) as db, db:
            db.execute('INSERT INTO marketing_media VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (mid, filename, title, kind, origin, alt, service, city, project, phase,
                        rights, int(cleared), config.now_iso(), username))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return get(mid)


def validate_ids(db, ids, cleared=False):
    if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(i, str) for i in ids):
        raise ValueError('Choose up to 20 library assets')
    for mid in ids:
        item = db.execute('SELECT * FROM marketing_media WHERE id=?', (mid,)).fetchone()
        if not item or (cleared and not item['cleared']):
            raise ValueError('Every attached asset must exist and be cleared for use')
    return list(dict.fromkeys(ids))
