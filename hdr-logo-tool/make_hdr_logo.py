#!/usr/bin/env python3
"""
Render a logo SVG to an HDR JPEG where only the white/bright elements glow
on HDR-capable displays (e.g. MacBook Pro 2021+, iPhone 12+ True Tone XDR
displays), while colored elements stay at their normal SDR appearance.

Technique:
  1. Render the SVG to a raster PNG (via rsvg-convert).
  2. Classify each pixel's "whiteness" from its saturation/value.
  3. Colored pixels are re-encoded so they decode (through the embedded
     ICC profile's PQ curve) back to ~`--sdr-nits` of luminance -- i.e.
     they round-trip to the same apparent brightness/color as a normal
     SDR image, so they do NOT glow.
  4. White/bright pixels are re-encoded to decode to up to `--hdr-nits`,
     which is above the SDR reference and creates real HDR headroom.
  5. The result is saved as a JPEG with a Rec.2020-gamut / ST 2084 (PQ)
     ICC profile embedded (see icc_rec2020_pq.py), which is what tells a
     compatible OS/browser to treat the pixel values as PQ-encoded
     instead of plain sRGB.

Usage:
  python3 make_hdr_logo.py logo.svg out_hdr.jpg \
      --width 2048 --hdr-nits 1000 --sdr-nits 203 --bg black
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from icc_rec2020_pq import build_rec2020_pq_icc_profile, nits_to_pq_code


def render_svg_to_png(svg_path, png_path, width, background):
    cmd = [
        "rsvg-convert",
        "-w", str(width),
        "-a",  # keep aspect ratio
        "-b", background,
        "-o", str(png_path),
        str(svg_path),
    ]
    subprocess.run(cmd, check=True)


def srgb_to_linear(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def gaussian_blur(mask, radius):
    if radius <= 0:
        return mask
    img = Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8))
    from PIL import ImageFilter
    img = img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(img).astype(np.float64) / 255.0


def compute_whiteness_mask(rgb01, sat_thresh, val_thresh, feather):
    """rgb01: HxWx3 float array in [0,1], gamma-encoded (as decoded from PNG)."""
    maxc = rgb01.max(axis=2)
    minc = rgb01.min(axis=2)
    value = maxc
    sat = np.where(maxc > 1e-6, (maxc - minc) / np.maximum(maxc, 1e-6), 0.0)

    val_factor = smoothstep(val_thresh, 1.0, value)
    sat_factor = 1.0 - smoothstep(0.0, sat_thresh, sat)
    whiteness = val_factor * sat_factor
    whiteness = gaussian_blur(whiteness, feather)
    return whiteness, value


def process_image(png_path, hdr_nits, sdr_nits, sat_thresh, val_thresh, feather):
    img = Image.open(png_path).convert("RGB")
    arr = np.asarray(img).astype(np.float64) / 255.0  # HxWx3, gamma-encoded

    whiteness, value = compute_whiteness_mask(arr, sat_thresh, val_thresh, feather)

    linear = srgb_to_linear(arr)  # HxWx3 linear light, 0-1

    # Colored / SDR-referenced target: preserve original color+brightness
    # ratios, scaled so full-scale linear maps to `sdr_nits`.
    sdr_target_nits = linear * sdr_nits

    # HDR white target: drive all channels equally, scaled by local pixel
    # brightness so anti-aliased/faded edges taper smoothly to 0 instead
    # of popping to full peak nits.
    hdr_target_nits = value[..., None] * hdr_nits
    hdr_target_nits = np.repeat(hdr_target_nits, 3, axis=2)

    w = whiteness[..., None]
    target_nits = sdr_target_nits * (1 - w) + hdr_target_nits * w

    pq_code = nits_to_pq_code(target_nits)  # 0-1
    out_u8 = np.clip(np.round(pq_code * 255.0), 0, 255).astype(np.uint8)
    return Image.fromarray(out_u8, mode="RGB")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("svg", type=Path, help="Input logo SVG")
    ap.add_argument("output", type=Path, help="Output HDR JPEG path")
    ap.add_argument("--width", type=int, default=2048, help="Render width in pixels")
    ap.add_argument("--bg", default="black",
                     help="Flatten background color for transparent areas (CSS color, e.g. black)")
    ap.add_argument("--hdr-nits", type=float, default=1000.0,
                     help="Peak luminance for white/bright elements")
    ap.add_argument("--sdr-nits", type=float, default=203.0,
                     help="Reference SDR white luminance for colored elements")
    ap.add_argument("--sat-thresh", type=float, default=0.15,
                     help="Saturation below which a pixel is eligible to be 'white'")
    ap.add_argument("--val-thresh", type=float, default=0.85,
                     help="Value (brightness) above which a pixel is eligible to be 'white'")
    ap.add_argument("--feather", type=float, default=2.0,
                     help="Gaussian blur radius (px) applied to the white/color mask")
    ap.add_argument("--quality", type=int, default=95, help="JPEG quality")
    ap.add_argument("--keep-png", type=Path, default=None,
                     help="Optionally save the intermediate rendered PNG here")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        png_path = Path(args.keep_png) if args.keep_png else Path(tmp) / "render.png"
        render_svg_to_png(args.svg, png_path, args.width, args.bg)

        out_img = process_image(
            png_path,
            hdr_nits=args.hdr_nits,
            sdr_nits=args.sdr_nits,
            sat_thresh=args.sat_thresh,
            val_thresh=args.val_thresh,
            feather=args.feather,
        )

        icc_profile = build_rec2020_pq_icc_profile()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        out_img.save(args.output, format="JPEG", quality=args.quality,
                     icc_profile=icc_profile)

    print(f"Wrote {args.output}")


if __name__ == "__main__":
    sys.exit(main())
