#!/usr/bin/env python3
"""Numerical checks for the Elastic Color Compressor DCTL (dctl/ElasticColorCompressor.dctl).

    python3 tools/verify.py

Requires numpy, OpenColorIO 2.6 (`opencolorio`, the ACES 2.0 ground truth) and a C++17 compiler (`c++`,
or set CXX). The DCTL itself is compiled as C++ through a
small prelude that maps the DCTL built-ins to <cmath>, twice: in float32 (what the GPU runs) and in
float64 (the same source with `float` defined as `double`, for derivatives). Every check runs on the
real DCTL code, so there is no hand-written model to keep in sync.
Mutation testing: ECC_DCTL=<copy.dctl> python3 tools/verify.py
Exit code 1 on any failure; the last line is "ALL OK" otherwise. Runs in a few seconds, < 200 MB.

Checks:
  1. Text: DEFINE_UI_PARAMS exactly the spec (saved grades depend on names, order and defaults);
     both matrix blocks printed by tools/gen_matrices.py and the block printed by
     tools/gen_aces2_tables.py appear verbatim; NEUTRAL_A / NEUTRAL_B equal the Oklab grey axis of
     every working space.
  2. Curves: per working space, decode is strictly increasing, encode(decode(x)) round trips,
     manufacturer reference values.
  3. Identity: Mix 0 and all strengths 0 return the input (to float32 round trip of the curve).
  4. Greys never move, in every working space, at full strength, for every Target Output.
  5. The target colour is a fixed point, in every working space, for every Target Output.
  6. Exposure ramps stay exposure ramps: along k * rgb the output luminance is strictly increasing
     and the output chromaticity is constant (random settings cycle through the Target Outputs).
  7. No fold: the Jacobian determinant of the whole map (codes in, codes out) is > 0 around the
     target, over random parameters across the full UI ranges incl. Target Output (float64 build,
     central differences).
  8. Alpha: in [0, 1], 1 at the target, multiplied by the input alpha; input alpha when off.
  9. Far from the target (opposite hue) the move is negligible with the defaults.
 10. Target Output None: the target equals the plain decode of the picker values, every working space.
 11. ACES 2.0 certification: hex -> the DCTL's target (ACES2065-1, float32 build) -> OCIO's forward
     ACES 2.0 SDR 100-nit view for the Rec.1886 and the sRGB display returns the hex within 0.02/255,
     over an 8-bit grid of the cube and typical brand colours. Two regions are reported separately,
     with OCIO's own inverse as the reference: BT.1886 codes with a channel below 12/255 (pure 2.4
     power, infinite slope at 0: float32 noise of 1e-7 in linear light is 0.3/255 in code values;
     compared in linear light) and the near-white corner (two channels 255, third >= 252), where the
     tonescale is nearly flat and neither OCIO's inverse nor the DCTL's reaches the hex.
"""
import ctypes
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import PyOpenColorIO as ocio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_aces2_tables as ga  # noqa: E402
import gen_matrices as gm  # noqa: E402

DCTL_PATH = Path(os.environ.get("ECC_DCTL") or ROOT / "dctl" / "ElasticColorCompressor.dctl")
TEXT = DCTL_PATH.read_text(encoding="utf-8")
print(f"info checks run on {DCTL_PATH}")
FAIL = []


def check(cond, msg):
    cond = bool(cond)
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


WS = ["ACEScct", "ACEScc", "DaVinci Intermediate", "ARRI LogC4", "Rec.709 Gamma 2.4", "sRGB"]
WS_MAT = ["AP1", "AP1", "DWG", "AWG4", "REC709", "REC709"]
TO = ["ACES 2.0 SDR Rec.709", "ACES 2.0 SDR sRGB", "None"]                 # Target Output options

