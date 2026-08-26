"""Orchestrates a per-rep B2B lead-generation run.

Flow (mirrors the plan doc):
  1. Load ``territories.json`` for the requested rep.
  2. For each segment × each city in their territory:
       * Try the free source(s) first via ``sources.pullers_for(segment)``.
       * Fall through to Perplexity gap-fill if free returns nothing.
  3. Rank by ICP score. Sources set a reachability base score in
     normalization; ``enrich`` adds storm/decision-maker nudges afterwards,
     so this sort decides WHO gets enriched, not the final order.
  4. Enrich the top-N (config: ``enrich_top_n``).
  5. Push in one batch through ``ingest.import_via_test_client(...)`` so
     salescrm's dedupe / suppression / DNC / batch / cadence all fire.
  6. Annotate the imported/deduped leads with research + storm text.
  7. Write a run manifest.

Nothing here touches ``salescrm.db`` directly, except the ``annotate_leads``
call which is a targeted UPDATE of Nimbus's own columns.
"""
import json
import os
import sys
from datetime import datetime

from .. import config
from .. import ingest as ingest_mod
from . import enrich as enrich_mod
from . import sources as sources_mod


def _now():
    return config.now_iso()


def _rep_config(rep):
    territories = config.load_territories()
    cfg = territories.get(rep)
    if not cfg:
        raise ValueError(f'No territory config for rep "{rep}". '
                         f'Known: {sorted(territories)}')
    return cfg


def _open_run(agent, rep, summary='', dry_run=False):
    with config.get_cache_db() as db:
        cur = db.execute(
            'INSERT INTO agent_runs (agent, rep, started_at, status, summary, '
            "dry_run) VALUES (?, ?, ?, 'running', ?, ?)",
            (agent, rep or '', _now(), summary, 1 if dry_run else 0))
        run_id = cur.lastrowid
        db.commit()
    return run_id


def _close_run(run_id, *, status, found=0, pushed=0, deduped=0, cost=0.0,
               error='', summary=''):
    with config.get_cache_db() as db:
        db.execute(
            'UPDATE agent_runs SET finished_at = ?, status = ?, '
            'leads_found = ?, leads_pushed = ?, leads_deduped = ?, cost_usd = ?, '
            'error = ?, summary = ? WHERE id = ?',
            (_now(), status, int(found), int(pushed), int(deduped),
             float(cost), error, summary, run_id))
        db.commit()


def _write_manifest(rep, run_id, manifest):
    fn = f'{datetime.utcnow().strftime("%Y-%m-%d")}_{rep or "content"}_{run_id}.json'
    path = os.path.join(config.runs_dir(), fn)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, sort_keys=True, default=str)
    return path


