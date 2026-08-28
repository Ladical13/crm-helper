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

Set a usage allowance in the provider account. Each complete automatic pass
submits five model requests: roof, wall siding, trim/fascia, soffit, and entry
door. Separate masks let the rep choose contrasting trim and soffit finishes.
Changing dropdowns afterwards runs locally and does not request more inference.
No credential belongs in client code,
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
Trim and soffit detection remain image-dependent, so the rep should review the
edges and use the matching **Refine selection** tool when boards or eaves are
obscured or missed.
Changing the photo or customer invalidates in-flight results in the editor.
Results are not saved to the customer estimate until **Save Renderings**.

## Manager exterior catalog

Managers open **Price Book → Exterior Catalog**. The editor accepts manual
rows or a CSV upload; **CSV Template** downloads the supported columns:

```
category,brand,product,style,color,color_code,hex,applies_to,price_book_bundle,active
```

One row is one product/color combination. Categories are `roof`, `siding`,
`trim`, `soffit`, `door`, and `paint`. Paint must use `applies_to=siding`,
`applies_to=trim`, `applies_to=soffit`, or `applies_to=door`. Hex values must be
six-digit CSS colors such as `#292929`. The optional
`price_book_bundle` links a roof or siding visual product to an existing bundle
so Design Studio starts on the system already quoted. It never changes scope
or pricing. CSV imports merge by stable product/color identity and update
matching rows rather than duplicating them. The catalog is limited to 5,000
rows and only managers/admins may save or import it.

The catalog lives under `exterior_catalog` in the live `price_book.json` on the
Railway volume. Existing seeded roof, siding, trim/fascia, soffit, and ProVia
palettes are migrated into uploader rows on first use. Version markers preserve
later manager edits and intentional row deletions.

## Catalog and preview accuracy

All five surface dropdowns read active entries from the manager exterior catalog.
They start with a catalog product linked to the estimate's selected bundle when
one exists. Exploring another product is a design choice only: it does **not**
change quantities, pricing, or the quoted bundle. Update Products/Pricing
separately after the customer chooses a look.

### LP SmartSide ExpertFinish 2025

The seeded LP ExpertFinish visual catalog is based on LP sales sheet LPEF01884
(01/25). It contains the sheet's 16 named colors and eight selectable wall
profiles: 6-inch and 8-inch Lap Joint Siding; straight-edge and staggered-edge
Shakes; 4×8-foot and 4×10-foot Panel - NGSE; 8-inch Nickel Gap; and 16-inch
Vertical Siding.

Trim/fascia and soffit are independent visual layers. ExpertFinish trim/fascia
uses the 16 named preview colors. Soffit can be closed or vented, with the sheet's
12-, 16-, and 24-inch widths represented in the profile label. The sales sheet
does not provide a per-SKU color availability matrix, so these choices remain a
visual aid and the rep must verify the exact product/color combination before
ordering. Field-painted LP systems retain the general paint-preview palette.

The hex values are sampled from the solid digital swatches in the supplied PDF.
LP states that displayed colors are representative and may not be an exact
match, and that not every product is available in every color. The tool retains
the physical-sample warning. This source updates visual options and product
copy only; it does not change manager pricing or quantities.

### James Hardie Statement Collection 2026

The seeded James Hardie Statement Collection visual catalog is based on the
North Rockies & Denver Color & Product Availability catalog HS2601-NRD (01/26).
It contains the region's 17 Statement Collection siding colors: Arctic White, Cobble
Stone, Navajo Beige, Khaki Brown, Monterey Taupe, Pearl Gray, Timber Bark, Rich
Espresso, Mountain Sage, Gray Slate, Light Mist, Boothbay Blue, Night Gray,
Evening Blue, Aged Pewter, Iron Gray, and Countrylane Red. The exact selectable
profiles are Hardie Plank in Select Cedarmill or Smooth; Hardie Panel + Trim
Batten in Rustic Grain or Smooth; Hardie Shingle in Straight Edge Panel or
Staggered Edge Panel; and Hardie Panel in Select Cedarmill, Smooth, or Sierra 8.