# =====================================================================
# 1. Text
# =====================================================================
UI_SPEC = [
    "DEFINE_UI_PARAMS(workingSpace, Working Space, DCTLUI_COMBO_BOX, 0, { WS_ACESCCT, WS_ACESCC, WS_DWG, WS_LOGC4, "
    "WS_REC709, WS_SRGB }, { ACEScct, ACEScc, DaVinci Intermediate, ARRI LogC4, Rec.709 Gamma 2.4, sRGB })",
    "DEFINE_UI_PARAMS(target, Target Color, DCTLUI_COLOR_PICKER, 0.0f, 0.188235294f, 0.529411765f)",
    "DEFINE_UI_PARAMS(targetOutput, Target Output, DCTLUI_COMBO_BOX, 0, { TO_ACES2_REC709, TO_ACES2_SRGB, TO_NONE }, "
    "{ ACES 2.0 SDR Rec.709, ACES 2.0 SDR sRGB, None - node code values })",
    "DEFINE_UI_PARAMS(hueRange, Zone Hue Range deg, DCTLUI_SLIDER_FLOAT, 25.0, 3.0, 120.0, 0.5)",
    "DEFINE_UI_PARAMS(satRange, Zone Sat Range, DCTLUI_SLIDER_FLOAT, 0.7, 0.1, 3.0, 0.01)",
    "DEFINE_UI_PARAMS(lumaRange, Zone Luma Range, DCTLUI_SLIDER_FLOAT, 0.25, 0.02, 1.0, 0.01)",
    "DEFINE_UI_PARAMS(hueCompress, Hue Compress, DCTLUI_SLIDER_FLOAT, 0.5, 0.0, 0.99, 0.01)",
    "DEFINE_UI_PARAMS(satCompress, Sat Compress, DCTLUI_SLIDER_FLOAT, 0.3, 0.0, 0.99, 0.01)",
    "DEFINE_UI_PARAMS(lumaCompress, Luma Compress, DCTLUI_SLIDER_FLOAT, 0.0, 0.0, 0.99, 0.01)",
    "DEFINE_UI_PARAMS(mix, Mix, DCTLUI_SLIDER_FLOAT, 1.0, 0.0, 1.0, 0.01)",
    "DEFINE_UI_PARAMS(outputAlpha, Output Zone Alpha, DCTLUI_CHECK_BOX, 0)",
    "DEFINE_UI_PARAMS(showTarget, Show Target, DCTLUI_CHECK_BOX, 0)",
]
ui_lines = [re.sub(r"\s+", " ", ln).strip() for ln in TEXT.splitlines() if ln.startswith("DEFINE_UI_PARAMS")]
ui_lines = [re.sub(r"\(\s*", "(", re.sub(r",\s*", ", ", ln)) for ln in ui_lines]
check(ui_lines == UI_SPEC, f"DEFINE_UI_PARAMS equal the spec ({len(ui_lines)}/{len(UI_SPEC)})")
for got, want in zip(ui_lines, UI_SPEC):
    if got != want:
        print(f"     got  {got}\n     want {want}")

b1, b2 = gm.dctl_blocks()
check(b1 in TEXT, "working-space matrix block equals tools/gen_matrices.py output verbatim")
check(b2 in TEXT, "Oklab matrix block equals tools/gen_matrices.py output verbatim")
check(ga.dctl_block() in TEXT, "ACES 2.0 constants and tables equal tools/gen_aces2_tables.py output verbatim")

defs = dict(re.findall(r"#define\s+(NEUTRAL_[AB])\s+\(?(-?[\d.e+-]+)f\)?", TEXT))
NA, NB = float(defs.get("NEUTRAL_A", "nan")), float(defs.get("NEUTRAL_B", "nan"))
for name, enum, prim, white in gm.SPACES:
    lms = gm.M1 @ (gm.rgb_to_xyz_d65(prim, white) @ np.ones(3))
    lab = gm.M2 @ np.cbrt(lms)
    err = max(abs(lab[1] / lab[0] - NA), abs(lab[2] / lab[0] - NB))
    check(err < 1e-9, f"grey axis of {enum} = (NEUTRAL_A, NEUTRAL_B) * L (err {err:.1e})")

