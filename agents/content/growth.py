"""Campaign landing pages and aggregate visits; customer data stays in the CRM."""
import json
import re
import sqlite3
import time
import uuid
from contextlib import closing

from .. import config
from . import media, posts, studio

STARTERS = {
    'roofing': {'title': 'Make sense of your roofing options',
                'intro': 'A clear scope makes it easier to decide what your roof needs. Tell us what you are seeing and what you would like help understanding.',
                'points': ['Compare the same materials and scope across estimates.', 'Ask how flashing, ventilation and cleanup are included.', 'Discuss your questions before choosing the next step.'],
                'cta': 'Request a roof inspection', 'service': 'roofing'},
    'windows': {'title': 'Start with the room that feels uncomfortable',
                'intro': 'Tell us where you notice drafts or window problems. We can discuss your project and help you decide what to evaluate next.',
                'points': ['Note which rooms and windows are affected.', 'Describe when you notice the problem.', 'Bring questions about scope, materials and installation.'],
                'cta': 'Discuss my window project', 'service': 'windows'},
    'property': {'title': 'Plan your property’s next exterior project',
                 'intro': 'Start a conversation about your building, priorities and planning timeline. Share the service you need and the questions your team is working through.',
                 'points': ['List the areas that need attention.', 'Gather existing inspection and maintenance records.', 'Agree on a comparable scope before collecting bids.'],
                 'cta': 'Discuss a property project', 'service': 'roofing'},
}


def unpack(row):
    return {**dict(row), 'detail': json.loads(row['detail'])}


def pages(campaign_id):
    studio.campaign(campaign_id)
    with closing(config.get_cache_db()) as db:
        return [unpack(r) for r in db.execute('SELECT * FROM marketing_pages WHERE campaign_id=? ORDER BY created_at', (campaign_id,))]


def get(page_id):
    with closing(config.get_cache_db()) as db:
        row = db.execute('SELECT * FROM marketing_pages WHERE id=?', (page_id,)).fetchone()
    if not row:
        raise LookupError('Page not found')
    return unpack(row)


def published(slug):
    with closing(config.get_cache_db()) as db:
        row = db.execute('SELECT * FROM marketing_pages WHERE slug=? AND published=1', (slug,)).fetchone()
    if not row:
        raise LookupError('Page not found')
    return unpack(row)


def create(campaign_id, template, username):
    campaign = studio.campaign(campaign_id)
    if template not in STARTERS:
        raise ValueError('Choose a page template')
    pid = uuid.uuid4().hex
    detail = {**STARTERS[template], 'market': campaign['brief']['market'], 'owner': username,
              'hero_id': '', 'followup_hours': 4, 'lead_type': 'commercial' if template == 'property' else 'homeowner'}
    with closing(config.get_cache_db()) as db, db:
        db.execute('INSERT INTO marketing_pages(id,campaign_id,slug,detail,created_at) VALUES (?,?,?,?,?)',
                   (pid, campaign_id, template + '-' + pid[:10], json.dumps(detail), config.now_iso()))
    return get(pid)


