"""Fun posts: the light stuff people actually engage with.

A feed that is all hail tips and inspection offers is a feed people scroll
past. A clean joke on a Friday, a "this or that" people argue about in the
comments, a photo quiz - that is what earns the reach the useful posts then
ride on. So alongside the earned-topic packages, Nimbus drafts a small weekly
lineup of recurring fun series. They do not have to be about roofing.

Same pipeline, same rules as every other post:

* **Draft only.** A person reads and approves every one; nothing publishes.
* **The honesty guard still runs** (posts._vet): a joke claiming "the #1
  roofer in Colorado" is still a fabricated claim.
* **Fun has its own guard rails** (FUN_RULES, FUN_BLOCKLIST), because a
  roofer's page is not a meme page and a joke lands on everyone who follows
  it - including the churches, HOAs, schools and insurance agents we are
  asking for work. Clean for all ages; no politics, religion, alcohol or
  drugs, no real disasters, no punching down, and nobody we work with is the
  punchline.
* **Facebook and Instagram only.** LinkedIn and Google Business are not
  where a Friday joke belongs.
* **No repeats.** The last few fun posts go into the prompt as "already
  used", and a near-duplicate is dropped.
"""
import json
import re
from datetime import date

from .. import config, perplexity
from . import posts

FUN_PLATFORMS = ('facebook', 'instagram')

# Recurring series. `day` is when to post it (the drafts are written earlier
# in the week so a person can review them first).
SERIES = {
    'friday_funnies': {
        'name': 'Friday Funnies', 'day': 'Friday',
        'brief': 'A short, clean, genuinely funny post to end the week: a pun, a '
                 'one-liner, a relatable homeowner moment, or Colorado life (the '
                 'weather changing four times before lunch, snow in May, the wind). '
                 'It does NOT have to be about roofing. Aim for a groan or a laugh, '
                 'and invite people to share their own in the comments.',
        'needs_photo': False,
    },
    'this_or_that': {
        'name': 'This or That', 'day': 'Monday',
        'brief': 'A two-option "this or that" question people will argue about '
                 'in the comments. Fun, low-stakes, easy to answer with one word: '
                 'Colorado life, home and yard, seasons, weekend plans - or an '
                 'exterior choice (metal or shingle, dark roof or light). Ask them '
                 'to vote in the comments.',
        'needs_photo': False,
    },
    'guess_what': {
        'name': 'Guess What Wednesday', 'day': 'Wednesday',
        'brief': 'A photo quiz: a close-up of something a roofer sees every day '
                 '(a roof vent, flashing, a gutter guard, a hail-dented vent cap, '
                 'a bird nest in a gutter) and the question "what is it?". The '
                 'answer is revealed in the comments on Thursday. Write the post '
                 'AND describe the close-up photo to take in image_prompt.',
        'needs_photo': True,
    },
    'weather_whiplash': {
        'name': 'Colorado Weather Whiplash', 'day': 'Thursday',
        'brief': 'A playful take on Colorado weather doing too much - sunshine, '
                 'hail, snow and 70 degrees in one week. Relatable and light; '
                 'invite people to share the weirdest weather week they remember. '
                 'Never about a real storm that damaged homes or hurt anyone.',
        'needs_photo': False,
    },
    'caption_this': {
        'name': 'Caption This', 'day': 'Tuesday',
        'brief': 'A "caption this" post for a funny, harmless photo from the '
                 'job: a ladder with a view, a crew dog on site, a squirrel '
                 'inspecting a gutter. Write the prompt to the audience and '
                 'describe the photo to take in image_prompt. Best caption wins '
                 'bragging rights (no prizes - we do not promise giveaways).',
        'needs_photo': True,
    },
}
WEEKLY_ROTATION = ('this_or_that', 'guess_what', 'weather_whiplash', 'caption_this')

FUN_RULES = [
    'This is a FUN, light-hearted post, not a sales post. No call to book an '
    'inspection, no offer, no "contact us" - just something people enjoy.',
    'Clean enough for a family feed and a church congregation to share.',
    'Never about politics, religion, sex, alcohol, marijuana or drugs, or anything crude.',
    'Never joke about a real disaster, a real storm that damaged homes, injuries or death.',
    'Never punch down: no jokes about race, gender, age, weight, disability, '
    'nationality, income or intelligence.',
    'Nobody we work with is the punchline: not homeowners, HOAs or boards, '
    'insurance agents, adjusters, realtors, property managers, churches, schools '
    'or other contractors.',
    'No real people, celebrities, brands, copyrighted characters, song lyrics or movie quotes.',
    'Do not claim anything about our company - no customers, jobs, reviews or numbers.',
]

# A backstop for the rules a model most often slips on. Words, not topics -
# it cannot judge taste, and it is not meant to. The reviewer does that.
FUN_BLOCKLIST = re.compile(
    r"\b(politic\w*|election|democrat\w*|republican\w*|trump|biden|congress|"
    r"god|jesus|pray\w*|bible|satan|beer|beers|wine|drunk|booze|liquor|"
    r"weed|marijuana|cannabis|420|stoned|high as|sex\w*|damn|hell|crap|"
    r"shit\w*|ass|wtf|kill\w*|dead|death|die[ds]?)\b", re.I)


