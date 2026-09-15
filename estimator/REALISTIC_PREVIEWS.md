# Realistic exterior previews (opt-in)

The instant texture canvas is unchanged. A separate **Generate realistic preview**
action sends the saved original elevation and selected catalog references to the
OpenAI image edits API. It does not depend on the SAM roof mask. It requests
material replacement, roof-plane perspective, and preservation of fascia/rakes
unless trim is separately selected. These are model instructions, not guaranteed
pixel locks. Review is mandatory before a candidate becomes the saved concept.
Positioned door/window/shutter cutouts remain an instant-editor feature. If those
placements are in scope, generation is rejected with instructions to deselect
their surfaces; this first version must not silently ignore a configured cutout.

## Railway setup

On the existing whole-repository estimator service, configure:

- `OPENAI_API_KEY`: a server-side OpenAI project API key. Never paste it into chat,
  source, catalog fields, or browser JavaScript.
- `EXTERIOR_REALISTIC_PREVIEW=1`: explicit feature enablement.
- Optional `EXTERIOR_RENDER_USER_DAILY_LIMIT=10` and
  `EXTERIOR_RENDER_DAILY_LIMIT=50`: rolling 24-hour attempt limits. Failed and
  interrupted attempts count. These are count limits, not dollar budgets.

OpenAI API access/billing is separate from ChatGPT subscriptions. Check model
access and any organization-verification requirement before enabling. Configure
project billing alerts/limits as appropriate. The fixed model is
`gpt-image-2.5-sunburst`, high quality, one image per confirmed action. Do not
substitute unverified model names. Official contract:
https://developers.openai.com/api/docs/guides/image-generation

Output follows the source aspect ratio, rounded to supported 16-pixel increments
with a 1536-pixel long side. Very wide/tall inputs beyond 3:1 are rejected. Reference
images and the original are decoded locally with EXIF removed before transfer.
Only locally stored estimate photos/catalog assets are accepted; arbitrary URLs
are not fetched. Up to six reference images may accompany an original.

## Workflow and safety

1. Upload the original and choose only the surfaces/products intended to change.
2. Click Generate realistic preview and confirm the paid provider transfer.
3. Return later if needed; the latest jobs are retrieved from the server.
4. Compare with the original, check the review box, then Use reviewed preview.
5. Existing customer-sharing permissions remain in effect. Saving new instant
   renderings can replace the accepted AI image; the canvas remains the instant
   preview, not a representation of the saved AI result.

Jobs/candidates reside in `DATA_DIR/realistic_previews/` on the persistent volume.
This directory must be included in volume backups and storage monitoring. SQLite
coordinates reservations across web workers (two active jobs company-wide, one
per estimate). A daemon thread makes the slow provider call outside the web
request; redeploys can interrupt it. Interrupted jobs become failed after ten
minutes and are **never automatically retried** because a provider may already
have billed the request. Repeated confirmed requests use an idempotency nonce.
Review OpenAI usage before deliberately starting again after an ambiguous failure.
Candidates are authenticated; accepting one copies it into the existing estimate
upload mechanism. No new public sharing route is created.

This initial version retains candidates and job records; it has no automatic
retention cleanup or dedicated durable queue. Monitor volume space. Do not delete
job records to reclaim images: they also enforce daily limits/idempotency.

## Acceptance checks before customer rollout

Offline tests mock the provider; they do not establish visual accuracy. Test the
customer's original portrait house photo with the actual selected metal product
and manufacturer reference after credentials and a tested build are deployed.
Check ridge-to-eave seams on both planes, valley geometry, fascia/rake exclusion,
chimney/vents/ladder retention, unchanged framing, and the original's roofline.
Reject rather than publish any failed result. Repeat on shingle roofs and siding.
Color remains a photographic approximation—physical manufacturer samples govern.

Run `python -m pytest estimator/tests/test_realistic_previews.py` and the repository
`python run_tests.py` before shipping. Keep this feature off until the live visual
acceptance check succeeds. No paid provider call is part of the test suite.
