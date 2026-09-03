"""Build deployable exterior-catalog textures from the reviewed source pack.

Run from anywhere in the repository. The source pack is intentionally kept
outside the estimator runtime; this command creates the bounded, metadata-free
PNG files that the application's normal texture uploader would produce.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageOps


REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO / "output" / "exterior-product-assets"
SOURCE_MANIFEST = SOURCE_ROOT / "manifest.csv"
DESTINATION = REPO / "estimator" / "exterior_catalog_assets"

PRODUCTS = {
    ("IKO", "Nordic"): ("roof", "b_iko_nordic", "iko_nordic"),
    ("CertainTeed", "Landmark"): ("roof", "b_landmark", "landmark"),
    ("James Hardie", "Statement Collection"): (
        "siding", "b_hardie_statement", "hardie_statement"
    ),
    ("LP Building Solutions", "SmartSide ExpertFinish"): (
        "siding", "b_lp_expert", "lp_expertfinish"
    ),
    ("LP Building Solutions", "SmartSide ExpertFinish Naturals Collection"): (
        "siding",
        "b_lp_expert",
        "lp_expertfinish_naturals",
    ),
}

# These images are tiled into the visualizer at roughly 96 CSS pixels. Keeping
# a little extra resolution permits zooming/scale adjustments without shipping
# multi-megabyte brochure images to every rep. An adaptive palette retains the
# visible shingle and wood-grain variation while making the deployable pack
# small enough for normal mobile use.
MAX_DIMENSION = 512
PALETTE_COLORS = 256


def normalize(source_path: Path) -> tuple[bytes, int, int]:
    with Image.open(source_path) as source:
        source.load()
        image = ImageOps.exif_transpose(source).copy()
    if max(image.size) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
    if "A" in image.getbands():
        image = image.convert("RGBA")
    else:
        image = image.convert("RGB").quantize(
            colors=PALETTE_COLORS,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
    encoded = io.BytesIO()
    image.save(encoded, "PNG", optimize=True)
    return encoded.getvalue(), image.width, image.height


def main() -> None:
    if not SOURCE_MANIFEST.exists():
        raise SystemExit(f"Source manifest not found: {SOURCE_MANIFEST}")
    DESTINATION.mkdir(parents=True, exist_ok=True)

    entries = []
    generated_files = set()
    with SOURCE_MANIFEST.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("asset_kind") != "surface_texture":
                continue
            product_key = (row.get("manufacturer", ""), row.get("product", ""))
            if product_key not in PRODUCTS:
                raise SystemExit(f"No runtime mapping for {product_key!r}")
            category, bundle_id, family = PRODUCTS[product_key]
            source_path = SOURCE_ROOT / row["local_file"]
            png, width, height = normalize(source_path)
            digest = hashlib.sha256(png).hexdigest()
            filename = f"et_{digest[:32]}.png"
            (DESTINATION / filename).write_bytes(png)
            generated_files.add(filename)
            entries.append(
                {
                    "category": category,
                    "bundle_id": bundle_id,
                    "family": family,
                    "color": row["color_or_style"],
                    "file": filename,
                    "texture_scale": 96,
                    "width": width,
                    "height": height,
                    "sha256": digest,
                    "source_document": Path(
                        row["source"].replace("\\", "/")
                    ).name,
                    "source_page": row.get("source_page", ""),
                }
            )

    entries.sort(key=lambda item: (item["bundle_id"], item["color"].casefold()))
    manifest = {
        "version": 1,
        "generated_from": "output/exterior-product-assets/manifest.csv",
        "texture_count": len(entries),
        "textures": entries,
    }
    (DESTINATION / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for stale in DESTINATION.glob("et_*.png"):
        if stale.name not in generated_files:
            stale.unlink()
    print(f"Wrote {len(entries)} textures to {DESTINATION}")


if __name__ == "__main__":
    main()
