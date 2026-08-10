"""Orchestrator for one weekly strategist run.

``run(dry_run=True)`` follows the convention the prospector set in this repo:
it does the whole job, reports exactly what the real run would produce, and
**writes nothing** — no DB rows, no cache writes, and no live Perplexity spend
(research is served from cache or skipped). That makes a dry run genuinely
free and genuinely representative.

Failure policy: a run degrades rather than collapses. No Perplexity key, a
blown spend cap, an unreachable sitemap — each removes one input and is
recorded in the report's source table. Only a failed crawl of our own site
ends the run, because with no pages there is nothing to reason about.
"""
import json
import traceback

from .. import config
from . import crawl as crawler
from . import honesty, recommend, report, research


def _site_url():
    """Where to crawl. MARKETING_SITE_URL wins, else the marketing profile."""
    import os
    override = os.environ.get('MARKETING_SITE_URL', '').strip()
    if override:
        return override.rstrip('/')
    return config.load_marketing_profile()['company']['website'].rstrip('/')


# A run is a worker thread, so a process restart — a deploy, a dev-server
# reload, a crash — kills it mid-flight and leaves its row saying "running"
# forever. The UI then shows a run that never finishes and no reason why.
STALE_RUN_MINUTES = 30


def reap_stale_runs():
    """Mark abandoned runs as interrupted. Returns how many were reaped.

    Called before opening a run and before listing them, so the state is
    corrected wherever someone might look at it.
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(minutes=STALE_RUN_MINUTES)
              ).strftime('%Y-%m-%dT%H:%M:%SZ')
    with config.get_cache_db() as db:
        cur = db.execute(
            "UPDATE seo_runs SET status = 'interrupted', finished_at = ?, "
            "error = 'The process restarted while this run was in progress "
            "(deploy, reload or crash). Nothing was saved for it. Run again.' "
            "WHERE status = 'running' AND started_at < ?",
            (config.now_iso(), cutoff))
        db.commit()
        return cur.rowcount


def _open_run(mode):
    reap_stale_runs()
    with config.get_cache_db() as db:
        cur = db.execute(
            'INSERT INTO seo_runs (started_at, status, mode) VALUES (?, ?, ?)',
            (config.now_iso(), 'running', mode))
        db.commit()
        return cur.lastrowid


def _close_run(run_id, **fields):
    if run_id is None:
        return
    sets = ', '.join(f'{k} = ?' for k in fields)
    with config.get_cache_db() as db:
        db.execute(f'UPDATE seo_runs SET {sets}, finished_at = ? WHERE id = ?',
                   list(fields.values()) + [config.now_iso(), run_id])
        db.commit()


def _gather_research(dry_run):
    """Customer questions + competitor landscape per priority market.

    One city per market and one service per market, deliberately: at four
    markets × five services this would be twenty paid calls a week for a
    report nobody reads twenty sections of. Breadth can grow once the queue
    is being worked.
    """
    questions, competitors, notes = {}, {}, []
    cost = 0.0

    if dry_run:
        return questions, competitors, 'skipped (dry run — no live research)', 0.0

    services = recommend.approved_services()
    if not services:
        return questions, competitors, 'no approved services in the profile', 0.0
    service_key, service_label = services[0]     # roofing leads the profile

    for _label, examples in recommend.priority_markets():
        if not examples:
            continue
        city = examples[0]
        ctx = (city, service_key, service_label)
        try:
            q = research.customer_questions(service_label, city)
            questions[ctx] = q
            cost += q['cost_usd']
        except research.ResearchUnavailable as e:
            notes.append(f'{city}: {e}')
            continue
        try:
            c = research.competitor_landscape(city, service_label)
            competitors[ctx] = c
            cost += c['cost_usd']
        except research.ResearchUnavailable as e:
            notes.append(f'{city} competitors: {e}')

    if notes:
        return questions, competitors, 'partial — ' + '; '.join(notes[:3]), cost
    if not questions:
        return questions, competitors, 'no research returned', cost
    return questions, competitors, f'{len(questions)} market(s) researched', cost


def run(dry_run=False, max_pages=crawler.DEFAULT_MAX_PAGES, use_cache=True):
    """Do a full strategist pass. Returns a manifest; never raises."""
    mode = 'dry_run' if dry_run else 'live'
    run_id = None if dry_run else _open_run(mode)
    manifest = {'run_id': run_id, 'mode': mode, 'ok': False}

    try:
        base = _site_url()
        crawl_result = crawler.crawl_site(
            base, max_pages=max_pages, use_cache=use_cache,
            # A dry run must leave no trace, including in the crawl cache.
            write_cache=not dry_run)
        readable = [p for p in crawl_result['pages'] if not p.get('error')]
        if not readable:
            raise crawler.CrawlError(
                f'no readable pages at {base} — cannot build a strategy without '
                f'the site. Check the site is up and robots.txt is not blocking us.')

        questions, competitors, research_note, research_cost = _gather_research(dry_run)

        raw = recommend.build_all(crawl_result, questions, competitors)
        kept, dropped = honesty.filter_all(raw)
        for r in kept:
            r['score'] = recommend.score(r)
        kept.sort(key=lambda r: -r['score'])

        opportunities = recommend.topic_opportunities(questions, competitors, readable)
        plan = recommend.content_plan(opportunities, kept)

        run_row = {'started_at': config.now_iso(), 'mode': mode}
        markdown = report.render(run_row, crawl_result, kept, dropped,
                                 research_note, opportunities, plan)

        manifest.update({
            'ok': True,
            'opportunities': opportunities,
            'content_plan': plan,
            'base_url': base,
            'pages_crawled': len(crawl_result['pages']),
            'pages_readable': len(readable),
            'skipped_by_robots': len(crawl_result.get('skipped') or []),
            'robots_note': crawl_result.get('robots_note'),
            'sitemap_note': crawl_result.get('sitemap_note'),
            'research_note': research_note,
            'recommendations': kept,
            'dropped': [{'category': r.get('category'), 'reason': why}
                        for r, why in dropped],
            'cost_usd': round(research_cost, 4),
            'report_markdown': markdown,
        })

        if not dry_run:
            _persist(run_id, kept, markdown, manifest)
            _close_run(run_id, status='ok', pages_crawled=len(crawl_result['pages']),
                       recs_created=len(kept), cost_usd=round(research_cost, 4),
                       summary=f'{len(kept)} recommendations, '
                               f'{len(dropped)} dropped, {research_note}')
        return manifest

    except Exception as e:                                       # noqa: BLE001
        manifest['error'] = f'{type(e).__name__}: {e}'
        manifest['traceback'] = traceback.format_exc()
        if not dry_run:
            _close_run(run_id, status='error', error=manifest['error'][:500])
        return manifest


def _persist(run_id, recs, markdown, manifest):
    with config.get_cache_db() as db:
        for r in recs:
            db.execute(
                'INSERT INTO seo_recommendations (run_id, created_at, category, '
                'city, service, intent, action, rationale, evidence, confidence, '
                'evidence_basis, review_notes, score, status) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (run_id, config.now_iso(), r['category'], r.get('city', ''),
                 r.get('service', ''), r.get('intent', ''), r['action'],
                 r.get('rationale', ''), json.dumps(r.get('evidence') or []),
                 r.get('confidence', 'low'), r.get('evidence_basis', 'public_research'),
                 r.get('review_notes', ''), float(r.get('score') or 0), 'pending'))
        db.execute(
            'INSERT INTO seo_reports (run_id, created_at, week_of, markdown, stats) '
            'VALUES (?, ?, ?, ?, ?)',
            (run_id, config.now_iso(), report.week_of(), markdown,
             json.dumps({k: v for k, v in manifest.items()
                         if k in ('pages_crawled', 'pages_readable', 'cost_usd',
                                  'research_note', 'robots_note', 'sitemap_note')})))
        db.commit()


def set_status(rec_id, status, username):
    """Approve or reject one recommendation. Returns True when a row changed.

    Approval records intent only. Nothing in this package acts on an approved
    recommendation — a human still does the work.
    """
    if status not in ('pending', 'approved', 'rejected'):
        raise ValueError(f'unknown status {status!r}')
    with config.get_cache_db() as db:
        cur = db.execute(
            'UPDATE seo_recommendations SET status = ?, reviewed_by = ?, '
            'reviewed_at = ? WHERE id = ?',
            (status, username or '', config.now_iso(), rec_id))
        db.commit()
        return bool(cur.rowcount)
