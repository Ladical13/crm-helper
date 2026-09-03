# Exterior catalog assets

This directory contains the compact, deployable manufacturer swatches used by
the estimator's Product Visualizer. The files are content-addressed and are
copied into `DATA_DIR/uploads/_catalog` when the app starts on Railway.

- IKO Nordic: 6 colors from the supplied Nordic brochure.
- CertainTeed Landmark: 9 colors from the supplied Landmark brochure.
- James Hardie Statement Collection: 17 colors from the supplied regional
  product catalog.
- LP SmartSide ExpertFinish: 16 core colors from official LP swatches.
- LP SmartSide ExpertFinish Naturals Collection: 6 official Cedar Texture
  swatches.

All previews are representative. Manufacturer availability and physical
samples remain authoritative. ProVia door screenshots/renderings are not part
of this pack; the app keeps its exact Envision configuration-code and image
handoff for those products.

`manifest.json` is generated with:

```powershell
python estimator/scripts/build_exterior_texture_assets.py
```

That script expects the reviewed research pack at
`output/exterior-product-assets/manifest.csv`, limits the longest edge to 512
pixels, strips source metadata through re-encoding, and uses an adaptive PNG
palette to keep the mobile download size reasonable.
