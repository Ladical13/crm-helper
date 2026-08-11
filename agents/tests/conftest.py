"""Isolate every agents/ test in its own scratch data dir.

The SEO fixtures live here rather than in a test module because two test files
need them (``test_seo.py`` and ``test_seo_brief.py``) and a fixture imported
across test modules is exactly what conftest is for.
"""
import json
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_agents_dir(monkeypatch, tmp_path):
    d = tmp_path / 'nimbus'
    d.mkdir()
    monkeypatch.setenv('AGENTS_DATA_DIR', str(d))
    monkeypatch.delenv('PERPLEXITY_API_KEY', raising=False)
    yield d


# ── A tiny fake website, for the SEO crawler ────────────────────────────────

GOOD_HTML = '''<!doctype html><html><head>
<title>Roof Replacement in Fort Collins | Project One Roofing</title>
<meta name="description" content="Storm damage roof replacement across Northern Colorado, from inspection through the insurance claim and the final walkthrough of your new roof.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://example.com/roofing">
<script type="application/ld+json">{"@type":"RoofingContractor","name":"Project One"}</script>
</head><body>
<h1>Roof Replacement in Fort Collins</h1>
<h2>What to expect</h2>
<p>%s</p>
<a href="/siding">Siding services</a>
<img src="a.jpg" alt="A finished roof">
</body></html>''' % (' word' * 400)

BARE_HTML = '''<!doctype html><html><head></head><body>
<h1>One</h1><h1>Two</h1>
<img src="x.jpg">
<a href="/a">click here</a><a href="/b">read more</a><a href="/c">learn more</a>
<script type="application/ld+json">{ this is not json </script>
</body></html>'''

# A client-rendered shell — what projectoneroofingcolorado.com actually serves.
SPA_HTML = ('<!doctype html><html><head><title>Project One</title></head>'
            '<body><div id="root"></div>'
            '<script>var a=1;/* lots of minified js words here to inflate */</script>'
            '</body></html>')


class FakeResponse:
    def __init__(self, text='', status=200, ctype='text/html'):
        self.text = text
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = {'Content-Type': ctype}

    def json(self):
        return json.loads(self.text)


@pytest.fixture
def fake_web(monkeypatch):
    """Serve a tiny fake site. Returns the list of URLs actually fetched."""
    from agents.seo import crawl

    pages = {
        'https://example.com/robots.txt': FakeResponse(
            'User-agent: *\nDisallow: /private\n', ctype='text/plain'),
        'https://example.com/sitemap.xml': FakeResponse(
            '<urlset><url><loc>https://example.com/roofing</loc></url>'
            '<url><loc>https://example.com/bare</loc></url>'
            '<url><loc>https://example.com/private/secret</loc></url></urlset>',
            ctype='application/xml'),
        'https://example.com/roofing': FakeResponse(GOOD_HTML),
        'https://example.com/bare': FakeResponse(BARE_HTML),
    }
    calls = []

    class FakeRequests:
        @staticmethod
        def get(url, **kw):
            calls.append(url)
            if url in pages:
                return pages[url]
            return FakeResponse('', status=404)

    monkeypatch.setattr(crawl, 'requests', FakeRequests)
    monkeypatch.setattr(crawl, 'CRAWL_DELAY', 0)     # keep the suite fast
    return calls


@pytest.fixture
def site_profile(monkeypatch):
    monkeypatch.setenv('MARKETING_SITE_URL', 'https://example.com')
