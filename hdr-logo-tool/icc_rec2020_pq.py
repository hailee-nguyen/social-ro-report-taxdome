"""
Builds a self-contained ICC v4 "matrix/TRC" profile describing the
Rec.2020 (BT.2020/BT.2100) colour gamut with an SMPTE ST 2084 (PQ)
transfer curve.

This is generated from the public BT.2020 primaries and the published
ST 2084 EOTF formula rather than extracted from a third-party photo, so
there's no dependency on finding "the right" sample JPEG online and no
question about redistributing someone else's embedded profile bytes.

The profile follows the same "matrix + TRC" family as the classic
sRGB.icc (v4) profile from color.org: colorant tags are D50-adapted via
a Bradford 'chad' matrix, media white point is stored as the un-adapted
D65 value, and the TRC curves are sampled lookup tables instead of a
parametric shaper (ICC has no built-in PQ shaper type).

A 'lumi' (luminance) tag of 10000 cd/m^2 is included, matching the PQ
convention that normalized linear 1.0 corresponds to 10,000 nits.
"""

import struct
import numpy as np

# ---------------------------------------------------------------------------
# ST 2084 (PQ) transfer functions
# ---------------------------------------------------------------------------

_M1 = 2610.0 / 16384.0
_M2 = 2523.0 / 4096.0 * 128.0
_C1 = 3424.0 / 4096.0
_C2 = 2413.0 / 4096.0 * 32.0
_C3 = 2392.0 / 4096.0 * 32.0


def pq_eotf(n):
    """PQ code value (0-1) -> linear light, normalized so 1.0 == 10000 nits."""
    n = np.clip(n, 0.0, 1.0)
    np_ = np.power(n, 1.0 / _M2)
    num = np.clip(np_ - _C1, 0.0, None)
    den = _C2 - _C3 * np_
    l = np.power(num / den, 1.0 / _M1)
    return l


def pq_oetf(l):
    """Linear light (0-1, 1.0 == 10000 nits) -> PQ code value (0-1)."""
    l = np.clip(l, 0.0, 1.0)
    lm1 = np.power(l, _M1)
    n = np.power((_C1 + _C2 * lm1) / (1.0 + _C3 * lm1), _M2)
    return n


def nits_to_pq_code(nits):
    """Convenience: absolute nits -> PQ code value (0-1)."""
    return pq_oetf(np.asarray(nits, dtype=np.float64) / 10000.0)


def pq_code_to_nits(code):
    """Convenience: PQ code value (0-1) -> absolute nits."""
    return pq_eotf(np.asarray(code, dtype=np.float64)) * 10000.0


# ---------------------------------------------------------------------------
# Colorimetry: Rec.2020 primaries -> D50-adapted RGB->XYZ matrix
# ---------------------------------------------------------------------------

# BT.2020 / BT.2100 primaries and D65 white (CIE xy chromaticities)
_REC2020_PRIMARIES = {
    "r": (0.708, 0.292),
    "g": (0.170, 0.797),
    "b": (0.131, 0.046),
}
_D65_WHITE_XY = (0.3127, 0.3290)

# Standard D65 and D50 XYZ white references (Y=1), as used by color.org's
# sRGB v4 profile.
D65_XYZ = np.array([0.9505, 1.0000, 1.0890])
D50_XYZ = np.array([0.9642, 1.0000, 0.8249])

# Bradford D65->D50 chromatic adaptation matrix (the standard one used in
# color.org's sRGB v4 profile / Lindbloom's tables).
BRADFORD_D65_TO_D50 = np.array([
    [1.0478112, 0.0228866, -0.0501270],
    [0.0295424, 0.9904844, -0.0170491],
    [-0.0092345, 0.0150436, 0.7521316],
])


def _xy_to_xyz(x, y):
    return np.array([x / y, 1.0, (1 - x - y) / y])


