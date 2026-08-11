"""Extract page metadata and structured data from HTML.

Uses the stdlib ``html.parser`` rather than BeautifulSoup. The extraction here
is shallow — title, meta, headings, links, JSON-LD — and adding a dependency
for that would sit badly in a repo that vendored Leaflet specifically to avoid
a third-party runtime dependency.

Everything returned is an **observation about our own page**, which is why
recommendations built from it may be stated as fact. Nothing here infers
anything about Google.
"""
import json
from html.parser import HTMLParser


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''
        self.meta_description = ''
        self.meta_robots = ''
        self.canonical = ''
        self.h1 = []
        self.h2 = []
        self.links = []            # (href, anchor text)
        self.images = 0
        self.images_missing_alt = 0
        self.jsonld_raw = []
        self.has_viewport = False
        self._stack = []
        self._buf = []
        self._in_jsonld = False
        self._jsonld_buf = []
        self._text = []
        self._noise_depth = 0      # inside <script>/<style>: not visible text
        self.root_container = ''   # an empty SPA mount point, if present

    # ── tags ──
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style'):
            self._noise_depth += 1
        if tag == 'div' and (a.get('id') or '').lower() in ('root', 'app', '__next'):
            self.root_container = a.get('id')
        if a.get('data-reactroot') is not None:
            self.root_container = self.root_container or 'data-reactroot'
        if tag in ('title', 'h1', 'h2'):
            self._stack.append(tag)
            self._buf = []
        elif tag == 'meta':
            name = (a.get('name') or '').lower()
            if name == 'description':
                self.meta_description = (a.get('content') or '').strip()
            elif name == 'robots':
                self.meta_robots = (a.get('content') or '').strip().lower()
            elif name == 'viewport':
                self.has_viewport = True
        elif tag == 'link' and (a.get('rel') or '').lower() in ('canonical', "['canonical']"):
            self.canonical = (a.get('href') or '').strip()
        elif tag == 'a':
            href = (a.get('href') or '').strip()
            if href:
                self._stack.append('a')
                self._buf = []
                self.links.append([href, ''])
        elif tag == 'img':
            self.images += 1
            if not (a.get('alt') or '').strip():
                self.images_missing_alt += 1
        elif tag == 'script' and (a.get('type') or '').lower() == 'application/ld+json':
            self._in_jsonld = True
            self._jsonld_buf = []

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self._noise_depth:
            self._noise_depth -= 1
        if tag == 'script' and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld_raw.append(''.join(self._jsonld_buf))
            return
        if self._stack and self._stack[-1] == tag:
            text = ' '.join(''.join(self._buf).split())
            self._stack.pop()
            if tag == 'title' and not self.title:
                self.title = text
            elif tag == 'h1':
                self.h1.append(text)
            elif tag == 'h2':
                self.h2.append(text)
            elif tag == 'a' and self.links:
                self.links[-1][1] = text
            self._buf = []

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._stack:
            self._buf.append(data)
        # Script and style bodies are not page content. Counting them was
        # inflating word_count with minified JS.
        if not self._noise_depth:
            self._text.append(data)


def _parse_jsonld(blocks):
    """Return the @type values found in JSON-LD, plus anything unparseable.

    A malformed block is reported rather than swallowed — broken structured
    data is itself a finding worth surfacing.
    """
    types, broken = [], 0
    for raw in blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            broken += 1
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            t = node.get('@type')
            for one in (t if isinstance(t, list) else [t]):
                if one:
                    types.append(str(one))
            for sub in (node.get('@graph') or []):
                if isinstance(sub, dict) and sub.get('@type'):
                    st = sub['@type']
                    for one in (st if isinstance(st, list) else [st]):
                        types.append(str(one))
    return types, broken


def extract(html, url=''):
    """Parse one page. Never raises — a page we can't parse is still a datapoint."""
    p = _Extractor()
    try:
        p.feed(html or '')
        p.close()
    except Exception:                                            # noqa: BLE001
        pass

    schema_types, broken_jsonld = _parse_jsonld(p.jsonld_raw)
    text = ' '.join(''.join(p._text).split())

    internal, external = [], []
    for href, _anchor in p.links:
        if href.startswith(('mailto:', 'tel:', 'javascript:', '#')):
            continue
        (external if href.startswith(('http://', 'https://')) and
         url and not href.startswith(url.split('/')[0] + '//' + url.split('/')[2])
         else internal).append(href)

    # A page whose served HTML is an empty mount point is assembled in the
    # browser. A static crawler sees the shell, not the page — so every
    # content-derived judgement about it would be wrong. Detecting this is
    # what stops the strategist confidently reporting "no H1" on forty pages
    # whose H1s are rendered by JavaScript.
    client_rendered = bool(p.root_container) and len(text.split()) < 50 \
        and not p.h1

    return {
        'client_rendered': client_rendered,
        'root_container': p.root_container,
        'title': p.title,
        'title_length': len(p.title),
        'meta_description': p.meta_description,
        'meta_description_length': len(p.meta_description),
        'meta_robots': p.meta_robots,
        'canonical': p.canonical,
        'h1': p.h1,
        'h1_count': len(p.h1),
        'h2': p.h2[:20],
        'word_count': len(text.split()),
        'images': p.images,
        'images_missing_alt': p.images_missing_alt,
        'has_viewport': p.has_viewport,
        'schema_types': sorted(set(schema_types)),
        'broken_jsonld_blocks': broken_jsonld,
        'internal_links': internal[:200],
        'external_link_count': len(external),
        'anchor_texts': [a for _h, a in p.links if a][:100],
    }


# ── Structured-data expectations for a local contractor ─────────────────────
# Google's own guidance for local businesses. These are the types worth having;
# their absence is an observation about our markup, not a claim about ranking.
EXPECTED_SCHEMA = {
    'home':    ['LocalBusiness', 'RoofingContractor', 'Organization'],
    'service': ['Service', 'LocalBusiness', 'RoofingContractor'],
    'faq':     ['FAQPage'],
}


def missing_schema(page, kind='home'):
    """Which expected schema types are absent. Empty list = nothing to say."""
    present = {t.lower() for t in (page.get('schema_types') or [])}
    wanted = EXPECTED_SCHEMA.get(kind, [])
    if any(w.lower() in present for w in wanted):
        return []
    return wanted