# =====================================================================
# Build: the DCTL compiled as C++ (float32 and float64)
# =====================================================================
PRELUDE = r"""
#include <cmath>
#ifdef ECC_F64
#define float double
#endif
struct float3 { union { float x; float r; }; union { float y; float g; }; union { float z; float b; }; };
struct float4 { float x, y, z, w; };
struct DCTLColorPickerParam { float r, g, b; };
static inline float3 make_float3(float a, float b, float c) { float3 v; v.x = a; v.y = b; v.z = c; return v; }
static inline float4 make_float4(float a, float b, float c, float d) { float4 v; v.x = a; v.y = b; v.z = c; v.w = d; return v; }
static inline float3 operator+(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
static inline float3 operator-(float3 a, float3 b) { return make_float3(a.x - b.x, a.y - b.y, a.z - b.z); }
#define __DEVICE__ static inline
#define DEFINE_DCTL_ALPHA_MODE_STRAIGHT
#define DEFINE_UI_TOOLTIP(...)
#define __CONSTANT__ static const
#define __CONSTANTREF__ const
#define _expf(x) std::exp(x)
#define _exp2f(x) std::exp2(x)
#define _logf(x) std::log(x)
#define _log2f(x) std::log2(x)
#define _log10f(x) std::log10(x)
#define _powf(x, y) std::pow(x, y)
#define _sqrtf(x) std::sqrt(x)
#define _sinf(x) std::sin(x)
#define _cosf(x) std::cos(x)
#define _atan2f(y, x) std::atan2(y, x)
#define _fabs(x) std::fabs(x)
#define _copysignf(x, y) std::copysign(x, y)
#define _round(x) std::round(x)
#define _fmaxf(x, y) std::fmax(x, y)
#define _fminf(x, y) std::fmin(x, y)
#define _clampf(x, a, b) std::fmin(std::fmax(x, a), b)
"""


def ui_decl(line):
    """C++ declaration for one DEFINE_UI_PARAMS line, plus the number of floats it takes in ecc_set()."""
    name, kind, rest = re.match(r"DEFINE_UI_PARAMS\((\w+),[^,]*,\s*(DCTLUI_\w+),\s*(.*)\)\s*$", line).groups()
    if kind == "DCTLUI_COMBO_BOX":
        default, enums = re.match(r"(\d+),\s*\{([^}]*)\}", rest).groups()
        return f"enum {{{enums}}}; int {name} = {default};", f"{name} = (int)p[i++];"
    if kind == "DCTLUI_CHECK_BOX":
        return f"int {name} = {rest.strip()};", f"{name} = (int)p[i++];"
    if kind == "DCTLUI_COLOR_PICKER":
        # Resolve gives the picker its own type (DCTLColorPickerParam, only .r .g .b), not a float3
        return (f"DCTLColorPickerParam {name} = {{{rest}}};",
                f"{name}.r = p[i]; {name}.g = p[i + 1]; {name}.b = p[i + 2]; i += 3;")
    return f"float {name} = {rest.split(',')[0]};", f"{name} = p[i++];"


def build(f64):
    src, setters = [], []
    for line in TEXT.splitlines():
        if line.startswith("DEFINE_UI_PARAMS"):
            decl, setter = ui_decl(line)
            src.append(decl)
            setters.append(setter)
        else:
            src.append(line)
    wrap = r"""
extern "C" {
void ecc_set(const float* p) { int i = 0; %s }
void ecc_run(int n, const float* in, float* out) {
    for (int k = 0; k < n; k++) {
        float4 o = transform(1, 1, 0, 0, in[4 * k], in[4 * k + 1], in[4 * k + 2], in[4 * k + 3]);
        out[4 * k] = o.x; out[4 * k + 1] = o.y; out[4 * k + 2] = o.z; out[4 * k + 3] = o.w;
    }
}
void ecc_curve(int n, int ws, int enc, const float* in, float* out) {
    for (int k = 0; k < n; k++) {
        float3 c = make_float3(in[3 * k], in[3 * k + 1], in[3 * k + 2]);
        float3 o = enc ? encode_log(c, ws) : decode_log(c, ws);
        out[3 * k] = o.x; out[3 * k + 1] = o.y; out[3 * k + 2] = o.z;
    }
}
void ecc_target_codes(int ws, float* out) {
    float3 o = encode_log(xyz_to_rgb(target_xyz(make_float3(target.r, target.g, target.b), targetOutput, ws), ws), ws);
    out[0] = o.x; out[1] = o.y; out[2] = o.z;
}
// fn 0: picker code values -> ACES2065-1 target (arg = sRGB display); 1: ACES 2.0 forward,
// ACES2065-1 -> display linear Rec.709; 2: ACES 2.0 inverse, display linear -> ACES2065-1
void ecc_aces2(int n, int fn, int arg, const float* in, float* out) {
    for (int k = 0; k < n; k++) {
        float3 c = make_float3(in[3 * k], in[3 * k + 1], in[3 * k + 2]);
        float3 o = fn == 0 ? aces2_target(c, arg) : fn == 1 ? aces2_fwd(c) : aces2_inv(c);
        out[3 * k] = o.x; out[3 * k + 1] = o.y; out[3 * k + 2] = o.z;
    }
}
}
""" % " ".join(setters)
    tmp = Path(tempfile.mkdtemp(prefix="ecc_"))
    cpp, lib = tmp / "dctl.cpp", tmp / "dctl.so"
    cpp.write_text(PRELUDE + "\n".join(src) + wrap)
    cmd = [os.environ.get("CXX", "c++"), "-std=c++17", "-O2", "-ffp-contract=off", "-shared", "-fPIC",
           "-Wall", "-Wno-unused-function", "-Wno-unused-parameter", "-o", str(lib), str(cpp)]
    if f64:
        cmd.insert(1, "-DECC_F64")
    r = subprocess.run(cmd, capture_output=True, text=True)
    check(r.returncode == 0 and "warning" not in r.stderr,
          f"DCTL compiles as C++ ({'float64' if f64 else 'float32'}) without warnings")
    if r.returncode:
        print(r.stderr)
        sys.exit(1)
    if r.stderr:
        print(r.stderr)
    return ctypes.CDLL(str(lib)), (np.float64 if f64 else np.float32)


