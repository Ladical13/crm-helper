"""Read an organisation's own website for its contact details.

    python -m agents.b2b.site_contacts --limit 50 --dry-run
    python -m agents.b2b.site_contacts --limit 500

Free, and more reliable for emails than asking a model: a staff page states
"John Smith, Facilities - jsmith@grace.org" in plain HTML, and reading it
copies it exactly, where a model paraphrases and occasionally invents. Research
finds the website; this reads it.

Per lead: the homepage plus up to MAX_PAGES same-site pages whose link text or
path says contact / staff / team / about / leadership, one second apart, with
robots.txt honoured. Then, and only into EMPTY fields:

* email - an address ON THE ORGANISATION'S OWN DOMAIN. The named contact's own
  address if one matches their name, otherwise a shared inbox (info@) as a way
  in. Never a random staff member's personal address: the youth pastor is not
  who signs for a roof, and emailing him cold is how a rep gets reported.
* phone - a tel: link first (someone chose that number), else the first
  US-format number on a contact page.

Every lead it looks at is stamped site_checked_at, so a site is read at most
once a quarter.
"""
import argparse
import re
import sys
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib import robotparser

UA = 'ProjectOneRoofingBot/1.0 (+https://projectoneroofingcolorado.com; contact lookup)'
MAX_PAGES = 6
TIMEOUT = 10
PAUSE = 1.0
RECHECK_DAYS = 90
_KEYWORDS = ('contact', 'staff', 'team', 'about', 'leadership', 'directory', 'people',
             'who-we-are', 'our-church', 'administration', 'facilities', 'office')
_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE_RE = re.compile(r'(?<!\d)(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?([2-9]\d{2})[\s.-]?(\d{4})(?!\d)')
_JUNK_EMAIL = ('example.com', 'sentry', 'wixpress', 'domain.com', 'email.com', 'yourname',
               '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg')
# Colorado area codes. A number outside them on a "Colorado church" site means
# research found a same-named organisation somewhere else - Shepherd of the
# Hills in Austin (512), not Fort Collins - and dialling it is worse than
# having no number at all.
CO_AREA_CODES = ('303', '719', '720', '970', '983')
_ROLE = {'info', 'office', 'admin', 'contact', 'hello', 'church', 'frontdesk', 'reception',
         'mail', 'secretary', 'welcome', 'parish', 'general', 'inquiries', 'connect'}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.mailto, self.tel, self.text = [], [], [], []
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href') or ''
            low = href.lower()
            if low.startswith('mailto:'):
                self.mailto.append(href[7:].split('?')[0].strip())
            elif low.startswith('tel:'):
                self.tel.append(href[4:].strip())
            else:
                self._href = href
                self.links.append([href, ''])

    def handle_endtag(self, tag):
        if tag == 'a':
            self._href = None

    def handle_data(self, data):
        self.text.append(data)
        if self._href is not None and self.links:
            self.links[-1][1] += data


def _deobfuscate(text):
    """'jsmith [at] grace [dot] org' and friends, which churches love."""
    t = re.sub(r'\s*[\[\(\{]\s*at\s*[\]\)\}]\s*', '@', text, flags=re.I)
    return re.sub(r'\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*', '.', t, flags=re.I)


def _root(host):
    parts = (host or '').lower().split(':')[0].split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


def parse(html, base_url):
    """(emails, phones, candidate links) from one page."""
    p = _Page()
    try:
        p.feed(html or '')
    except Exception:
        pass
    text = _deobfuscate(' '.join(p.text))
    emails = [e.lower().strip('.') for e in p.mailto + _EMAIL_RE.findall(text)]
    emails = [e for e in dict.fromkeys(emails)
              if _EMAIL_RE.fullmatch(e) and not any(j in e for j in _JUNK_EMAIL)]
    phones = []
    for raw in p.tel + [m.group(0) for m in _PHONE_RE.finditer(text)]:
        m = _PHONE_RE.search(raw)
        if m:
            phones.append(f'({m.group(1)}) {m.group(2)}-{m.group(3)}')
    host = urlparse(base_url).netloc
    links = []
    for href, label in p.links:
        url = urljoin(base_url, href).split('#')[0]
        u = urlparse(url)
        if u.scheme not in ('http', 'https') or _root(u.netloc) != _root(host):
            continue
        hay = (u.path + ' ' + label).lower()
        if any(k in hay for k in _KEYWORDS):
            links.append(url)
    return emails, list(dict.fromkeys(phones)), list(dict.fromkeys(links))


def pick_email(emails, domain_root, first='', last=''):
    """The one email this lead should get, or ''. Own domain only; the named
    contact's address first, then a shared inbox; never another person's."""
    own = [e for e in emails if _root(e.split('@')[1]) == domain_root]
    first, last = (first or '').lower().strip(), (last or '').lower().split(' ')[-1].strip()
    if first or last:
        for e in own:
            local = re.sub(r'[^a-z]', '', e.split('@')[0])
            if (last and last in local) or (first and len(first) > 2 and local.startswith(first)):
                return e
    for e in own:
        if e.split('@')[0] in _ROLE:
            return e
    return ''


