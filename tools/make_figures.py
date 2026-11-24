#!/usr/bin/env python3
"""Regenerates the README figures in docs/img/ by running the real DCTL.

    python3 tools/make_figures.py

Requires numpy, Pillow and a C++17 compiler (`c++`, or set CXX). Every image that shows the effect is
the output of dctl/ElasticColorCompressor.dctl, compiled as C++ with the same harness as tools/verify.py.
The DCTL runs in its sRGB working space (with Target Output "None - node code values" where the DCTL has it), so the target hex,
input and output code values are displayed directly (view transform: sRGB). Positions are Oklab, measured
on the DCTL's output; the 3D projection is drawn here with Pillow. The only parts not computed by the
DCTL are the drawing, labels and the "hard qualifier" comparison, a plain numpy model of a key plus a hue
shift. Output is deterministic: the same inputs give byte-identical files.
"""
import ctypes
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
sys.path.insert(0, str(ROOT / "tools"))
import gen_matrices as gm  # noqa: E402

# The C++ build of the DCTL is the one in verify.py (its section from PRELUDE up to the F32/F64 instances).
# Running that section here keeps one harness: the figures run exactly the code the checks run.
_src = (ROOT / "tools" / "verify.py").read_text(encoding="utf-8")
_ns = dict(TEXT=(ROOT / "dctl" / "ElasticColorCompressor.dctl").read_text(encoding="utf-8"),
           check=lambda ok, msg: ok or sys.exit("build failed: " + msg),
           ctypes=ctypes, os=os, re=re, subprocess=subprocess, sys=sys, tempfile=tempfile, Path=Path, np=np)
exec(_src[_src.index('PRELUDE = r"""'):_src.index("F32, F64 = Dctl(")], _ns)
DCTL = _ns["Dctl"](True)
WS_SRGB = 5

TARGET_HEX = "8E44AD"                       # a brand purple with room in sRGB on every side (blue, magenta, greys)
TARGET = tuple(int(TARGET_HEX[i:i + 2], 16) / 255 for i in (0, 2, 4))
TO_NONE = 2                                 # Target Output "None - node code values": the hex is the node's code
STRENGTHS = dict(hueCompress=0.6, satCompress=0.5, lumaCompress=0.4)
SETTINGS = dict(workingSpace=WS_SRGB, targetOutput=TO_NONE, target=TARGET, hueRange=35.0, satRange=0.7, lumaRange=0.2,
                **STRENGTHS)
SETTINGS_TXT = "Hue 0.6, Sat 0.5, Luma 0.4; Zone Hue 35, Sat 0.7, Luma 0.2"

BG, FG, MUTED, GRID = (28, 28, 30), (225, 225, 225), (150, 150, 155), (60, 60, 64)
M709 = gm.rgb_to_xyz_d65(*[s for s in gm.SPACES if s[1] == "REC709"][0][2:])


def font(size):
    return ImageFont.load_default(size=size)


# ---------- colour conversions (only to build inputs and to measure outputs, never to apply the effect) ----------
def lab_to_lin(lab):
    return ((lab @ np.linalg.inv(gm.M2).T) ** 3) @ np.linalg.inv(gm.M1).T @ np.linalg.inv(M709).T


def lin_to_lab(lin):
    return np.cbrt(lin @ M709.T @ gm.M1.T) @ gm.M2.T


def codes(lin):
    return DCTL.curve(WS_SRGB, lin, True)


def lin(c):
    return DCTL.curve(WS_SRGB, c, False)


def run(c, **kw):
    DCTL.set(**dict(SETTINGS, **kw))
    return DCTL.run(c)[:, :3]


def to8(c):
    return np.clip(np.round(np.asarray(c) * 255), 0, 255).astype(np.uint8)


T_LAB = lin_to_lab(lin(np.array([TARGET])))[0]
T_L, T_C, T_H = T_LAB[0], np.hypot(*T_LAB[1:]), np.arctan2(T_LAB[2], T_LAB[1])