def run(rep, *, segments=None, cities=None, per_city_limit=10,
        enrich_top_n=None, dry_run=False, client=None, model=None):
    """Run one B2B pass for ``rep``.

    ``client`` is a Werkzeug ``Client`` on the composed application, already
    carrying the caller's admin session. The Nimbus blueprint builds one from
    the request; the CLI wraps ``portal.wsgi.application`` and pre-signs it
    in as ``PORTAL_USER`` + ``PORTAL_PASSWORD``.

    Returns the manifest dict.
    """
    cfg = _rep_config(rep)
    display_name = cfg.get('display_name') or rep
    segments = segments or cfg.get('segments') or []
    cities   = cities   or cfg.get('cities')   or []
    counties = cfg.get('counties') or []
    enrich_top_n = int(enrich_top_n if enrich_top_n is not None
                       else cfg.get('enrich_top_n', 40))
    per_city_limit = int(per_city_limit or 10)

    summary = f'{display_name} — {len(segments)} segment(s) x {len(cities)} city/cities'
    run_id = _open_run('b2b', rep, summary=summary, dry_run=dry_run)

    manifest = {
        'run_id': run_id, 'rep': rep, 'display_name': display_name,
        'started_at': _now(), 'segments': list(segments), 'cities': list(cities),
        'counties': list(counties), 'per_city_limit': per_city_limit,
        'enrich_top_n': enrich_top_n, 'dry_run': bool(dry_run),
        'events': [], 'errors': [],
    }

    try:
        # 1-2. Pull candidates.
        all_rows = []
        for segment in segments:
            pullers = sources_mod.pullers_for(segment)
            for city in cities or ['']:
                got = _pull_segment_city(segment, city, counties, pullers,
                                        per_city_limit, model=model,
                                        events=manifest['events'],
                                        errors=manifest['errors'])
                for r in got:
                    r.setdefault('_segment', segment)
                    all_rows.append(r)

        manifest['found'] = len(all_rows)

        # 3. Rank.
        all_rows.sort(key=lambda r: (-int(r.get('icp_score') or 0),
                                     r.get('company', '')))

        # 4. Enrich top-N.
        enriched = []
        for r in all_rows[:enrich_top_n]:
            try:
                e = enrich_mod.enrich_one(r, r.get('_segment', 'gc'), model=model)
            except Exception as ex:                                  # noqa: BLE001
                manifest['errors'].append({
                    'phase': 'enrich', 'company': r.get('company'),
                    'error': str(ex)[:200]})
                e = dict(r)
                e['research_notes'] = ''
                e['research_citations'] = []
                e['recent_storm'] = ''
                e['enrichment_cost'] = 0.0
            enriched.append(e)
        # Anything past top-N carries through un-enriched so the rep still
        # gets a full pipeline; enrichment is a scoring boost, not a filter.
        candidates = enriched + list(all_rows[enrich_top_n:])
        manifest['enriched'] = len(enriched)

        # 5. Push.
        push_results = {}
        if client is None:
            # CLI or test caller — write the file but skip the import so the
            # dispatcher stays useful when the Flask app isn't loaded.
            manifest['events'].append({'note': 'no client provided; skipping push'})
        else:
            # One batch per segment (so the salescrm importer's lead_type stays
            # coherent).
            by_segment = {}
            for c in candidates:
                by_segment.setdefault(c.get('_segment', 'gc'), []).append(c)
            batch_stub = f'nimbus:b2b:{rep}:{run_id}'
            for segment, rows in by_segment.items():
                cleaned = [_strip_nimbus_fields(r) for r in rows]
                try:
                    res = ingest_mod.import_via_test_client(
                        client, cleaned,
                        lead_type=segment, source=f'nimbus:b2b:{segment}',
                        rep=rep, batch=f'{batch_stub}:{segment}',
                        dry_run=dry_run)
                    push_results[segment] = res
                except Exception as ex:                              # noqa: BLE001
                    manifest['errors'].append({
                        'phase': 'push', 'segment': segment,
                        'error': str(ex)[:200]})
                    continue

                # 6. Annotate. Match the API response's "details" list back
                # onto the rows we just pushed to know each lead_id (both
                # freshly inserted AND deduped-onto-existing).
                lead_ids = _lead_ids_from_response(client, batch=f'{batch_stub}:{segment}',
                                                   rep=rep)
                updates = []
                for enriched_row in rows[:len(lead_ids)]:
                    updates.append({
                        'lead_id': lead_ids[rows.index(enriched_row)],
                        'research_notes': enriched_row.get('research_notes', ''),
                        'research_citations': enriched_row.get('research_citations', []),
                        'recent_storm': enriched_row.get('recent_storm', ''),
                    })
                # In-process annotate via the loaded salescrm module.
                salescrm_mod = sys.modules.get('p1_salescrm_app')
                if salescrm_mod is not None and updates and not dry_run:
                    try:
                        ingest_mod.annotate_leads(salescrm_mod, updates)
                    except Exception as ex:                          # noqa: BLE001
                        manifest['errors'].append({
                            'phase': 'annotate', 'error': str(ex)[:200]})

        manifest['pushed'] = sum((r['counts'].get('inserted') or 0)
                                 for r in push_results.values())
        manifest['deduped'] = sum((r['counts'].get('duplicate') or 0)
                                  for r in push_results.values())
        manifest['batches'] = {seg: r.get('batches') for seg, r in push_results.items()}

        # 7. Cost total for this run.
        cost = sum(float(r.get('enrichment_cost') or 0.0) for r in enriched)
        # Add gap-fill query costs too.
        for ev in manifest['events']:
            cost += float(ev.get('cost') or 0.0)
        manifest['cost_usd'] = round(cost, 4)

        _close_run(run_id, status='ok',
                   found=manifest['found'], pushed=manifest.get('pushed', 0),
                   deduped=manifest.get('deduped', 0), cost=cost,
                   summary=summary)
    except Exception as ex:                                          # noqa: BLE001
        _close_run(run_id, status='error', error=str(ex)[:400], summary=summary)
        manifest['fatal_error'] = str(ex)
        raise
    finally:
        manifest['finished_at'] = _now()
        manifest['manifest_path'] = _write_manifest(rep, run_id, manifest)

    return manifest


def _pull_segment_city(segment, city, counties, pullers, per_city_limit,
                       *, model, events, errors):
    """Try each puller in order; stop at the first non-empty result."""
    for puller in pullers:
        try:
            rows = puller(city=city, county=(counties[0] if counties else ''),
                          state='CO', limit=per_city_limit, segment=segment,
                          model=model, reason=f'b2b:{segment}:{city}') \
                if puller.__module__.endswith('perplexity_gap') \
                else puller(city=city, county=(counties[0] if counties else ''),
                            state='CO', limit=per_city_limit)
        except TypeError:
            # Free-source stubs have a simpler signature.
            rows = puller(city=city, county=(counties[0] if counties else ''),
                          state='CO', limit=per_city_limit)
        except Exception as ex:                                      # noqa: BLE001
            errors.append({'phase': 'pull', 'segment': segment,
                           'city': city, 'source': puller.__module__,
                           'error': str(ex)[:200]})
            continue
        events.append({'phase': 'pull', 'segment': segment, 'city': city,
                       'source': puller.__module__, 'rows': len(rows or [])})
        if rows:
            return rows
    return []


def _strip_nimbus_fields(row):
    """Drop keys the salescrm importer doesn't know about.

    Research/storm columns live outside the importer's field list; annotate
    them after the import commits (see ``ingest.annotate_leads``).
    """
    drop = {'_segment', 'research_notes', 'research_citations', 'recent_storm',
            'enrichment_cost'}
    return {k: v for k, v in row.items() if k not in drop}


def _lead_ids_from_response(client, batch, rep):
    """Fetch the freshly-imported lead ids for annotation."""
    r = client.get(f'/crm/api/leads?rep={rep}&batch={batch}&limit=500')
    if r.status_code != 200:
        # Batch filter isn't a public param — fall back to listing rep leads
        # and matching import_batch server-side is out of scope here.
        return []
    data = r.get_json() or []
    return [x.get('id') for x in data if x.get('id')]