class Dctl:
    def __init__(self, f64):
        self.lib, self.dt = build(f64)

    def ptr(self, a):
        return a.ctypes.data_as(ctypes.c_void_p)

    def set(self, **kw):
        p = dict(DEFAULTS, **kw)
        v = []
        for k in ORDER:
            v += list(np.atleast_1d(p[k]))
        a = np.ascontiguousarray(v, self.dt)
        self.lib.ecc_set(self.ptr(a))

    def run(self, rgb, alpha=1.0):
        rgb = np.asarray(rgb, np.float64).reshape(-1, 3)
        x = np.ascontiguousarray(np.c_[rgb, np.broadcast_to(alpha, len(rgb))], self.dt)
        y = np.empty_like(x)
        self.lib.ecc_run(len(x), self.ptr(x), self.ptr(y))
        return y.astype(np.float64)

    def curve(self, ws, x, enc):
        x = np.ascontiguousarray(np.asarray(x, np.float64).reshape(-1, 3), self.dt)
        y = np.empty_like(x)
        self.lib.ecc_curve(len(x), ws, int(enc), self.ptr(x), self.ptr(y))
        return y.astype(np.float64)

    def aces2(self, x, fn, arg=0):
        x = np.ascontiguousarray(np.asarray(x, np.float64).reshape(-1, 3), self.dt)
        y = np.empty_like(x)
        self.lib.ecc_aces2(len(x), fn, arg, self.ptr(x), self.ptr(y))
        return y.astype(np.float64)

    def target_codes(self, ws):
        y = np.empty(3, self.dt)
        self.lib.ecc_target_codes(ws, self.ptr(y))
        return y.astype(np.float64)


# the DCTL's parameter order, read from the DCTL (ecc_set() takes them in this order)
ORDER = re.findall(r"^DEFINE_UI_PARAMS\((\w+)", TEXT, re.M)
DEFAULTS = dict(workingSpace=0, targetOutput=0, target=(0.0, 48 / 255, 135 / 255), hueCompress=0.5, satCompress=0.3,
                lumaCompress=0.0, hueRange=25.0, satRange=0.7, lumaRange=0.25, mix=1.0, outputAlpha=0, showTarget=0)
STRONG = dict(hueCompress=0.99, satCompress=0.99, lumaCompress=0.99)
F32, F64 = Dctl(False), Dctl(True)
rng = np.random.default_rng(7)


def to_xyz(lin, ws):
    return lin @ gm.rgb_to_xyz_d65(*[s for s in gm.SPACES if s[1] == WS_MAT[ws]][0][2:]).T


def from_xyz(xyz, ws):
    return xyz @ np.linalg.inv(gm.rgb_to_xyz_d65(*[s for s in gm.SPACES if s[1] == WS_MAT[ws]][0][2:])).T


def codes_of(lin, ws):
    return F64.curve(ws, lin, True)