def fun_problems(text):
    """Why a fun post must be dropped, if it must."""
    problems = []
    hit = FUN_BLOCKLIST.search(text or '')
    if hit:
        problems.append(f'off-limits for a fun post: {hit.group(0)!r}')
    return problems


def recent_fun(limit=15):
    """First lines of the most recent fun posts, so the next ones differ."""
    with config.get_cache_db() as db:
        rows = db.execute(
            "SELECT draft_text FROM content_drafts WHERE source = 'fun' "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [(r['draft_text'] or '').strip().split('\n')[0][:140] for r in rows]


def _similar(a, b):
    wa, wb = set(re.findall(r'[a-z]{3,}', a.lower())), set(re.findall(r'[a-z]{3,}', b.lower()))
    return bool(wa and wb) and len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.7


def _prompt(series, platform, spec, avoid):
    return '\n'.join([
        f'Write one {spec["label"]} post for the recurring series "{series["name"]}" '
        f'(posted on {series["day"]}s).',
        series['brief'],
        f'Length: {spec["words"]} words. Voice: {spec["voice"]}',
        'Rules:', *[f'- {r}' for r in FUN_RULES],
        ('Already used recently - do something clearly different:\n'
         + '\n'.join(f'- {a}' for a in avoid)) if avoid else '',
        'Return JSON only: {"body": "...", "call_to_action": "the engagement ask, '
        'e.g. vote or caption in the comments", "hashtags": ["up to three"], '
        '"image_prompt": "the photo to take or the graphic to make", '
        '"alt_text": "...", "hook": "a 3-6 word title for this post"}',
    ])


def build(series_key, platforms=FUN_PLATFORMS, model=None, dry_run=False):
    """Draft one fun series post per platform. Never raises for one platform."""
    series = SERIES.get(series_key)
    if not series:
        return {'posts': [], 'rejected': [{'reason': f'unknown series {series_key}'}], 'cost_usd': 0}
    profile = config.load_marketing_profile()
    system = posts._system_prompt(profile)
    avoid = recent_fun()
    out, rejected, cost = [], [], 0.0
    package_id = f'fun-{series_key}-{date.today().isoformat()}'
    for platform in platforms:
        spec = posts.PLATFORMS.get(platform)
        if not spec or platform not in FUN_PLATFORMS:
            rejected.append({'platform': platform, 'reason': 'fun posts are Facebook/Instagram only'})
            continue
        try:
            r = perplexity.search_json(_prompt(series, platform, spec, avoid), system=system,
                                       model=model, max_tokens=1200,
                                       reason=f'fun-post:{series_key}:{platform}')
        except (perplexity.SpendCapReached, perplexity.PerplexityError) as e:
            rejected.append({'platform': platform, 'reason': str(e)})
            continue
        cost += float(r.get('cost_usd') or 0.0)
        data = r.get('data') or {}
        text = posts._render(data, platform)
        problems = posts._vet(text, platform, spec) + fun_problems(text)
        if any(_similar(text.split('\n')[0], a) for a in avoid):
            problems.append('too close to a recent fun post')
        if problems:
            rejected.append({'platform': platform, 'reason': '; '.join(problems)})
            continue
        hook = str((data.get('hook') if isinstance(data, dict) else '') or '').strip()[:60]
        notes = [f'Fun series - post on {series["day"]}.',
                 'Read it as a follower: would you smile, and would anyone we work with wince?']
        if series['needs_photo']:
            notes.append('Needs a real photo of our own - see the photo brief. No stock images.')
        out.append({
            'package_id': package_id, 'platform': platform,
            'topic': f'🎉 {series["name"]}' + (f': {hook}' if hook else ''),
            'draft_text': text, 'citations': [], 'source': 'fun',
            'review_notes': ' '.join(notes),
            'creative': {k: (data.get(k, '') if isinstance(data, dict) else '')
                         for k in ('image_prompt', 'alt_text')},
        })
    if out and not dry_run:
        posts._persist(out)
    return {'posts': out, 'rejected': rejected, 'cost_usd': round(cost, 4)}


def weekly_series(today=None):
    """Friday Funnies every week, plus one rotating series."""
    week = (today or date.today()).isocalendar()[1]
    return ['friday_funnies', WEEKLY_ROTATION[week % len(WEEKLY_ROTATION)]]


def weekly_run(dry_run=False):
    made, dropped, cost = 0, 0, 0.0
    for key in weekly_series():
        r = build(key, dry_run=dry_run)
        made += len(r['posts'])
        dropped += len(r['rejected'])
        cost += r['cost_usd']
    return {'note': f'{made} fun draft(s)' + (f', {dropped} dropped' if dropped else ''),
            'cost_usd': round(cost, 4)}
