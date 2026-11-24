#!/usr/bin/env python3
"""Regenerates the README figures in docs/img/ by running the real DCTL.

    python3 tools/make_figures.py

Requires numpy, Pillow and a C++17 compiler (`c++`, or set CXX). Every image that shows the effect is
the output of dctl/ElasticColorCompressor.dctl, compiled as C++ with the same harness as tools/verify.py.
The DCTL runs in its sRGB working space, so input and output code values are displayed directly
(view transform: sRGB). The only parts not computed by the DCTL are the axes, labels and the
"hard qualifier" comparison, which is a plain numpy model of a key plus a hue shift.
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

TARGET_HEX = "3A75C4"                       # a mid brand blue, with cyan and purple neighbours
TARGET = tuple(int(TARGET_HEX[i:i + 2], 16) / 255 for i in (0, 2, 4))
SETTINGS = dict(workingSpace=WS_SRGB, target=TARGET, hueRange=35.0, satRange=0.7, hueCompress=0.9, satCompress=0.6)
SETTINGS_TXT = "Hue Compress 0.9, Sat Compress 0.6, Zone Hue Range 35, Zone Sat Range 0.7"

BG, FG, MUTED, GRID = (28, 28, 30), (225, 225, 225), (150, 150, 155), (60, 60, 64)
M709 = gm.rgb_to_xyz_d65(*[s for s in gm.SPACES if s[1] == "REC709"][0][2:])


def font(size):
    return ImageFont.load_default(size=size)


# ---------- colour conversions (only to build inputs and to measure outputs, never to apply the effect) ----------
def lab_to_lin(lab):
    return ((lab @ np.linalg.inv(gm.M2).T) ** 3) @ np.linalg.inv(gm.M1).T @ np.linalg.inv(M709).T


def lin_to_lab(lin):
    return np.cbrt(lin @ M709.T @ gm.M1.T) @ gm.M2.T


def lch(L, C, h):
    return np.stack([L + 0 * h, C * np.cos(h), C * np.sin(h)], -1)


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
    s = np.log(np.hypot(lab[:, 1], lab[:, 2]) / lab[:, 0] / (T_C / T_L))
    key = (np.abs(d) < np.radians(SETTINGS["hueRange"])) & (np.abs(s) < SETTINGS["satRange"])
    h2 = h - shift * d * key
    C = np.hypot(lab[:, 1], lab[:, 2])
    return np.stack([lab[:, 0], C * np.cos(h2), C * np.sin(h2)], -1)


# =====================================================================
# 1. Ramps: hue, saturation and exposure, before and after
# =====================================================================
def fig_ramps():
    W, PAD, SH = 960, 40, 44
    n = W - 2 * PAD
    x = np.linspace(-1, 1, n)
    # hue ramp: +-90 deg around the target hue at the target lightness; C / L 0.16 keeps cyan inside sRGB
    h = T_H + np.radians(90) * x
    hue_in = codes(lab_to_lin(lch(T_L, 0.16 * T_L, h)))
    # saturation ramp: grey to beyond the target, at the target hue and lightness
    cmax = 0.33 * T_L
    sat_in = codes(lab_to_lin(lch(T_L, np.linspace(0.0, cmax, n), T_H + 0 * x)))
    # exposure ramp: a colour 18 deg off the target hue, -5 to +1 stop (no channel clips)
    off = lab_to_lin(lch(T_L, 0.2 * T_L, np.array([T_H + np.radians(18)])))[0]
    exp_in = codes(off[None] * 2.0 ** np.linspace(-5, 1, n)[:, None])
    assert all(in_gamut(lin(c)).all() for c in (hue_in, sat_in, exp_in))
    out = lin_to_lab(lin(run(hue_in)))
    assert np.all(np.diff(np.unwrap(np.arctan2(out[:, 2], out[:, 1]))) > 0), "hue ramp must stay in order"
    rows = [
        ("Hue ramp, before", hue_in, (n - 1) / 2),
        ("after (this DCTL): hues bunch toward the target, the ramp stays smooth", run(hue_in), (n - 1) / 2),
        ("for comparison, a hard qualifier + hue shift: a step at each edge of the key",
         codes(lab_to_lin(qualifier(lin_to_lab(lin(hue_in))))), (n - 1) / 2),
        ("Saturation ramp, before (grey to beyond the target)", sat_in, T_C / cmax * (n - 1)),
        ("after: saturations near the target's bunch toward it; grey stays grey", run(sat_in), T_C / cmax * (n - 1)),
        ("Exposure ramp of an off-target blue, before (-5 to +1 stop)", exp_in, None),
        ("after: the same hue and saturation pull at every exposure", run(exp_in), None),
    ]
    gaps = {3: 18, 5: 18}
    H = 70 + sum(22 + SH + 6 + gaps.get(i, 0) for i in range(len(rows))) + 30
    im = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(im)
    dr.text((PAD, 18), f"Target #{TARGET_HEX}   ({SETTINGS_TXT})", fill=FG, font=font(17))
    dr.rectangle([W - PAD - 40, 14, W - PAD, 40], fill=tuple(to8(TARGET)))
    y = 62
    for i, (label, c, mark) in enumerate(rows):
        y += gaps.get(i, 0)
        dr.text((PAD, y), label, fill=FG if label.startswith(("Hue", "Sat", "Exp")) else MUTED, font=font(15))
        y += 22
        strip = np.repeat(to8(c)[None], SH, 0)
        im.paste(Image.fromarray(strip), (PAD, y))
        if mark is not None:                                   # the target's position on the input ramp
            mx = PAD + mark
            dr.polygon([(mx - 6, y + SH + 7), (mx + 6, y + SH + 7), (mx, y + SH)], fill=FG)
        y += SH + 6
    dr.text((PAD, H - 24), "Rendered by the DCTL in its sRGB working space; codes displayed directly (sRGB view).",
            fill=MUTED, font=font(13))
    im.save(OUT / "ramps.png", optimize=True)


# =====================================================================
# 2. Vectorscope view (Oklab a, b at the target's lightness): where each colour goes
# =====================================================================
def scope_panel(title, move, size=440, scale=1000.0, ss=2):
    S = size * ss
    im = Image.new("RGB", (S, S), BG)
    dr = ImageDraw.Draw(im)
    cx = cy = S / 2
    k = scale * ss                                              # pixels per unit of a or b

    def px(a, b):
        return cx + a * k, cy - b * k

    for r in (0.05, 0.1, 0.15, 0.2):  # Oklab chroma rings
        dr.ellipse([cx - r * k, cy - r * k, cx + r * k, cy + r * k], outline=GRID, width=ss)
    dr.line([0, cy, S, cy], fill=GRID, width=ss)
    dr.line([cx, 0, cx, S], fill=GRID, width=ss)
    g = np.linspace(-0.2, 0.2, 25)
    A, B = np.meshgrid(g, g)
    lab = np.stack([np.full(A.size, T_L), A.ravel(), B.ravel()], -1)
    keep = in_gamut(lab_to_lin(lab)) & (np.hypot(lab[:, 1], lab[:, 2]) > 0.01)
    lab = lab[keep]
    out_lab, out_codes = move(lab)
    col = to8(out_codes)
    for (a0, b0), (a1, b1), c in zip(lab[:, 1:], out_lab[:, 1:], col):
        x0, y0 = px(a0, b0)
        x1, y1 = px(a1, b1)
        if np.hypot(x1 - x0, y1 - y0) > 1.5 * ss:
            dr.line([x0, y0, x1, y1], fill=(200, 200, 200), width=ss)
        rr = 4.5 * ss
        dr.ellipse([x1 - rr, y1 - rr, x1 + rr, y1 + rr], fill=tuple(c))
    tx, ty = px(T_LAB[1], T_LAB[2])
    dr.ellipse([tx - 9 * ss, ty - 9 * ss, tx + 9 * ss, ty + 9 * ss], outline=(255, 255, 255), width=2 * ss)
    im = im.resize((size, size), Image.LANCZOS)
    ImageDraw.Draw(im).text((14, 10), title, fill=FG, font=font(15))
    return im


def fig_scope():
    def ecc(lab):
        c = run(codes(lab_to_lin(lab)))
        return lin_to_lab(lin(c)), c

    def key(lab):
        o = qualifier(lab)
        return o, codes(lab_to_lin(o))

    left = scope_panel("This DCTL: a smooth pull that fades with distance", ecc)
    right = scope_panel("For comparison: hard qualifier + hue shift", key)
    W, PAD = left.width * 2 + 3 * 20, 20
    im = Image.new("RGB", (W, left.height + 2 * PAD + 30), BG)
    im.paste(left, (PAD, PAD))
    im.paste(right, (2 * PAD + left.width, PAD))
    ImageDraw.Draw(im).text(
        (PAD, left.height + PAD + 8),
        f"Oklab a/b plane at the target's lightness (circle: #{TARGET_HEX}). Dots: output color at its new position;"
        " lines: the move.", fill=MUTED, font=font(13))
    im.save(OUT / "scope.png", optimize=True)


# =====================================================================
# 3. The attractor curve, measured on the DCTL (luma axis, along the target's own exposure ramp)
# =====================================================================
def fig_curve():
    wL = 0.05
    k = np.geomspace(0.05, 6, 4000)
    tlin = lin(np.array([TARGET]))[0]
    c = codes(k[:, None] * tlin)
    L_in = lin_to_lab(lin(c))[:, 0]
    x = (L_in - T_L) / wL
    sel = np.abs(x) <= 3.0
    W, H, P = 900, 470, 60
    sx = lambda v: P + (v + 3) / 6 * (W - 2 * P)                    # noqa: E731
    sy = lambda v: H - P - (v + 3) / 6 * (H - 2 * P)                # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           'font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{W}" height="{H}" rx="8" fill="rgb{BG}"/>']
    for v in range(-3, 4):
        out.append(f'<line x1="{sx(v):.1f}" y1="{sy(-3):.1f}" x2="{sx(v):.1f}" y2="{sy(3):.1f}" stroke="rgb{GRID}"/>')
        out.append(f'<line x1="{sx(-3):.1f}" y1="{sy(v):.1f}" x2="{sx(3):.1f}" y2="{sy(v):.1f}" stroke="rgb{GRID}"/>')
        out.append(f'<text x="{sx(v):.1f}" y="{sy(-3) + 20:.1f}" fill="rgb{MUTED}" font-size="13" text-anchor="middle">{v}</text>')
        out.append(f'<text x="{sx(-3) - 10:.1f}" y="{sy(v) + 4:.1f}" fill="rgb{MUTED}" font-size="13" text-anchor="end">{v}</text>')
    out.append(f'<line x1="{sx(-3)}" y1="{sy(-3)}" x2="{sx(3)}" y2="{sy(3)}" stroke="rgb{MUTED}" '
               'stroke-dasharray="6 5" stroke-width="1.5"/>')
    colours = {0.5: "#7fb2ff", 0.8: "#ffb454", 0.99: "#ff6b6b"}
    for s, col in colours.items():
        y = (lin_to_lab(lin(run(c, hueCompress=0, satCompress=0, lumaCompress=s, lumaRange=wL)))[:, 0] - T_L) / wL
        assert np.all(np.diff(y[sel]) > 0), "attractor curve must be strictly increasing"
        pts = " ".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(x[sel][::8], y[sel][::8]) if abs(b) <= 3.0)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.5"/>')
    ly = 80
    for label, col, dash in [("strength 0 (identity)", f"rgb{MUTED}", ' stroke-dasharray="6 5"')] + \
            [(f"strength {s}", col, "") for s, col in colours.items()]:
        out.append(f'<line x1="{P + 20}" y1="{ly}" x2="{P + 50}" y2="{ly}" stroke="{col}" stroke-width="2.5"{dash}/>')
        out.append(f'<text x="{P + 58}" y="{ly + 5}" fill="rgb{FG}" font-size="14">{label}</text>')
        ly += 22
    out += [f'<text x="{P}" y="34" fill="rgb{FG}" font-size="17">x\' = x - strength &#183; x &#183; exp(-x&#178; / 2)'
            '</text>',
            f'<text x="{sx(0.3):.1f}" y="{sy(-2):.1f}" fill="rgb{FG}" font-size="14">At 0.99 the slope at the target is 0.01:</text>',
            f'<text x="{sx(0.3):.1f}" y="{sy(-2) + 20:.1f}" fill="rgb{FG}" font-size="14">squeezed 100 times but still rising,</text>',
            f'<text x="{sx(0.3):.1f}" y="{sy(-2) + 40:.1f}" fill="rgb{FG}" font-size="14">so colors never merge or swap order.</text>',
            f'<text x="{W / 2}" y="{H - 14}" fill="rgb{MUTED}" font-size="13" text-anchor="middle">'
            'input: distance to the target, in zone ranges</text>',
            f'<text x="18" y="{H / 2}" fill="rgb{MUTED}" font-size="13" text-anchor="middle" '
            f'transform="rotate(-90 18 {H / 2})">output distance</text>', "</svg>"]
    (OUT / "attractor.svg").write_text("\n".join(out) + "\n", encoding="utf-8")


# =====================================================================
# 4. Pipeline diagram
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
    fig_ramps()
    fig_scope()
    fig_curve()
    fig_pipeline()
    for p in sorted(OUT.iterdir()):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size // 1024} KB")