def rec2020_to_xyz_d50_matrix():
    """3x3 matrix mapping linear Rec.2020 RGB -> D50-adapted XYZ (PCS)."""
    p = np.column_stack([
        _xy_to_xyz(*_REC2020_PRIMARIES["r"]),
        _xy_to_xyz(*_REC2020_PRIMARIES["g"]),
        _xy_to_xyz(*_REC2020_PRIMARIES["b"]),
    ])
    w = _xy_to_xyz(*_D65_WHITE_XY)
    w = w / w[1] * D65_XYZ[1]  # normalize Y=1, then scale to the D65 ref used
    s = np.linalg.solve(p, w)
    m_d65 = p * s  # scale each primary column
    m_d50 = BRADFORD_D65_TO_D50 @ m_d65
    return m_d50


# ---------------------------------------------------------------------------
# Raw ICC v4 binary construction
# ---------------------------------------------------------------------------

def _pad4(b):
    pad = (-len(b)) % 4
    return b + b"\x00" * pad


def _s15Fixed16(value):
    return struct.pack(">i", int(round(value * 65536)))


def _tag_XYZType(xyz):
    x, y, z = xyz
    return _pad4(b"XYZ " + b"\x00" * 4 + _s15Fixed16(x) + _s15Fixed16(y) + _s15Fixed16(z))


def _tag_textDescription(ascii_text):
    # 'desc' textDescriptionType (ICC v2-style, still widely accepted by v4 readers)
    text = ascii_text.encode("ascii") + b"\x00"
    body = struct.pack(">I", len(text)) + text
    body += struct.pack(">I", 0)  # Unicode language code
    body += struct.pack(">I", 0)  # Unicode description length
    body += struct.pack(">H", 0)  # ScriptCode code
    body += b"\x00" * 67          # ScriptCode description (67 bytes)
    return _pad4(b"desc" + b"\x00" * 4 + body)


def _tag_textType(ascii_text):
    text = ascii_text.encode("ascii") + b"\x00"
    return _pad4(b"text" + b"\x00" * 4 + text)


def _tag_curv_from_samples(samples_0_1, n_points=1024):
    """ICC 'curv' curveType holding n_points u16 samples of a 0-1 curve."""
    xs = np.linspace(0.0, 1.0, n_points)
    ys = np.clip(samples_0_1(xs), 0.0, 1.0)
    u16 = np.round(ys * 65535.0).astype(np.uint16)
    body = struct.pack(">I", n_points) + u16.astype(">u2").tobytes()
    return _pad4(b"curv" + b"\x00" * 4 + body)


def _tag_s15Fixed16ArrayType(values):
    body = b"".join(_s15Fixed16(v) for v in values)
    return _pad4(b"sf32" + b"\x00" * 4 + body)


def _tag_lumi(nits):
    # 'lumi' tag data is typed as XYZType, with the luminance in cd/m^2
    # stored in the Y channel (ICC.1:2010, 10.18).
    return _tag_XYZType((0.0, nits, 0.0))


