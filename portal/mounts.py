"""The mounted apps and where they hang off the origin.

Single source of truth for the mount prefixes: portal.wsgi builds the
DispatcherMiddleware map from this, the launcher renders cards from it, and
the app-switcher bar renders its pills from it. Change a prefix here and all
three follow.

`hidden` means mounted and reachable, but not advertised: portal.wsgi serves
it like any other app, while the launcher grid and the switcher bar leave it
out (portal/app.py filters it out of /api/me). It is for an app that belongs to
one person rather than to the company — reachable by URL and installable to a
home screen, without putting a tile for it in front of every rep.

Prefixes are also baked into each app's front end (fetch paths, service worker
scope, static hrefs), so changing one is not a one-file edit — grep the app's
static/ directory too.
"""

MOUNTS = [
    {
        'key':    'canvass',
        'prefix': '/canvass',
        'label':  'Canvass',
        'icon':   '📍',
        'blurb':  'Knock the door, drop the pin, check the hail.',
        'dir':    'canvasser',
        'accent': '#10B981',
    },
    {
        'key':    'crm',
        'prefix': '/crm',
        'label':  'Pipeline',
        'icon':   '📋',
        'blurb':  'Work the lead — stages, tasks, follow-ups.',
        'dir':    'salescrm',
        'accent': '#F97316',
    },
    {
        'key':    'estimate',
        'prefix': '/estimate',
        'label':  'Estimate',
        'icon':   '🏠',
        'blurb':  'Measure, price, present, sign.',
        'dir':    'estimator',
        'accent': '#00A8B5',
    },
    {
        # P1 Lift — a personal training log. Mounted because it is used on a
        # phone in a gym, which means it needs to be on the site and behind the
        # one login; `hidden` because nobody else has any reason to see a tile
        # for it. Everything it stores is scoped to the signed-in user, so a rep
        # who finds the URL gets their own empty log, never somebody else's.
        'key':    'workout',
        'prefix': '/workout',
        'label':  'Lift',
        'icon':   '🏋️',
        'blurb':  'Log the set, beat last week.',
        'dir':    'workout',
        'accent': '#F97316',
        'hidden': True,
    },
]

BY_KEY = {m['key']: m for m in MOUNTS}
PREFIXES = [m['prefix'] for m in MOUNTS]
# What the launcher and the switcher bar are allowed to show. Everything in
# MOUNTS is mounted; only these are advertised.
VISIBLE = [m for m in MOUNTS if not m.get('hidden')]
