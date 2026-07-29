"""Colorado partner sourcing for The Pipeline.

Pulls referral-partner prospects from free Colorado open data, normalizes them
to one row shape, and hands them to the CRM's bulk importer. Deliberately dumb:
all dedupe, suppression and assignment logic lives server-side in salescrm, so
this package can be re-run at will without needing to know what already exists.

    python -m prospector segments
    python -m prospector pull dora:hoa --out prospector/inbox/hoa.json
    python -m prospector push prospector/inbox/hoa.json --base-url ... --user luke --dry-run
"""
__all__ = ['normalize', 'socrata', 'push', 'sources']