def in_gamut(l):
    return np.all((l >= 0) & (l <= 1), -1)


def qualifier(lab, shift=0.9):
    """Comparison only (not this tool): a hard key on hue and saturation, then hue pulled 90 % toward the target."""
    h = np.arctan2(lab[:, 2], lab[:, 1])
    d = (h - T_H + np.pi) % (2 * np.pi) - np.pi
    with np.errstate(divide="ignore"):                          # greys: s = -inf, outside the key
        s = np.log(np.hypot(lab[:, 1], lab[:, 2]) / lab[:, 0] / (T_C / T_L))
    key = (np.abs(d) < np.radians(SETTINGS["hueRange"])) & (np.abs(s) < SETTINGS["satRange"])
    h2 = h - shift * d * key
    C = np.hypot(lab[:, 1], lab[:, 2])
    return np.stack([lab[:, 0], C * np.cos(h2), C * np.sin(h2)], -1)


# =====================================================================
# 3D lattice: an Oklab grid of sRGB colors, drawn at the DCTL's output positions in its output colors
# =====================================================================
def lattice():
    """Nodes on a grid aligned with the target hue (grey axis and target are nodes), kept inside sRGB, and edges.
    Two lightness layers: the target's, and a faint one below it that shows the lightness pull."""
    step = T_C / 3
    L = T_L + 0.15 * np.arange(-1, 1)
    I, J, K = np.meshgrid(np.arange(-1, 6), np.arange(-3, 4), np.arange(len(L)), indexing="ij")
    along, across = I * step, J * step                             # along / across the target hue
    ch, sh = np.cos(T_H), np.sin(T_H)
    lab = np.stack([L[K], along * ch - across * sh, along * sh + across * ch], -1).reshape(-1, 3)
    idx = np.stack([I, J, K], -1).reshape(-1, 3)
    keep = in_gamut(lab_to_lin(lab))
    lab, idx = lab[keep], idx[keep]
    where = {tuple(v): n for n, v in enumerate(idx)}
    edges = [(n, where[tuple(v + d)]) for n, v in enumerate(idx) for d in np.eye(3, dtype=int)
             if tuple(v + d) in where]
    return lab, np.array(edges), idx[:, 2] == len(L) - 1


CENTER = T_LAB[1:] / 2                      # turn about the midpoint between the grey axis and the target
AZ0 = np.arctan2(-T_LAB[1], -T_LAB[2])      # azimuth that puts the target nearest the viewer


def ecc(lab, f=1.0):
    """The real DCTL at a fraction f of the figure strengths: output Oklab position and output code values."""
    c = run(codes(lab_to_lin(lab)), **{k: f * v for k, v in STRENGTHS.items()})
    return lin_to_lab(lin(c)), c


def key(lab, f=1.0):
    o = qualifier(lab, 0.9 * f)
    return o, codes(lab_to_lin(o))


def render3d(lab, col, edges, main, az, el=0.75, size=(640, 560), scale=1250.0, ss=2, mark=None):
    """Perspective view, painter's algorithm (far to near). Nodes off the main layer and their edges are faint."""
    W, H = size[0] * ss, size[1] * ss
    im = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(im)
    x, y, z = lab[:, 1] - CENTER[0], lab[:, 2] - CENTER[1], lab[:, 0] - T_L
    xr, yr = np.cos(az) * x - np.sin(az) * y, np.sin(az) * x + np.cos(az) * y
    up, d = z * np.cos(el) + yr * np.sin(el), yr * np.cos(el) - z * np.sin(el)
    f = 1.4 / (1.4 + d)
    px = np.stack([W / 2 + xr * f * scale * ss, H / 2 - up * f * scale * ss], -1)
    col = col.astype(float)

    def shade(c, t):
        return tuple(int(v) for v in np.round(c * (1 - t) + np.array(BG) * t))

    items = [(d[a] / 2 + d[b] / 2, 0, a, b) for a, b in edges] + [(d[n] - 1e-3, 1, n, n) for n in range(len(lab))]
    for depth, kind, a, b in sorted(items, key=lambda t: -t[0]):
        bright = main[a] and main[b]
        if kind == 0:
            dr.line([tuple(px[a]), tuple(px[b])], fill=shade((col[a] + col[b]) / 2, 0 if bright else 0.6),
                    width=round((2.6 if bright else 1.2) * ss * f[a]))
        elif bright:
            r = 4.2 * ss * f[a]
            X, Y = px[a]
            dr.ellipse([X - r, Y - r, X + r, Y + r], fill=shade(col[a], 0), outline=shade(col[a] * 0.5, 0), width=ss)
    if mark is not None:
        X, Y = px[mark]
        r = 12 * ss * f[mark]
        dr.ellipse([X - r, Y - r, X + r, Y + r], outline=FG, width=2 * ss)
    return im.resize(size, Image.BOX)


