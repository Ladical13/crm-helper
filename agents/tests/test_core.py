"""Core invariants for the agents/ package.

Focused on the non-obvious edges — a cache TTL that quietly grows, a spend
cap that doesn't hard-stop, a JSON parser that swallows a code fence and
returns a raw string, a source_ref generator whose collision rate makes
re-runs stop being idempotent.
"""
import json
from unittest.mock import patch

import pytest


def test_config_seeds_default_territories_on_first_read():
    from agents import config
    t = config.load_territories()
    assert set(t) >= {'avery', 'phil', 'derik', 'bryan'}
    for u, cfg in t.items():
        assert isinstance(cfg.get('counties'), list)
        assert isinstance(cfg.get('segments'), list)
        assert cfg.get('monthly_lead_cap', 0) > 0
        assert cfg.get('enrich_top_n', 0) > 0


def test_settings_merge_defaults_over_disk():
    from agents import config
    config.save_settings({'monthly_spend_cap_usd': 12})
    s = config.load_settings()
    assert s['monthly_spend_cap_usd'] == 12
    # A key we didn't write must still resolve to the default.
    assert 'perplexity_model' in s


def test_spend_cap_blocks_further_live_calls():
    from agents import config, perplexity as ppx
    config.save_settings({'monthly_spend_cap_usd': 0.01})
    # Pre-load the ledger over the cap.
    with config.get_cache_db() as db:
        db.execute("INSERT INTO spend_ledger (occurred_at, source, cost_usd) "
                   "VALUES (?, 'perplexity', 1.0)", (config.now_iso(),))
        db.commit()
    with pytest.raises(ppx.SpendCapReached):
        ppx.search('anything', reason='test')


def test_cache_hit_is_free(monkeypatch):
    from agents import config, perplexity as ppx
    # Seed a hit.
    key = ppx._hash('sonar', 'q', '')
    with config.get_cache_db() as db:
        db.execute(
            "INSERT INTO perplexity_cache "
            "(query_hash, query, model, answer, citations, cost_usd, "
            " created_at, expires_at) VALUES (?, 'q', 'sonar', 'a', '[]', "
            " 0.02, ?, '9999-01-01T00:00:00Z')",
            (key, config.now_iso()))
        db.commit()
    r = ppx.search('q', model='sonar', reason='test')
    assert r['cached'] is True
    assert r['cost_usd'] == 0.0
    # A cache hit must not touch the network — proven by leaving
    # PERPLEXITY_API_KEY unset (fixture blanked it).


def test_search_json_parses_code_fence():
    from agents import perplexity as ppx
    data = ppx._try_parse_json('```json\n{"topic": "hail"}\n```')
    assert data == {'topic': 'hail'}


def test_search_json_extracts_object_from_prose():
    from agents import perplexity as ppx
    data = ppx._try_parse_json('Here you go: {"topic": "hail season"} — cheers')
    assert data == {'topic': 'hail season'}


def test_search_json_falls_back_to_raw_when_unparseable():
    from agents import perplexity as ppx
    data = ppx._try_parse_json('not json at all')
    assert isinstance(data, dict) and 'raw' in data


# ── B2B sources ──────────────────────────────────────────────────────────────

def test_perplexity_gap_normalize_emits_stable_source_ref():
    from agents.b2b.sources import perplexity_gap
    r1 = perplexity_gap._normalize(
        {'name': 'First Baptist Church', 'source_url': 'https://firstbaptist.org/about'},
        segment='church', state='CO', city='Denver')
    r2 = perplexity_gap._normalize(
        {'name': 'First Baptist Church', 'source_url': 'https://firstbaptist.org/about'},
        segment='church', state='CO', city='Denver')
    assert r1['source_ref'] == r2['source_ref']
    # Different orgs must produce different source_refs.
    r3 = perplexity_gap._normalize(
        {'name': 'Second Baptist Church', 'source_url': 'https://second.org'},
        segment='church', state='CO', city='Denver')
    assert r1['source_ref'] != r3['source_ref']


def test_perplexity_gap_normalize_skips_nameless_rows():
    from agents.b2b.sources import perplexity_gap
    assert perplexity_gap._normalize({'phone': '3035551212'},
                                     segment='church', state='CO', city='Denver') is None