def build_rec2020_pq_icc_profile(peak_nits=10000.0, n_curve_points=1024,
                                  description="Rec2020 Gamut with PQ Transfer"):
    """Return bytes of a Rec.2020 gamut / ST 2084 (PQ) ICC v4 profile."""

    m = rec2020_to_xyz_d50_matrix()
    r_xyz, g_xyz, b_xyz = m[:, 0], m[:, 1], m[:, 2]

    def pq_curve(xs):
        # xs are PQ code values in [0,1]; the TRC must map device code value
        # -> linear PCS-relative light, i.e. exactly the PQ EOTF, scaled so
        # that a full-scale (1.0) PQ code == `peak_nits` maps to linear 1.0.
        linear_10000 = pq_eotf(xs)  # 0-1, where 1.0 == 10000 nits
        return linear_10000 * (10000.0 / peak_nits)

    tags = {
        "desc": _tag_textDescription(description),
        "cprt": _tag_textType("No copyright; generated programmatically from public BT.2020/ST 2084 specs."),
        "wtpt": _tag_XYZType(D65_XYZ),
        "chad": _tag_s15Fixed16ArrayType(BRADFORD_D65_TO_D50.flatten().tolist()),
        "rXYZ": _tag_XYZType(r_xyz),
        "gXYZ": _tag_XYZType(g_xyz),
        "bXYZ": _tag_XYZType(b_xyz),
        "rTRC": _tag_curv_from_samples(pq_curve, n_curve_points),
        "gTRC": _tag_curv_from_samples(pq_curve, n_curve_points),
        "bTRC": _tag_curv_from_samples(pq_curve, n_curve_points),
        "lumi": _tag_lumi(peak_nits),
    }

    # rTRC/gTRC/bTRC are identical; dedupe so all three tag-table entries
    # point at the same tag data block (standard ICC practice).
    trc_data = tags["rTRC"]
    tags["gTRC"] = trc_data
    tags["bTRC"] = trc_data

    tag_order = ["desc", "cprt", "wtpt", "chad", "rXYZ", "gXYZ", "bXYZ",
                 "rTRC", "gTRC", "bTRC", "lumi"]

    header_size = 128
    tag_count = len(tag_order)
    tag_table_size = 4 + tag_count * 12
    data_start = header_size + tag_table_size

    tag_table_entries = []
    tag_data_blob = b""
    offsets = {}
    for name in tag_order:
        data = tags[name]
        if id(data) in offsets:
            off, size = offsets[id(data)]
        else:
            off = data_start + len(tag_data_blob)
            size = len(data)
            tag_data_blob += data
            offsets[id(data)] = (off, size)
        tag_table_entries.append((name.encode("ascii"), off, size))

    tag_table = struct.pack(">I", tag_count)
    for sig, off, size in tag_table_entries:
        tag_table += sig + struct.pack(">II", off, size)

    total_size = data_start + len(tag_data_blob)

    header = bytearray(128)
    struct.pack_into(">I", header, 0, total_size)
    header[4:8] = b"\x00\x00\x00\x00"       # CMM type (none)
    struct.pack_into(">I", header, 8, 0x04300000)  # profile version 4.3.0.0
    header[12:16] = b"mntr"                 # device class: display/monitor
    header[16:20] = b"RGB "                 # data colour space
    header[20:24] = b"XYZ "                 # PCS
    # date/time (12 bytes) left as zero
    header[36:40] = b"acsp"                 # profile file signature
    header[40:44] = b"\x00\x00\x00\x00"     # platform
    struct.pack_into(">I", header, 44, 0)   # flags
    header[48:52] = b"\x00\x00\x00\x00"     # device manufacturer
    header[52:56] = b"\x00\x00\x00\x00"     # device model
    # device attributes (8 bytes) left as zero
    struct.pack_into(">I", header, 64, 0)   # rendering intent: perceptual
    # PCS illuminant: D50
    struct.pack_into(">i", header, 68, int(round(D50_XYZ[0] * 65536)))
    struct.pack_into(">i", header, 72, int(round(D50_XYZ[1] * 65536)))
    struct.pack_into(">i", header, 76, int(round(D50_XYZ[2] * 65536)))
    header[80:84] = b"\x00\x00\x00\x00"     # profile creator
    # profile ID (16 bytes MD5) left as zero = "not computed"

    profile = bytes(header) + tag_table + tag_data_blob
    assert len(profile) == total_size
    return profile


if __name__ == "__main__":
    import sys
    out_path = sys.argv[1] if len(sys.argv) > 1 else "rec2020_pq.icc"
    profile_bytes = build_rec2020_pq_icc_profile()
    with open(out_path, "wb") as f:
        f.write(profile_bytes)
    print(f"Wrote {len(profile_bytes)} bytes to {out_path}")