def caption(dr, xy, lines, size=13):
    for n, (text, colr) in enumerate(lines):
        dr.text((xy[0], xy[1] + n * (size + 7)), text, fill=colr, font=font(size))


def target_node(lab):
    return int(np.argmin(np.abs(lab - T_LAB).sum(1)))


def timeline(n=72):
    """Per frame: strength fraction (0 -> full -> 0 twice, lingering at both ends) and a slow swing of the camera
    around the side that faces the target, so its neighbourhood is never behind other edges."""
    for i in range(n):
        p = i / n
        f = 0.5 - 0.5 * np.cos(4 * np.pi * p)
        yield f * f * (3 - 2 * f), AZ0 + 0.6 * np.sin(2 * np.pi * p)


def strength_bar(dr, x, y, f):
    dr.text((x - 92, y - 2), "strength", fill=MUTED, font=font(15))
    dr.rectangle([x, y, x + 200, y + 14], outline=MUTED)
    dr.rectangle([x + 2, y + 2, x + 2 + round(196 * f), y + 12], fill=tuple(to8(TARGET)))


def save_gif(frames, name):
    frames = [im.quantize(colors=64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for im in frames]
    frames[0].save(OUT / name, save_all=True, append_images=frames[1:], duration=75, loop=0, optimize=True,
                   disposal=1)


# =====================================================================
# 1. Animation: the lattice swings while the DCTL's strength goes 0 -> full -> 0
# =====================================================================
def fig_warp():
    lab, edges, main = lattice()
    t = target_node(lab)
    W, H = 860, 640
    frames = []
    for f, az in timeline():
        out, c = ecc(lab, f)
        im = Image.new("RGB", (W, H), BG)
        im.paste(render3d(out, to8(c), edges, main, az, size=(W, H - 130), scale=1500.0, mark=t), (0, 50))
        dr = ImageDraw.Draw(im)
        dr.text((24, 16), f"Elastic Color Compressor, target #{TARGET_HEX}", fill=FG, font=font(18))
        strength_bar(dr, W - 224, 20, f)
        caption(dr, (24, H - 70), [
            (f"Oklab lattice of sRGB colors at the target's lightness (ring: #{TARGET_HEX}), faint: a layer below it.",
             MUTED),
            ("Each node: the DCTL output color at its output position, computed in its sRGB working space.", MUTED),
            (f"Full strength: {SETTINGS_TXT}.", MUTED)])
        frames.append(im)
    save_gif(frames, "warp.gif")


# =====================================================================
# 2. Animation, side by side: the same lattice under this DCTL and under a hard qualifier
# =====================================================================
def fig_vs_key():
    lab, edges, main = lattice()
    t = target_node(lab)
    out, _ = ecc(lab)
    # no folds: every edge keeps its direction (its component along the original edge stays positive)
    e0, e1 = edges[:, 0], edges[:, 1]
    assert np.all(((out[e1] - out[e0]) * (lab[e1] - lab[e0])).sum(1) > 0)
    PW, PH, PAD = 440, 420, 10
    W, H = 2 * PW + 3 * PAD, 80 + PH + 3 * 20 + 14
    frames = []
    for f, az in timeline():
        im = Image.new("RGB", (W, H), BG)
        dr = ImageDraw.Draw(im)
        for n, (title, (o, cc)) in enumerate([("This DCTL: the lattice bends", ecc(lab, f)),
                                              ("Hard qualifier + hue shift: it tears", key(lab, f))]):
            im.paste(render3d(o, to8(cc), edges, main, az, size=(PW, PH), scale=1000.0, mark=t),
                     (PAD + n * (PW + PAD), 80))
            dr.text((PAD + n * (PW + PAD) + 12, 52), title, fill=FG, font=font(16))
        dr.text((PAD + 12, 16), f"Same lattice, target #{TARGET_HEX} (ring)", fill=FG, font=font(18))
        strength_bar(dr, W - PAD - 212, 20, f)
        caption(dr, (PAD + 12, 80 + PH + 4), [
            (f"Left: the DCTL in its sRGB working space ({SETTINGS_TXT}).", MUTED),
            ("Right, for comparison only: a plain model of a hue/saturation key with a hue shift of up to 90 %.",
             MUTED),
            ("Only what is inside the key moves, so the lattice tears at the key edge.", MUTED)])
        frames.append(im)
    save_gif(frames, "vs-key.gif")


# =====================================================================
# 3. Pipeline diagram
# =====================================================================
def fig_pipeline():
    boxes = [("Working space", "ACEScct, DI, LogC4, ..."), ("Linear RGB", "decode the curve"),
             ("Oklab", "L, hue, s = ln(C / L)"), ("1. Luma", "L pulled to target L"),
             ("2. Hue", "hue pulled to target hue"), ("3. Saturation", "s pulled to target s"),
             ("Back", "input + change")]
    W, H, bw, bh, gap = 1000, 150, 120, 70, 20
    x0 = (W - len(boxes) * bw - (len(boxes) - 1) * gap) / 2
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           'font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{W}" height="{H}" rx="8" fill="rgb{BG}"/>',
           '<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
           f'<path d="M0,0 L8,4 L0,8 z" fill="rgb{MUTED}"/></marker></defs>']
    y = 40
    for i, (title, sub) in enumerate(boxes):
        x = x0 + i * (bw + gap)
        step = title[0].isdigit()
        fill = "#1f3a5f" if step else "#3a3a3e"
        out.append(f'<rect x="{x:.0f}" y="{y}" width="{bw}" height="{bh}" rx="6" fill="{fill}" stroke="rgb{MUTED}"/>')
        out.append(f'<text x="{x + bw / 2:.0f}" y="{y + 28}" fill="rgb{FG}" font-size="14" font-weight="bold" '
                   f'text-anchor="middle">{title}</text>')
        out.append(f'<text x="{x + bw / 2:.0f}" y="{y + 48}" fill="rgb{MUTED}" font-size="10.5" '
                   f'text-anchor="middle">{sub}</text>')
        if i:
            out.append(f'<line x1="{x - gap + 2:.0f}" y1="{y + bh / 2}" x2="{x - 3:.0f}" y2="{y + bh / 2}" '
                       f'stroke="rgb{MUTED}" stroke-width="1.5" marker-end="url(#arr)"/>')
    sx = x0 + 3 * (bw + gap)
    out.append(f'<text x="{sx + (3 * bw + 2 * gap) / 2:.0f}" y="{y + bh + 26}" fill="rgb{MUTED}" font-size="12" '
               'text-anchor="middle">each step moves one coordinate with a curve that never goes flat</text>')
    out.append(f'<text x="{x0:.0f}" y="24" fill="rgb{FG}" font-size="14">Per pixel, no key: '
               'same space in and out</text>')
    out.append("</svg>")
    (OUT / "pipeline.svg").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    fig_warp()
    fig_vs_key()
    fig_pipeline()
    for p in sorted(OUT.iterdir()):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size // 1024} KB")