# =====================================================================
# 2. Curves
# =====================================================================
for ws, name in enumerate(WS):
    lo = -0.3 if ws in (0, 1) else -0.05
    x = np.linspace(lo, 1.2, 20001)
    X = np.c_[x, x, x]
    lin = F64.curve(ws, X, False)[:, 0]
    check(np.all(np.diff(lin) > 0), f"{name}: decode strictly increasing on [{lo}, 1.2]")
    keep = x > -0.3013698630136986 if ws == 1 else np.full_like(x, True, bool)   # ACEScc floor: not invertible
    back = F64.curve(ws, F64.curve(ws, X, False), True)[:, 0]
    err = np.abs(back - x)[keep].max()
    check(err < 1e-8, f"{name}: encode(decode(x)) round trip, float64 (err {err:.1e}; float32 literals)")
    back32 = F32.curve(ws, F32.curve(ws, X, False), True)[:, 0]
    err32 = np.abs(back32 - x)[keep].max()
    check(err32 < 2e-6, f"{name}: encode(decode(x)) round trip, float32 (err {err32:.1e})")

REFS = [(0, 0.18, 0.4135884), (2, 0.18, 0.336043), (2, 1.0, 0.513837), (2, 100.0, 1.0),
        (3, 0.0, 0.0929), (3, 0.18, 0.2784), (4, 0.18, 0.18 ** (1 / 2.4)), (5, 0.0031308, 0.04045)]
for ws, lin, code in REFS:
    got = F64.curve(ws, [lin] * 3, True)[0, 0]
    tol = 5e-5 if ws == 3 else 2e-6
    check(abs(got - code) < tol, f"{WS[ws]}: encode({lin}) = {code} (got {got:.7f})")


# =====================================================================
# 3-5. Identity, greys, target fixed point (float32, what the GPU runs)
# =====================================================================
def sample_codes(ws, n):
    lin = rng.uniform(-0.01, 1.0, (n, 3)) ** 1 * rng.choice([0.05, 0.3, 1.0, 4.0], (n, 1))
    if ws >= 4:
        lin = np.clip(lin, 0, 1)
    return codes_of(lin, ws)


for ws, name in enumerate(WS):
    c = sample_codes(ws, 4000)
    ident = F32.curve(ws, F32.curve(ws, c, False), True)       # the float32 curve round trip alone
    for label, kw in (("Mix 0", dict(STRONG, mix=0.0)),
                      ("strengths 0", dict(hueCompress=0.0, satCompress=0.0, lumaCompress=0.0))):
        F32.set(workingSpace=ws, **kw)
        out = F32.run(c)[:, :3]
        check(np.array_equal(out, ident), f"{name}: {label} returns the input bit-exactly (up to the curve round trip)")

    g = codes_of(np.repeat(np.geomspace(1e-4, 16, 400)[:, None], 3, 1), ws)
    gid = F32.curve(ws, F32.curve(ws, g, False), True)
    for to, to_name in enumerate(TO):
        err = 0.0
        for tgt in (DEFAULTS["target"], (0.5, 0.5, 0.5), (1.0, 0.2, 0.0)):
            F32.set(workingSpace=ws, targetOutput=to, target=tgt, lumaRange=1.0, satRange=3.0, **STRONG)
            err = max(err, np.abs(F32.run(g)[:, :3] - gid).max())
        check(err < 2e-6, f"{name}, {to_name}: greys do not move, 3 targets incl. a grey one (max {err:.1e})")

    for to, to_name in enumerate(TO):
        err = 0.0
        for tgt in (DEFAULTS["target"], (1.0, 0.8, 0.0), (0.9, 0.1, 0.4), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)):
            F32.set(workingSpace=ws, targetOutput=to, target=tgt, **STRONG)
            t = F32.target_codes(ws)
            out = F32.run(t)[0, :3]
            # compared in linear light (Rec.709 Gamma 2.4 has an infinite slope at code 0), relative above 1
            tl = F64.curve(ws, t, False)
            err = max(err, np.abs(F64.curve(ws, out, False) - tl).max() / max(1.0, np.abs(tl).max()))
        check(err < 2e-6, f"{name}, {to_name}: 5 targets incl. black and white are fixed points (max {err:.1e})")