def update(page_id, data):
    from portal import users
    with closing(config.get_cache_db()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM marketing_pages WHERE id=?', (page_id,)).fetchone()
        if not row:
            raise LookupError('Page not found')
        old = unpack(row)
        if type(data.get('revision')) is not int or data['revision'] != old['revision']:
            raise studio.Conflict('Page changed. Reload before saving.')
        detail = dict(old['detail'])
        for key, limit in [('title', 150), ('intro', 1600), ('cta', 100), ('market', 100), ('owner', 100), ('hero_id', 40)]:
            detail[key] = studio._text(data.get(key, detail.get(key, '')), limit, key not in ('hero_id',))
        if not users.get(detail['owner']):
            raise ValueError('Choose an existing team member to receive inquiries')
        detail['service'] = data.get('service', detail['service'])
        if detail['service'] not in studio.SERVICES:
            raise ValueError('Choose a supported service')
        detail['lead_type'] = data.get('lead_type', detail['lead_type'])
        if detail['lead_type'] not in ('homeowner', 'commercial', 'hoa', 'property_manager'):
            raise ValueError('Choose a supported audience')
        detail['points'] = data.get('points', detail['points'])
        if not isinstance(detail['points'], list) or not 1 <= len(detail['points']) <= 6:
            raise ValueError('Add one to six helpful points')
        detail['points'] = [studio._text(p, 350, True) for p in detail['points']]
        detail['followup_hours'] = data.get('followup_hours', detail['followup_hours'])
        if type(detail['followup_hours']) is not int or not 1 <= detail['followup_hours'] <= 72:
            raise ValueError('Follow-up target must be 1–72 hours')
        slug = studio._text(data.get('slug', old['slug']), 80, True)
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
            raise ValueError('Use lowercase words separated by hyphens for the page address')
        if old['published'] and slug != old['slug']:
            raise ValueError('Unpublish before changing the address; existing links will stop working')
        live = data.get('published', bool(old['published']))
        if type(live) is not bool:
            raise ValueError('Publication must be true or false')
        if detail['hero_id']:
            hero = media.get(detail['hero_id'])
            if hero['kind'] != 'image' or not hero['cleared'] or not hero['alt']:
                raise ValueError('Choose a cleared image with an accessible description')
        if live:
            public_text = '\n'.join([detail['title'], detail['intro'], detail['cta'], *detail['points']])
            problems = posts._vet(public_text, 'facebook', {'hard_limit': 6000, 'label': 'Landing page'})
            if problems:
                raise ValueError('; '.join(problems))
        try:
            db.execute('UPDATE marketing_pages SET slug=?,detail=?,published=?,revision=revision+1 WHERE id=?',
                       (slug, json.dumps(detail), int(live), page_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError('That page address is already in use') from exc
    return get(page_id)


def attribution(page, args):
    source = args.get('utm_source', 'direct')
    # Fixed labels prevent unbounded rows and personal data in aggregate counts.
    if source not in ('facebook', 'instagram', 'linkedin', 'google_business', 'blog', 'email', 'direct', 'call', 'dm'):
        source = 'other'
    raw = str(args.get('utm_content', ''))
    draft_id = int(raw) if raw.isdigit() and len(raw) < 12 else 0
    if draft_id:
        with closing(config.get_cache_db()) as db:
            if not db.execute('SELECT 1 FROM marketing_posts WHERE draft_id=? AND campaign_id=?',
                              (draft_id, page['campaign_id'])).fetchone():
                draft_id = 0
    return {'source': source, 'draft_id': draft_id, 'campaign_id': page['campaign_id'], 'page_id': page['id']}


def visit(page, attr):
    with closing(config.get_cache_db()) as db, db:
        db.execute('INSERT INTO marketing_visits VALUES (?,?,?,?,1) '
                   'ON CONFLICT(page_id,day,source,draft_id) DO UPDATE SET views=views+1',
                   (page['id'], config.now_iso()[:10], attr['source'], attr['draft_id']))


def views(campaign_id):
    with closing(config.get_cache_db()) as db:
        return [dict(r) for r in db.execute('SELECT v.source,v.draft_id,SUM(v.views) views FROM marketing_visits v '
                'JOIN marketing_pages p ON p.id=v.page_id WHERE p.campaign_id=? GROUP BY v.source,v.draft_id', (campaign_id,))]


def allow(bucket, limit=10, seconds=3600):
    """SQLite-backed rate budget shared by workers; caller hashes the client IP."""
    now = int(time.time())
    with closing(config.get_cache_db()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM marketing_rate_limits WHERE started<?', (now - 86400,))
        row = db.execute('SELECT * FROM marketing_rate_limits WHERE bucket=?', (bucket,)).fetchone()
        count = row['count'] if row and row['started'] > now - seconds else 0
        if count >= limit:
            return False
        db.execute('INSERT OR REPLACE INTO marketing_rate_limits VALUES (?,?,?)',
                   (bucket, row['started'] if count else now, count + 1))
    return True
