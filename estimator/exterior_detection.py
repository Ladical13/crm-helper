"""Optional SAM 3 surface selection through fal's queue API.

No API key or customer photo is exposed to the browser beyond the original
photo the rep already uploaded. Disabled until EXTERIOR_AUTO_DETECT=1 AND
FAL_KEY are configured. No network call occurs at import time.

Schema: https://fal.ai/models/fal-ai/sam-3/image/api
Queue: https://fal.ai/docs/documentation/model-apis/inference/queue
"""
import base64
import io
import os
import re
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageChops, ImageOps

MODEL_URL = 'https://queue.fal.run/fal-ai/sam-3/image'
# SAM 3 returns one mask per detected object and ``combine_masks`` unions them
# into the role's single saved mask. Keep fascia in the siding request so the
# customer's siding choice also covers the horizontal trim at the roof edge,
# without creating a fourth billable inference request or a separate UI layer.
PROMPTS = {
    'roof': 'roof',
    'siding': 'exterior wall siding, fascia boards',
    'door': 'entry door',
}
MAX_BYTES = 4 * 1024 * 1024
MAX_SIDE = 1600
FAL_PRIVATE_HEADERS = {
    # The photo is sent inline in the JSON payload. fal otherwise retains
    # request inputs/outputs for its dashboard history by default.
    'X-Fal-Store-IO': '0',
    # Belt-and-suspenders for any media object the endpoint may materialize.
    'X-Fal-Object-Lifecycle-Preference': '{"expiration_duration_seconds":3600}',
}


class DetectionError(Exception):
    """A safe, user-facing message, never an upstream body or credential."""


def configured():
    return (os.environ.get('EXTERIOR_AUTO_DETECT', '').lower() in ('1', 'true', 'yes')
            and bool(os.environ.get('FAL_KEY', '').strip()))


def decode_image(uri):
    if not isinstance(uri, str) or len(uri) > MAX_BYTES * 4 // 3 + 100:
        raise ValueError('Image must be smaller than 4 MB.')
    if not re.match(r'^data:image/(png|jpeg|webp);base64,', uri):
        raise ValueError('Use a PNG, JPEG, or WebP image.')
    try:
        data = base64.b64decode(uri.split(',', 1)[1], validate=True)
        if len(data) > MAX_BYTES:
            raise ValueError('Image must be smaller than 4 MB.')
        with Image.open(io.BytesIO(data)) as img:
            if max(img.size) > MAX_SIDE or min(img.size) < 1:
                raise ValueError('Image dimensions must not exceed 1600 pixels.')
            img.load()
            return img.copy()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Image could not be decoded.') from exc


def _json(method, url, payload=None):
    """Only our fixed endpoint / signed queue URLs reach this function."""
    try:
        headers = {'Authorization': 'Key ' + os.environ['FAL_KEY']}
        if method == 'POST' and url == MODEL_URL:
            headers.update(FAL_PRIVATE_HEADERS)
        with requests.request(method, url, json=payload,
                              headers=headers,
                              timeout=(5, 15), allow_redirects=False, stream=True) as response:
            if response.status_code not in (200, 202):
                raise DetectionError('The detection service is unavailable. Check its key and usage allowance.')
            # Bound upstream data too: max eight masks at input resolution.
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 16 * 1024 * 1024:
                    raise DetectionError('The detection response was too large.')
                chunks.append(chunk)
            import json
            result = json.loads(b''.join(chunks))
            if not isinstance(result, dict):
                raise ValueError('Expected an object')
            return result
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise DetectionError('Could not contact the detection service. Your photo and edits are unchanged.') from exc


def _queue_url(url, request_id):
    # Use returned URLs: subpath routing can differ between hosted models.
    # Never send our API credential to redirects, a client URL, or another host.
    parsed = urlsplit(url if isinstance(url, str) else '')
    if (parsed.scheme != 'https' or parsed.netloc != 'queue.fal.run'
            or parsed.query or parsed.fragment
            or not re.fullmatch(r'/fal-ai/sam-3(?:/image)?/requests/'
                                + re.escape(request_id) + r'(?:/status|/response)?', parsed.path)):
        raise DetectionError('The detection service returned an invalid job address.')
    return url


def submit(role, image_uri):
    if role not in PROMPTS:
        raise ValueError('Unknown surface.')
    img = decode_image(image_uri)
    # Re-encode to strip metadata before sending a customer photo off-site.
    clean = ImageOps.exif_transpose(img).convert('RGB')
    buf = io.BytesIO()
    clean.save(buf, 'JPEG', quality=90)
    uri = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
    result = _json('POST', MODEL_URL, {
        'image_url': uri, 'prompt': PROMPTS[role], 'apply_mask': False,
        'sync_mode': True, 'output_format': 'png', 'return_multiple_masks': True,
        'max_masks': 8, 'include_scores': True,
    })
    rid = result.get('request_id', '')
    if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', rid):
        raise DetectionError('The detection service returned an invalid job.')
    return {'request_id': rid, 'width': clean.width, 'height': clean.height,
            'status_url': _queue_url(result.get('status_url'), rid),
            'response_url': _queue_url(result.get('response_url'), rid)}


def poll(job):
    status = _json('GET', _queue_url(job['status_url'], job['request_id']))
    if status.get('status') in ('IN_QUEUE', 'IN_PROGRESS'):
        return {'status': 'pending'}
    if status.get('status') != 'COMPLETED' or status.get('error'):
        raise DetectionError('Surface detection failed. Your existing selections were kept.')
    result = _json('GET', _queue_url(job['response_url'], job['request_id']))
    return combine_masks(result, (job['width'], job['height']))


def combine_masks(result, size):
    masks = result.get('masks')
    if not isinstance(masks, list) or len(masks) > 8:
        raise DetectionError('The detection service returned invalid masks.')
    combined = Image.new('L', size, 0)
    scores = result.get('scores') or []
    if not isinstance(scores, list):
        raise DetectionError('The detection service returned invalid confidence scores.')
    count = 0
    for index, item in enumerate(masks):
        # Low confidence is not a usable selection. No made-up fallback region.
        score = scores[index] if index < len(scores) else None
        if isinstance(score, (int, float)) and score < 0.5:
            continue
        try:
            img = decode_image(item.get('url') if isinstance(item, dict) else None)
        except ValueError as exc:
            raise DetectionError('The detection service returned an unreadable mask.') from exc
        if img.size != size:
            raise DetectionError('The detected mask does not match the photo dimensions.')
        # apply_mask=False returns white foreground / black background. Convert
        # luminance into alpha; copying an opaque B/W image as a canvas mask
        # would otherwise recolor the entire house and sky.
        gray = img.convert('L').point(lambda value: 255 if value >= 128 else 0)
        gray = ImageChops.multiply(gray, img.convert('RGBA').getchannel('A'))
        if gray.getbbox():
            combined = ImageChops.lighter(combined, gray)
            count += 1
    if not count:
        return {'status': 'not_found', 'count': 0}
    rgba = Image.new('RGBA', size, 'white')
    rgba.putalpha(combined)
    buf = io.BytesIO()
    rgba.save(buf, 'PNG')
    return {'status': 'complete', 'count': count,
            'mask': 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()}