# =====================================================================
# 6. Exposure ramps stay exposure ramps (float64)
# =====================================================================
k = np.geomspace(2 ** -8, 2 ** 6, 3000)
for ws, name in enumerate(WS):
    worst, drift = np.inf, 0.0
    for trial in range(12):
        F64.set(workingSpace=ws, targetOutput=trial % 3, target=tuple(rng.uniform(0, 1, 3)), lumaRange=rng.uniform(0.02, 1.0),
                hueRange=rng.uniform(3, 120), satRange=rng.uniform(0.1, 3), **STRONG)
        base = rng.uniform(0.0, 1.0, 3) * 0.2 + 0.001
        lin = k[:, None] * base
        if ws >= 4:
            lin = lin[lin.max(1) <= 1.0]
        out = F64.curve(ws, F64.run(codes_of(lin, ws))[:, :3], False)
        XYZ = to_xyz(out, ws)
        Y = XYZ[:, 1]
        worst = min(worst, (np.diff(Y) / Y[1:]).min())
        xy = (XYZ[:, :2] / XYZ.sum(1, keepdims=True))[Y > 1e-5]     # above the L floor 0.01 (Y 1e-6)
        drift = max(drift, np.ptp(xy, 0).max())
    check(worst > 0, f"{name}: output luminance strictly increasing along exposure ramps, all Target Outputs "
                     f"(min step {worst:.1e})")
    check(drift < 1e-6, f"{name}: output chromaticity constant along exposure ramps (drift {drift:.1e})")


# =====================================================================
# 7. No fold: Jacobian determinant > 0 (float64, central differences in code values)
# =====================================================================
def jac_det(dctl, c, h=1e-6):
    J = np.empty((len(c), 3, 3))
    for j in range(3):
        e = np.zeros(3)
        e[j] = h
        J[:, :, j] = (dctl.run(c + e)[:, :3] - dctl.run(c - e)[:, :3]) / (2 * h)
    det = np.linalg.det(J)
    # a strong pull towards an extreme target (Target Output None can put it at the edge of a wide gamut) bends
    # the map within 1e-6 code values: re-measure non-positive points with a step 100x smaller (a real fold stays
    # negative as the step shrinks)
    bad = det <= 0
    if bad.any() and h > 1e-8:
        det[bad] = jac_det(dctl, c[bad], 1e-8)
    return det


for ws, name in enumerate(WS):
    worst = np.inf
    for trial in range(60):
        p = dict(workingSpace=ws, targetOutput=trial % 3, target=tuple(rng.uniform(0, 1, 3)), hueCompress=rng.choice([0.99, rng.uniform(0, .99)]),
                 satCompress=rng.choice([0.99, rng.uniform(0, .99)]), lumaCompress=rng.choice([0.99, rng.uniform(0, .99)]),
                 hueRange=rng.uniform(3, 120), satRange=rng.uniform(0.1, 3), lumaRange=rng.uniform(0.02, 1),
                 mix=rng.choice([1.0, rng.uniform(0, 1)]))
        F64.set(**p)
        t = F64.target_codes(ws)
        tl = F64.curve(ws, t, False)[0]
        # colours around the target: random gains per channel and exposure, so hue, saturation and luma all vary
        lin = tl * rng.lognormal(0, 0.35, (1500, 3)) * rng.lognormal(0, 0.8, (1500, 1)) + rng.normal(0, 0.01, (1500, 3))
        lin = np.abs(lin) + 1e-3
        if ws >= 4:
            lin = np.clip(lin, 0, 1)
        c = codes_of(lin, ws)
        det = jac_det(F64, c)
        if ws == 1:   # ACEScc encodes every lin <= 0 to its floor (official, not invertible): det 0 there
            det = det[F64.curve(ws, F64.run(c)[:, :3], False).min(1) > 1e-4]
        worst = min(worst, det.min())
    check(worst > 0, f"{name}: Jacobian determinant > 0 around the target, 60 random settings, all Target Outputs "
                     f"(min {worst:.1e})")

