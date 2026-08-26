# Exterior Design Studio

## Automatic selection (optional, off by default)

The intended rep workflow is **upload photo → automatic surface detection →
product/color dropdowns → review → Save Renderings**. The browser paints no
regions by default. Open **Refine selection** for optional edge corrections.
**Show original photo** compares the original with the current preview.

This requires a paid fal account with access to `fal-ai/sam-3/image`. An owner
must approve provider use and configure these server variables before enabling:

```
EXTERIOR_AUTO_DETECT=1
FAL_KEY=<server-side fal credential>
```

Set a usage allowance in the provider account. Each photo submits three model
requests (roof, siding, entry door). Changing dropdowns afterwards runs locally
and does not request more inference. No credential belongs in client code,
source control, or chat. No customer photo is sent while either variable is
missing. Setting these variables is an explicit opt-in to sending the resized
photo to fal. Metadata is stripped before transmission. Review fal's data
handling terms before enabling it for customer photos. Detection submissions
set `X-Fal-Store-IO: 0` so the inline photo and returned masks do not enter
fal's request-history storage, and request that any temporary media object
expire after one hour.

Deploy the whole repository through the existing portal service. No database
migration is required. This code change alone neither configures the provider
nor deploys the feature. Without configuration, the interface says detection
is not connected and keeps the manual refinement tools available.

Inference uses the queue so it does not occupy a web worker while the model
runs. Poll tickets expire after 10 minutes and bind the job to a rep, estimate,
and photo. Each surface has a 30-second submission cooldown per estimate.
Failures and empty/low-confidence detections preserve existing selections.
Changing the photo or customer invalidates in-flight results in the editor.
Results are not saved to the customer estimate until **Save Renderings**.

## Catalog and preview accuracy

Roof/siding dropdowns start with the estimate's selected bundles, then read
colors/styles from the live price book. Exploring another product is a design
choice only: it does **not** change quantities, pricing, or the quoted bundle.
Update Products/Pricing separately after the customer chooses a look.

The door menu now uses ProVia **Signet**, **Ascent**, and **Legacy**, with a
subset of named ProVia paint finishes. Hex swatches are approximations. The
door preview recolors the existing door: it does not render exact panel,
glass, grain, or hardware geometry. Do not use the old generic tiled door
textures to imply an exact ProVia product. Use the linked ProVia configurator
to specify exact door style, glass, and hardware until approved product images
and placement are added. Existing saved designs retain their old choices.

References (verified 2026-08-26):

- [ProVia entry doors](https://www.provia.com/doors/entry-doors/)
- [ProVia paint options](https://www.provia.com/doors/paint-options/)
- [ProVia Envision/configurator](https://www.provia.com/design-center/envision/)
- [SAM 3 input/output schema](https://fal.ai/models/fal-ai/sam-3/image/api)
- [fal queue protocol](https://fal.ai/docs/documentation/model-apis/inference/queue)
- [fal data-retention controls](https://fal.ai/docs/documentation/model-apis/media-expiration)

Automated tests mock inference and validate masks, access checks, ticket
scope, failures, and saved state. Live detection quality must still be tested
with an approved key and representative home photos (trees, shadows, brick,
multiple houses, and partial roof views). Detection is not guaranteed to find
only the intended house. Reps must review the result before sharing it.