The representative hex values are sampled from the catalog's textured digital
swatches. James Hardie says printed colors are only as accurate as the printing
method permits and directs customers to order samples, so the physical-sample
warning remains in the tool. Primed products, the nearly 700 made-to-order
Dream Collection finishes, and the primed-only Artisan and Architectural Panel
specialty lines remain separate and are not folded into the Statement bundle.

The catalog shows a narrower regional finish range for separate trim and soffit.
Hardie Trim has Rustic Grain and Smooth profiles and is limited here to Arctic
White, Cobble Stone, Iron Gray, and Timber Bark. Hardie Soffit has Vented Smooth,
Non-Vented Smooth, Non-Vented Select Cedarmill, and Vented Select Cedarmill
profiles and is limited here to Arctic White. Design Studio enforces those
separate palettes instead of offering all 17 siding colors. Primed Hardie systems
retain the general field-paint preview palette. Verify the physical specification
and regional availability before ordering.

Product copy now reflects the catalog's HZ5 northern-climate positioning,
resistance to moisture damage, pests, dimensional movement, hail, and impact,
and its noncombustible/Class A fiber-cement testing language. It also reflects
the 30-year non-prorated limited substrate warranty and 15-year limited
ColorPlus finish warranty. This update does not change manager pricing,
quantities, or the quoted siding bundle.

### CertainTeed Landmark 2026

The seeded standard Landmark visual catalog is based on CertainTeed Landmark
Series brochure 00-00-134-US-EN (03/26). It contains the nine colors under the
standard **Landmark Color Palette**: Silver Birch, Georgetown Gray, Weathered
Wood, Heather Blend, Burnt Sienna, Resawn Shake, Driftwood, Moiré Black, and
Black Walnut. Landmark Solaris, Landmark ClimateFlex, Landmark PRO, NorthGate,
and Landmark TL are separately identified product lines in the brochure and
are not folded into the standard Landmark bundle or its pricing.

The representative hex values are sampled from the brochure's photographic
shingle swatches. They cannot reproduce the granule mix, blend range,
installation pattern, lighting, or sunlight on a real roof. CertainTeed says
printed reproductions cannot be guaranteed to match the actual product color,
so the physical-sample warning remains in the tool.

Product copy now reflects the supplied brochure: dual-layer construction,
UL 2218 Class 3 impact resistance, NailTrak, QuadraBond, CertaSeal, a lifetime
limited transferable residential warranty with 10-year SureStart protection,
a 25-year StreakFighter algae-resistance warranty, and a 15-year 110 mph wind
warranty. A 160 mph wind-warranty upgrade is available only with the required
CertainTeed starter and hip-and-ridge products. This update does not change
manager pricing, quantities, or the quoted roofing bundle.

### IKO Nordic 2026

The seeded IKO Nordic visual catalog is based on IKO homeowner brochure
MR9L350 (02/26). It contains the brochure's six enhanced color blends: Olde
Style Weatherwood, Summit Grey, Granite Black, Driftshake, Shadow Brown, and
Glacier. The representative hex values are sampled from the brochure's
photographic shingle swatches; they cannot reproduce the granule mix, blend
range, installation pattern, lighting, or sunlight on a real roof. IKO directs
customers to review several full-size shingles and an actual installation
before making a final selection, and notes that product/color availability
varies by region.

Product copy now reflects the supplied sheet: polymer-modified asphalt,
ArmourZone reinforced nailing surface, a Class 4 impact resistance rating,
130 mph limited wind warranty, Limited Lifetime warranty with 15 years of Iron
Clad Protection, and a 10-year limited blue-green algae resistance warranty.
The Class 4 rating may help a homeowner obtain an insurance-premium reduction
where available, but it is not a guarantee of impact resistance and hail damage
is not covered by the limited warranty. This update does not change manager
pricing, quantities, or the quoted roofing bundle.

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