# =====================================================================
# 8. Alpha
# =====================================================================
for ws, name in enumerate(WS):
    F32.set(workingSpace=ws, outputAlpha=1)
    c = sample_codes(ws, 3000)
    a1 = F32.run(c, 1.0)[:, 3]
    a5 = F32.run(c, 0.5)[:, 3]
    check(a1.min() >= 0 and a1.max() <= 1, f"{name}: zone alpha in [0, 1]")
    check(np.allclose(a5, 0.5 * a1, atol=1e-7), f"{name}: zone alpha multiplied by the input alpha")
    err = 0.0
    for to in range(len(TO)):
        F32.set(workingSpace=ws, outputAlpha=1, targetOutput=to)
        err = max(err, abs(F32.run(F32.target_codes(ws))[0, 3] - 1))
    check(err < 1e-5, f"{name}: zone alpha is 1 at the target, all Target Outputs")
    F32.set(workingSpace=ws, outputAlpha=0)
    check(np.all(F32.run(c, 0.7)[:, 3] == np.float32(0.7)), f"{name}: Output Zone Alpha off passes the input alpha")

# =====================================================================
# 9. Far from the target
# =====================================================================
F64.set()
t = F64.curve(0, F64.target_codes(0), False)[0]
lab = gm.M2 @ np.cbrt(gm.M1 @ to_xyz(t[None], 0)[0])
lab[1:] *= -0.25                                                 # same L, a quarter of the chroma, hue + 180 deg
opp = from_xyz((np.linalg.inv(gm.M1) @ (np.linalg.inv(gm.M2) @ lab) ** 3)[None], 0)[0]
check(opp.min() > 0, "the opposite-hue colour of the default target is inside AP1")
move = np.abs(F64.run(codes_of(opp[None], 0))[0, :3] - codes_of(opp[None], 0)[0]).max()
check(move < 1e-3, f"defaults: a colour of the opposite hue moves < 1e-3 code values (moves {move:.1e})")

# =====================================================================
# 10. Target Output None: the target is the plain decode of the picker values (float64)
# =====================================================================
for ws, name in enumerate(WS):
    err = 0.0
    for tgt in rng.uniform(0, 1, (50, 3)):
        F64.set(workingSpace=ws, targetOutput=2, target=tuple(tgt))
        want = F64.curve(ws, tgt, False)
        err = max(err, np.abs(F64.curve(ws, F64.target_codes(ws), False) - want).max() / max(1.0, np.abs(want).max()))
    # tolerance: the matrices are float32 literals, so RGB -> XYZ -> RGB round trips to ~1e-7
    check(err < 3e-7, f"{name}, None: target = decode(picker) in the working space, 50 targets (max rel {err:.1e})")

# =====================================================================
# 11. ACES 2.0 certification against OpenColorIO (float32 build, what the GPU runs)
# =====================================================================
CFG = ocio.Config.CreateFromBuiltinConfig("studio-config-latest")
VIEW = "ACES 2.0 - SDR 100 nits (Rec.709)"
BRANDS = ["003087", "E30613", "FFD100", "00A651", "DA291C", "0033A0", "FF6600", "6CC24A", "7F3F98", "1D1D1B"]
lv = np.r_[6, np.arange(0, 256, 15)]                             # 19 levels incl. 0, 255 (all corners) and 6 (sRGB toe)
cube = np.stack(np.meshgrid(lv, lv, lv, indexing="ij"), -1).reshape(-1, 3)
brands = np.array([[int(h[i:i + 2], 16) for i in (0, 2, 4)] for h in BRANDS])
nw = np.arange(252, 256)
near_white = np.stack(np.meshgrid(nw, nw, nw, indexing="ij"), -1).reshape(-1, 3)
hexes = np.unique(np.r_[cube, brands, near_white], axis=0)
in_nw = (hexes.min(1) >= 252) & (hexes.min(1) < 255)            # within 3 codes of white, white itself excluded
cv = hexes / 255.0
for srgb, disp in ((0, "Rec.1886 Rec.709 - Display"), (1, "sRGB - Display")):
    def ocio_apply(x, direction):
        y = np.ascontiguousarray(x, np.float32)
        CFG.getProcessor("ACES2065-1", disp, VIEW, direction).getDefaultCPUProcessor().applyRGB(y)
        return y.astype(np.float64)

    def eotf(c):
        c = np.clip(c, 0, None)
        return c ** 2.4 if not srgb else np.where(c < 0.055 / 1.4, c / 12.9232102, ((c + 0.055) / 1.055) ** 2.4)

    out = ocio_apply(F32.aces2(cv, 0, srgb), ocio.TRANSFORM_DIR_FORWARD)
    ref = ocio_apply(ocio_apply(cv, ocio.TRANSFORM_DIR_INVERSE), ocio.TRANSFORM_DIR_FORWARD)   # OCIO's own round trip
    err, err_ref = np.abs(out - cv).max(1) * 255, np.abs(ref - cv).max(1) * 255
    dark = (hexes.min(1) < 12) if not srgb else np.zeros(len(hexes), bool)
    main = ~in_nw & ~dark
    w = np.where(main)[0][err[main].argmax()]
    check(err[main].max() <= 0.02,
          f"{disp}: hex -> target -> OCIO ACES 2.0 view = hex within 0.02/255, {main.sum()} colours "
          f"(max {err[main].max():.4f}/255 at #{''.join(f'{v:02X}' for v in hexes[w])}; OCIO's own inverse "
          f"{err_ref[main].max():.4f}/255)")
    if dark.any():
        lin, lin_ref = np.abs(eotf(out) - eotf(cv)).max(1)[dark], np.abs(eotf(ref) - eotf(cv)).max(1)[dark]
        check(lin.max() <= 3e-5,
              f"{disp}: a channel below 12/255, {dark.sum()} colours: display linear light within 3e-5 of white (max "
              f"{lin.max():.1e}; OCIO's own inverse {lin_ref.max():.1e}). In code values {err[dark].max():.3f}/255 "
              f"(OCIO's own {err_ref[dark].max():.3f}/255): BT.1886 is a pure 2.4 power, float32 noise near 0 is amplified")
    check(np.all(err[in_nw] <= err_ref[in_nw] + 0.02),
          f"{disp}: within 3 codes of white, {in_nw.sum()} colours: no worse than OCIO's own inverse + 0.02/255 "
          f"(max {err[in_nw].max():.3f}/255, OCIO's own {err_ref[in_nw].max():.3f}/255; see Limitations in README)")