def test_perplexity_gap_normalize_defaults_state_to_co():
    from agents.b2b.sources import perplexity_gap
    row = perplexity_gap._normalize({'name': 'Some Org'}, segment='church',
                                    state='', city='Boulder')
    assert row['state'] == 'CO'


# ── Content scoring ──────────────────────────────────────────────────────────

def test_score_ranks_more_actionable_topics_higher():
    from agents.content import score
    ranked = score.rank([
        {'topic': 'hail damage insurance claim',
         'summary': 'homeowners disputing depreciation on hail claims',
         'citations': ['https://a', 'https://b']},
        {'topic': 'kitchen renovations',
         'summary': 'quartz vs granite countertops',
         'citations': []},
    ])
    assert ranked[0]['topic'].startswith('hail')
    assert ranked[0]['score'] > ranked[1]['score']


# ── Marketing profile ────────────────────────────────────────────────────────

def test_marketing_profile_loads_from_the_repo_not_the_volume():
    """The autouse fixture points AGENTS_DATA_DIR at an empty scratch dir.

    Loading anyway is the whole invariant: the profile is version-controlled,
    so it must never be seeded onto the volume where the repo copy would go
    inert.
    """
    from agents import config
    profile = config.load_marketing_profile()
    assert config.data_dir() not in config.marketing_profile_path()
    assert profile['company']['website_domain'] == 'projectoneroofingcolorado.com'


def test_marketing_profile_has_every_required_section():
    from agents import config
    profile = config.load_marketing_profile()
    for key in ('version', 'last_reviewed', 'company', 'approved_services',
                'service_area', 'target_customers', 'phrases_to_avoid',
                'provable_differentiators', 'credentials'):
        assert key in profile, f'marketing profile lost its {key!r} section'


def test_approved_services_are_a_closed_list_and_customers_are_not():
    """Both flags are load-bearing and point opposite ways.

    Offering work we don't do is a promise we can't keep; refusing a customer
    type merely because nobody listed it yet is lost business.
    """
    from agents import config
    profile = config.load_marketing_profile()
    assert profile['approved_services']['exhaustive'] is True
    assert profile['target_customers']['exhaustive'] is False


def test_banned_phrases_are_lowercased_and_include_free():
    from agents import config
    phrases = config.banned_phrases()
    assert 'free' in phrases
    assert all(p == p.lower() and p.strip() for p in phrases)


def test_every_differentiator_carries_its_proof():
    """Empty today, so this passes vacuously — and bites the day someone adds
    an unbacked superiority claim."""
    from agents import config
    claims = config.load_marketing_profile()['provable_differentiators']['claims']
    for c in claims:
        assert c.get('claim'), f'differentiator with no claim text: {c!r}'
        assert c.get('proof'), f'unproven differentiator: {c.get("claim")!r}'


def test_marketing_profile_fails_loud_when_missing(monkeypatch, tmp_path):
    """A silently-empty profile would mean no banned phrases and no approved
    service list — an agent would write anything."""
    from agents import config
    monkeypatch.setattr(config, 'marketing_profile_path',
                        lambda: str(tmp_path / 'nope.json'))
    with pytest.raises(FileNotFoundError):
        config.load_marketing_profile()


# ── ingest annotate ─────────────────────────────────────────────────────────

def test_annotate_leads_serializes_citations_list():
    """A list of URLs must arrive on disk as JSON, not repr()."""
    from agents import ingest
    fake_module = type('X', (), {})()
    calls = []

    class FakeDb:
        def execute(self, sql, params):
            calls.append((sql, params))
            self.total_changes = 1
        def commit(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    fake_module.get_db = lambda: FakeDb()
    fake_module._now = lambda: '2026-01-01T00:00:00Z'
    ingest.annotate_leads(fake_module, [{
        'lead_id': 'abc',
        'research_notes': '{"summary":"pastor is john"}',
        'research_citations': ['https://a', 'https://b'],
        'recent_storm': '1 event 2in 2025-07-14',
    }])
    assert len(calls) == 1
    _sql, params = calls[0]
    # params order: research_notes, research_citations, recent_storm,
    #               enriched_at, updated_at, lead_id
    assert params[1] == '["https://a", "https://b"]'