def read_site(website, fetch=None, sleep=time.sleep):
    """{'emails','phones','pages'} for one website. `fetch(url) -> html|None`
    is injectable for tests; the default honours robots.txt."""
    fetch = fetch or _fetcher(website)
    emails, phones, pages, seen, texts = [], [], [], set(), []
    queue = [website]
    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        html = fetch(url)
        if pages:
            sleep(PAUSE)
        if not html:
            continue
        pages.append(url)
        texts.append(html)
        e, ph, links = parse(html, url)
        emails += e
        phones += ph
        queue += [l for l in links if l not in seen]
    return {'emails': list(dict.fromkeys(emails)), 'phones': list(dict.fromkeys(phones)),
            'pages': pages, 'text': ' '.join(texts)[:3_000_000]}


def same_org(found, lead):
    """Does this website look like it belongs to THIS lead? It must mention the
    lead's city or Colorado somewhere. Research can land on a same-named
    organisation in another state, and nothing else here would notice."""
    text = (found.get('text') or '').lower()
    city = (lead.get('city') or '').strip().lower()
    return bool(text) and ((city and city in text) or 'colorado' in text
                           or re.search(r'\bco\s+8\d{4}\b', text) is not None)


def co_phone(p):
    d = re.sub(r'\D', '', p or '')
    return len(d) == 10 and d[:3] in CO_AREA_CODES


def _fetcher(website):
    import requests
    rp = robotparser.RobotFileParser()
    try:
        r = requests.get(urljoin(website, '/robots.txt'), timeout=TIMEOUT, headers={'User-Agent': UA})
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except Exception:
        rp.parse([])

    def fetch(url):
        if not rp.can_fetch(UA, url):
            return None
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={'User-Agent': UA})
            ctype = r.headers.get('content-type', '')
            if r.status_code != 200 or 'html' not in ctype:
                return None
            return r.text[:2_000_000]
        except Exception:
            return None
    return fetch


def apply(crm, lead, found, dry_run=False):
    """Fill empty email/phone from what the site says. Returns what was filled."""
    host = urlparse(lead['website']).netloc
    fill = {}
    if not same_org(found, lead):
        # Probably a different organisation with the same name: fill nothing,
        # and tell the rep, so they check the website research picked.
        with crm.get_db() as db:
            if not dry_run:
                db.execute('UPDATE leads SET site_checked_at=? WHERE id=?', (crm._now(), lead['id']))
                if found.get('pages'):
                    crm._log_activity(db, lead['id'], 'system', rep=lead['rep'],
                                      body=f"Website {lead['website']} never mentions "
                                           f"{lead.get('city') or 'Colorado'} - it may be a different "
                                           f"organisation. Nothing was taken from it; check it.")
        return {}
    if not (lead.get('email') or '').strip():
        e = pick_email(found['emails'], _root(host), lead.get('first_name'), lead.get('last_name'))
        if e:
            fill['email'] = e
    co = [p for p in found['phones'] if co_phone(p)]
    if not (lead.get('phone') or '').strip() and co:
        fill['phone'] = co[0]
    with crm.get_db() as db:
        supp = crm._suppression_index(db)
        if 'email' in fill and crm._suppressed_by(supp, '', crm._norm_email(fill['email']), ''):
            fill.pop('email')
        if 'phone' in fill and crm._suppressed_by(supp, crm._norm_phone(fill['phone']), '', ''):
            fill.pop('phone')
        if dry_run:
            return fill
        sets = dict(fill, site_checked_at=crm._now(), updated_at=crm._now())
        if 'email' in fill:
            sets['email_norm'] = crm._norm_email(fill['email'])
            sets['contact_source'] = 'research'
        if 'phone' in fill:
            sets['phone_norm'] = crm._norm_phone(fill['phone'])
        db.execute('UPDATE leads SET ' + ', '.join(f'{k}=?' for k in sets) + ' WHERE id=?',
                   list(sets.values()) + [lead['id']])
        crm._refresh_contact_quality(db, lead['id'])
        if fill:
            crm._log_activity(db, lead['id'], 'system', rep=lead['rep'],
                              body='From their website: ' + ', '.join(f'{k} {v}' for k, v in fill.items())
                                   + f" ({', '.join(found['pages'][:3])})")
    return fill


def candidates(crm, limit):
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(days=RECHECK_DAYS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with crm.get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM leads WHERE dnc = 0 AND website != '' AND (email = '' OR phone = '') "
            "AND (site_checked_at = '' OR site_checked_at < ?) "
            "ORDER BY contact_quality, icp_score DESC LIMIT ?", (cutoff, limit))]


def run(crm, limit=50, dry_run=False, log=print, fetch_factory=None):
    rows = candidates(crm, limit)
    log(f'{len(rows)} websites to read')
    emails = phones = 0
    for i, lead in enumerate(rows, 1):
        try:
            found = read_site(lead['website'],
                              fetch=fetch_factory(lead['website']) if fetch_factory else None)
            fill = apply(crm, lead, found, dry_run=dry_run)
        except Exception as e:                     # one broken site must not stop the run
            log(f'  [{i}] {lead.get("company")}: failed ({e})')
            continue
        emails += 'email' in fill
        phones += 'phone' in fill
        log(f'  [{i}/{len(rows)}] {lead.get("company")}: '
            + (', '.join(f'{k}={v}' for k, v in fill.items()) or f'nothing new ({len(found["pages"])} pages)'))
    log(f'Done: {emails} emails, {phones} phones filled' + (' (dry run)' if dry_run else ''))
    return {'emails': emails, 'phones': phones, 'seen': len(rows)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--limit', type=int, default=50)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    import portal.wsgi  # noqa: F401  - loads the CRM as p1_crm_app
    run(sys.modules['p1_crm_app'], limit=args.limit, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