# =====================================================================
# 12. Show Target: the frame is the target in the working space; through OCIO it is the hex
# =====================================================================
c = np.array([[0.1, 0.5, 0.9], [0.4, 0.4, 0.4]])
for ws, name in enumerate(WS):
    for to in range(3):
        F32.set(workingSpace=ws, targetOutput=to, showTarget=1)
        out = F32.run(c, 0.3)
        check(np.allclose(out[:, :3], F32.target_codes(ws), rtol=0, atol=1e-6) and np.all(out[:, 3] == np.float32(0.3)),
              f"{name}, Target Output {to}: Show Target outputs the target codes and passes the alpha")
AP1_TO_AP0 = np.linalg.inv(np.array([[1.4514393161, -0.2365107469, -0.2149285693],
                                     [-0.0765537734, 1.1762296998, -0.0996759264],
                                     [0.0083161484, -0.0060324498, 0.9977163014]]))
for srgb, disp in ((0, "Rec.1886 Rec.709 - Display"), (1, "sRGB - Display")):
    worst = worst_lin = 0.0
    for h in BRANDS:
        hexv = np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)]) / 255
        F32.set(workingSpace=0, targetOutput=srgb, target=tuple(hexv), showTarget=1)
        ap0 = np.ascontiguousarray(F64.curve(0, F32.run(c[:1])[:, :3], False) @ AP1_TO_AP0.T, np.float32)
        CFG.getProcessor("ACES2065-1", disp, VIEW, ocio.TRANSFORM_DIR_FORWARD).getDefaultCPUProcessor().applyRGB(ap0)
        ok_codes = hexv >= 12 / 255 if not srgb else np.ones(3, bool)   # BT.1886: dark channels in linear light
        worst = max(worst, (np.abs(ap0[0] - hexv) * 255)[ok_codes].max(initial=0))
        worst_lin = max(worst_lin, np.abs(np.clip(ap0[0], 0, None) ** 2.4 - hexv ** 2.4)[~ok_codes].max(initial=0))
    check(worst <= 0.05 and worst_lin <= 3e-5,
          f"{disp}: Show Target in ACEScct through the OCIO ACES 2.0 view = hex for the brand colours (max "
          f"{worst:.4f}/255; channels below 12/255 of BT.1886 in linear light, max {worst_lin:.1e} of white)")

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    sys.exit(1)
print("ALL OK")
