"""Public-Research Local SEO Strategist.

Works with **no owned data source** — no Search Console, no GA4, no CMS
credentials, no Business Profile API. Those are owned at the franchise level
and may never arrive, so nothing here waits on them.

What it does have:

  * the approved marketing profile (``agents/marketing_profile.json``),
  * a polite, robots-respecting crawl of our own public website,
  * public page metadata and structured-data inspection,
  * Perplexity research into what Northern Colorado customers ask, with
    citations and the existing monthly spend cap.

What it produces: a saved weekly report and a ranked queue of recommendations
that a human approves. Nothing is published, sent, or edited anywhere.

**The honesty rule that shapes the whole design:** without an owned analytics
source we cannot know a ranking, a search volume, a traffic number or a
competitor's performance. So the strategist never states one. Every finding is
labelled a *public-research opportunity* and carries the URLs it came from.
``agents/seo/honesty.py`` enforces this and the tests fail the build if a
recommendation slips through claiming a number it cannot have.
"""

__all__ = ['crawl', 'inspect', 'research', 'recommend', 'report', 'honesty', 'run']
