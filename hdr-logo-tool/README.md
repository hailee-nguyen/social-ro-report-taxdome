# HDR glow logo tool

Renders a logo SVG to an HDR JPEG where only the white/bright elements
physically glow on HDR-capable displays (MacBook Pro 2021+, iPhone 12+),
while colored elements stay at their normal SDR appearance.

## How it works

1. **Render**: `rsvg-convert` rasterizes the SVG to a PNG at the requested
   width, flattened onto a solid background (JPEG has no alpha channel).
2. **Classify**: each pixel gets a continuous "whiteness" score from its
   HSV saturation/value (low saturation + high value = white-ish), feathered
   with a small Gaussian blur so glow edges taper smoothly instead of
   showing a hard cutout.
3. **Re-encode**:
   - Colored pixels are driven to decode back to `--sdr-nits` (default
     203, the common SDR reference white used by HDR photo pipelines) —
     they look like an ordinary SDR image and do not glow.
   - White/bright pixels are driven to decode up to `--hdr-nits` (default
     1000) — well above SDR reference, which is what creates the HDR
     headroom that makes them glow.
   - Both are encoded with the SMPTE ST 2084 (PQ) OETF.
4. **Tag**: the JPEG is saved with an embedded ICC profile describing the
   Rec.2020 gamut with a PQ transfer curve (`icc_rec2020_pq.py`), which is
   what tells a compatible OS/browser to interpret the pixel values as PQ
   instead of plain sRGB.

## Files

- `make_hdr_logo.py` — the CLI pipeline (SVG → HDR JPEG).
- `icc_rec2020_pq.py` — builds the Rec.2020/PQ ICC profile from the
  published BT.2020 primaries and the ST 2084 formulas, and exposes the
  PQ encode/decode helpers used by the pipeline.
- `test_logo.svg` — a small synthetic logo (white circle + red/green
  blocks) used to exercise the pipeline end-to-end.

### Why the ICC profile is generated, not scraped from a sample JPEG

The originally sketched approach was to extract an ICC profile from an
existing HDR JPEG found online. That's fragile (there's no guarantee a
random downloaded photo carries exactly the right profile) and it would
mean redistributing binary ICC bytes lifted from someone else's image in
this repo. Instead, `icc_rec2020_pq.py` builds the profile directly from
the public Rec.2020/BT.2100 chromaticities and the ST 2084 formulas —
the same "matrix + TRC" family as color.org's sRGB v4 profile, just with
Rec.2020 primaries, a PQ curve, and a `lumi` tag of 10000 cd/m² (PQ's
code 1.0 == 10,000 nits by definition). It's deterministic, inspectable,
and has no external dependency.

## Usage

```bash
# System deps (Ubuntu/Debian):
sudo apt-get install librsvg2-bin
pip3 install Pillow numpy

# macOS:
brew install librsvg
pip3 install Pillow numpy

python3 make_hdr_logo.py path/to/logo.svg out_hdr.jpg \
    --width 2048 \
    --hdr-nits 1000 \
    --sdr-nits 203 \
    --bg black
```

Key options:

| Flag | Default | Meaning |
|---|---|---|
| `--width` | 2048 | Render width in px (height keeps aspect ratio) |
| `--bg` | black | Flatten color for transparent areas |
| `--hdr-nits` | 1000 | Peak luminance for white/bright elements |
| `--sdr-nits` | 203 | Reference white for colored elements |
| `--sat-thresh` | 0.15 | Saturation below which a pixel is "white"-eligible |
| `--val-thresh` | 0.85 | Value above which a pixel is "white"-eligible |
| `--feather` | 2.0 | Gaussian blur radius (px) on the white/color mask |
| `--quality` | 95 | JPEG quality |

Try it on the bundled test logo:

```bash
python3 make_hdr_logo.py test_logo.svg /tmp/test_logo_hdr.jpg --width 1200
```

## What's verified vs. not

Verified in this environment:
- The ICC profile is well-formed ICC v4 (parses cleanly via Pillow/lcms2).
- The PQ encode/decode math round-trips exactly against the published ST
  2084 formulas (e.g. 1000 nits → code 0.7518 → back to 1000.00 nits).
- The RGB→XYZ colorant matrix correctly maps linear white to the D50 PCS
  white point used by the profile.
- Rendering `test_logo.svg` produces a JPEG where the white circle's
  pixels decode to ~1000 nits (the configured HDR target) and the
  colored blocks decode back to their proportional share of ~203 nits
  (the SDR reference), with the black background staying at 0 nits.

Not verified here (no HDR-capable display in this sandbox):
- Whether macOS/iOS actually recognizes this specific profile and shows
  the "boosted" glow — that has to be checked on real hardware (a
  MacBook Pro 2021+ or iPhone 12+, e.g. via Photos, Safari, or AirDrop).
- 8-bit JPEG channel precision is limited (256 levels), which can cause
  visible banding or minor hue drift in very dark, saturated colors under
  strict colorimetric decoding — an inherent limitation of 8-bit PQ
  JPEGs in general, not specific to this profile. If that matters for
  your logo, consider widening `--val-thresh`/`--sat-thresh` gaps or
  avoiding very dark saturated brand colors near the glow boundary.
