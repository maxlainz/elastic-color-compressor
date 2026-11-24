#!/usr/bin/env python3
"""Generate the ACES 2.0 Output Transform constants and tables embedded in the DCTL.

    python3 tools/gen_aces2_tables.py      # prints the DCTL block to stdout

The DCTL inverts the ACES 2.0 SDR 100-nit Output Transform (Rec.709 limiting primaries, D65 white)
for the Target Color only. The CTL reference builds its parameters and three tables (reach M,
limiting-gamut cusps on a non-uniform hue grid, upper hull gamma) at init time; a DCTL cannot, so
this script is a float64 numpy port of that init code and prints the result as constant arrays.
The output is pasted verbatim into dctl/ElasticColorCompressor.dctl; tools/verify.py checks it.

Source: ACES 2.0 CTL, aces-core v2.0 (github.com/aces-aswf/aces-core, lib/), functions
  Lib.Academy.Tonescale.ctl         init_TSParams
  Lib.Academy.OutputTransform.ctl   init_JMhParams, init_ODTParams, make_reach_m_table,
                                    make_uniform_hue_gamut_table (find_reach_corners_table,
                                    build_limiting_cusp_corners_tables, extract_sorted_cube_hues,
                                    build_hue_table, build_cusp_table, find_display_cusp_for_hue),
                                    make_upper_hull_gamma_table (generate_gamma_test_data,
                                    evaluate_gamma_fit), determine_hue_linearity_search_range
  Lib.Academy.Utilities.ctl         RGBtoXYZ_f33
Matrices follow the CTL row-vector convention: out = v * M (out[j] = sum_i v[i] M[i][j]).
"""
import math

import numpy as np

# ---- CTL constants (Lib.Academy.OutputTransform.ctl) ----
AP0 = [(0.73470, 0.26530), (0.00000, 1.00000), (0.00010, -0.07700), (0.32168, 0.33767)]
AP1 = [(0.713, 0.293), (0.165, 0.830), (0.128, 0.044), (0.32168, 0.33767)]
REC709 = [(0.64, 0.33), (0.30, 0.60), (0.15, 0.06), (0.3127, 0.3290)]
CAM16_PRI = [(0.8336, 0.1735), (2.3854, -1.4659), (0.087, -0.125), (0.333, 0.333)]

TABLE_SIZE, BASE = 360, 1
TOTAL = TABLE_SIZE + 2
HUE_LIMIT = 360.0
CORNERS = 6
REACH_CUSP_TOL, DISPLAY_CUSP_TOL = 1e-3, 1e-7
GAMMA_MIN, GAMMA_MAX, GAMMA_STEP, GAMMA_ACC = 0.0, 5.0, 0.4, 1e-5
REF_LUM, L_A, Y_B = 100.0, 100.0, 20.0
SURROUND = (0.9, 0.59, 0.9)
J_SCALE = 100.0
CAM_NL_OFFSET = 0.2713 * 100.0
CAM_NL_SCALE = 4.0 * 100.0
MODEL_GAMMA = SURROUND[1] * (1.48 + math.sqrt(Y_B / REF_LUM))
CHROMA_COMPRESS, CHROMA_COMPRESS_FACT = 2.4, 3.3
CHROMA_EXPAND, CHROMA_EXPAND_FACT, CHROMA_EXPAND_THR = 1.3, 0.69, 0.5
SMOOTH_CUSPS, SMOOTH_M, CUSP_MID_BLEND = 0.12, 0.27, 1.3
FOCUS_GAIN_BLEND, FOCUS_DISTANCE, FOCUS_DISTANCE_SCALING = 0.3, 1.35, 1.75
PEAK = 100.0                                   # SDR 100 nits


def rgb_to_xyz_f33(C, Y=1.0):
    """Lib.Academy.Utilities.ctl RGBtoXYZ_f33 (row-vector matrix)."""
    (rx, ry), (gx, gy), (bx, by), (wx, wy) = C
    X, Z = wx * Y / wy, (1 - wx - wy) * Y / wy
    d = rx * (by - gy) + bx * (gy - ry) + gx * (ry - by)
    Sr = (X * (by - gy) - gx * (Y * (by - 1) + by * (X + Z)) + bx * (Y * (gy - 1) + gy * (X + Z))) / d
    Sg = (X * (ry - by) + rx * (Y * (by - 1) + by * (X + Z)) - bx * (Y * (ry - 1) + ry * (X + Z))) / d
    Sb = (X * (gy - ry) - rx * (Y * (gy - 1) + gy * (X + Z)) + gx * (Y * (ry - 1) + ry * (X + Z))) / d
    return np.array([[Sr * rx, Sr * ry, Sr * (1 - rx - ry)],
                     [Sg * gx, Sg * gy, Sg * (1 - gx - gy)],
                     [Sb * bx, Sb * by, Sb * (1 - bx - by)]])


def pacrc_fwd(v):
    a = abs(v) ** 0.42
    return math.copysign(a / (CAM_NL_OFFSET + a), v)


def pacrc_inv(v):
    ra = min(abs(v), 0.99)
    return math.copysign((CAM_NL_OFFSET * ra / (1 - ra)) ** (1 / 0.42), v)


def init_jmh(prims):
    M16 = np.linalg.inv(rgb_to_xyz_f33(CAM16_PRI))
    base = np.array([[2, 1, 1 / 9], [1, -12 / 11, 1 / 9], [1 / 20, 1 / 11, -2 / 9]])
    RGB_TO_XYZ = rgb_to_xyz_f33(prims)
    XYZ_w = np.full(3, REF_LUM) @ RGB_TO_XYZ
    RGB_w = XYZ_w @ M16
    k = 1 / (5 * L_A + 1)
    k4 = k ** 4
    F_L = 0.2 * k4 * (5 * L_A) + 0.1 * (1 - k4) ** 2 * (5 * L_A) ** (1 / 3)
    F_L_n = F_L / REF_LUM
    D = F_L_n * XYZ_w[1] / RGB_w
    RGB_Aw = [pacrc_fwd(x) for x in D * RGB_w]
    cr = CAM_NL_SCALE * base
    A_w = cr[0, 0] * RGB_Aw[0] + cr[1, 0] * RGB_Aw[1] + cr[2, 0] * RGB_Aw[2]
    to_cam = (RGB_TO_XYZ @ M16) * REF_LUM @ np.diag(D)
    to_aab = np.c_[cr[:, 0] / A_w, cr[:, 1:] * 43 * SURROUND[2]]
    return dict(to_cam=to_cam, from_cam=np.linalg.inv(to_cam), to_aab=to_aab, from_aab=np.linalg.inv(to_aab),
                F_L_n=F_L_n, cz=MODEL_GAMMA, A_w_J=pacrc_fwd(F_L))


def rgb_to_aab(rgb, p):
    return np.array([pacrc_fwd(x) for x in np.asarray(rgb, float) @ p["to_cam"]]) @ p["to_aab"]


def rgb_to_jmh(rgb, p):
    A, a, b = rgb_to_aab(rgb, p)
    if A <= 0:
        return np.zeros(3)
    return np.array([J_SCALE * A ** p["cz"], math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0])


def jmh_to_rgb(jmh, p):
    J, M, h = jmh
    hr = math.radians(h)
    Aab = np.array([(J / J_SCALE) ** (1 / p["cz"]), M * math.cos(hr), M * math.sin(hr)])
    return np.array([pacrc_inv(x) for x in Aab @ p["from_aab"]]) @ p["from_cam"]


def y_to_j(Y, p):
    ra = pacrc_fwd(abs(Y) * p["F_L_n"])
    return math.copysign(J_SCALE * (ra / p["A_w_J"]) ** p["cz"], Y)


def init_ts(n):
    """Lib.Academy.Tonescale.ctl init_TSParams."""
    n_r, g, c, c_d, w_g, t_1, r_hit_min, r_hit_max = 100.0, 1.15, 0.18, 10.013, 0.14, 0.04, 128.0, 896.0
    r_hit = r_hit_min + (r_hit_max - r_hit_min) * (math.log(n / n_r) / math.log(10000.0 / 100.0))
    m_0 = n / n_r
    m_1 = 0.5 * (m_0 + math.sqrt(m_0 * (m_0 + 4 * t_1)))
    u = ((r_hit / m_1) / ((r_hit / m_1) + 1)) ** g
    m = m_1 / u
    w_i = math.log(n / 100.0) / math.log(2.0)
    c_t = c_d / n_r * (1 + w_i * w_g)
    g_ip = 0.5 * (c_t + math.sqrt(c_t * (c_t + 4 * t_1)))
    g_ipp2 = -(m_1 * (g_ip / m) ** (1 / g)) / ((g_ip / m) ** (1 / g) - 1)
    w_2 = c / g_ipp2
    s_2 = w_2 * m_1
    u_2 = ((r_hit / m_1) / ((r_hit / m_1) + w_2)) ** g
    m_2 = m_1 / u_2
    return dict(n=n, n_r=n_r, g=g, t_1=t_1, c_t=c_t, s_2=s_2, u_2=u_2, m_2=m_2, forward_limit=8 * r_hit,
                log_peak=math.log10(n / n_r))


# ---- gamut compression helpers used by the table builders ----
def lerp(a, b, t):
    return a + t * (b - a)


def compute_focus_j(cusp_J, mid_J, J_max):
    return lerp(cusp_J, mid_J, min(1.0, CUSP_MID_BLEND - cusp_J / J_max))


def get_focus_gain(J, thr, J_max, focus_dist):
    gain = J_max * focus_dist
    if J > thr:
        adj = math.log10((J_max - thr) / max(0.0001, J_max - J))
        gain *= adj * adj + 1
    return gain


def solve_j_intersect(J, M, focusJ, maxJ, slope_gain):
    Ms = M / slope_gain
    a = Ms / focusJ
    if J < focusJ:
        b, c = 1 - Ms, -J
        return -2 * c / (b + math.sqrt(b * b - 4 * a * c))
    b, c = -(1 + Ms + maxJ * a), maxJ * Ms + J
    return -2 * c / (b - math.sqrt(b * b - 4 * a * c))


def slope_of(Ji, focusJ, J_max, slope_gain):
    d = Ji if Ji < focusJ else J_max - Ji
    return d * (Ji - focusJ) / (focusJ * slope_gain)


def line_boundary_m(J_ax, slope, inv_gamma, J_max, M_max, J_ref):
    shifted = J_ref * (J_ax / J_ref) ** inv_gamma
    return shifted * M_max / (J_max - slope * M_max)


def smin_scaled(a, b, ref):
    s = SMOOTH_CUSPS * ref
    h = max(s - abs(a - b), 0.0) / s
    return min(a, b) - h ** 3 * s / 6


def gamut_boundary_m(JMcusp, J_max, g_top_inv, g_bot_inv, Ji_src, slope, Ji_cusp):
    lower = line_boundary_m(Ji_src, slope, g_bot_inv, JMcusp[0], JMcusp[1], Ji_cusp)
    upper = line_boundary_m(J_max - Ji_src, -slope, g_top_inv, J_max - JMcusp[0], JMcusp[1], J_max - Ji_cusp)
    return smin_scaled(lower, upper, JMcusp[1])


# ---- table builders ----
def cube_corner(i):
    return np.array([1.0 if (i + 1) % 6 < 3 else 0.0, 1.0 if (i + 5) % 6 < 3 else 0.0,
                     1.0 if (i + 3) % 6 < 3 else 0.0])


def cycle(rgb, jmh):
    m = int(np.argmin([j[2] for j in jmh]))
    rgb = [rgb[(i + m) % 6] for i in range(6)]
    jmh = [jmh[(i + m) % 6].copy() for i in range(6)]
    rgb = [rgb[5]] + rgb + [rgb[0]]
    jmh = [jmh[5].copy()] + jmh + [jmh[0].copy()]
    jmh[0][2] -= HUE_LIMIT
    jmh[7][2] += HUE_LIMIT
    return rgb, jmh


def reach_corners(reach, J_max, forward_limit):
    limitA = (J_max / J_SCALE) ** (1 / reach["cz"])
    rgb, jmh = [], []
    for i in range(CORNERS):
        v = cube_corner(i)
        lo, hi = 0.0, forward_limit
        while hi - lo > REACH_CUSP_TOL:
            t = (lo + hi) / 2
            if rgb_to_aab(t * v, reach)[0] < limitA:
                lo = t
            else:
                hi = t
        rgb.append(hi * v)
        jmh.append(rgb_to_jmh(hi * v, reach))
    return cycle(rgb, jmh)[1]


def limiting_corners(limit):
    rgb = [PEAK / REF_LUM * cube_corner(i) for i in range(CORNERS)]
    return cycle(rgb, [rgb_to_jmh(c, limit) for c in rgb])


def sorted_cube_hues(reach_jmh, limit_jmh):
    out, r, l = [], 1, 1
    while r < CORNERS + 1 or l < CORNERS + 1:
        rh, lh = reach_jmh[r][2], limit_jmh[l][2]
        if rh == lh:
            out.append(rh)
            r, l = r + 1, l + 1
        elif rh < lh:
            out.append(rh)
            r += 1
        else:
            out.append(lh)
            l += 1
    return out


def build_hue_table(hues):
    spacing = TABLE_SIZE / HUE_LIMIT
    n = len(hues)
    counts = [0] * (n + 2)
    min_index = 0 if hues[0] == 0.0 else 1
    last = None
    for k in range(n):
        nominal = min(max(round(hues[k] * spacing), min_index), TABLE_SIZE - 1)
        if last == nominal:
            if k > 1 and counts[k - 2] != counts[k - 1] - 1:
                counts[k - 1] -= 1
            else:
                nominal += 1
        counts[k] = min(nominal, TABLE_SIZE - 1)
        min_index = last = nominal
    table = np.zeros(TOTAL)

    def fill(samples, lower, upper, base):
        delta = (upper - lower) / samples if samples else 0.0
        for i in range(samples):
            table[base + i] = lower + i * delta

    total = 0
    fill(counts[0], 0.0, hues[0], total + 1)
    total += counts[0]
    for k in range(1, n):
        s = counts[k] - counts[k - 1]
        fill(s, hues[k - 1], hues[k], total + 1)
        total += s
    fill(TABLE_SIZE - total, hues[n - 1], HUE_LIMIT, total + 1)
    table[0] = table[BASE + TABLE_SIZE - 1] - HUE_LIMIT
    table[BASE + TABLE_SIZE] = table[BASE] + HUE_LIMIT
    return table


def display_cusp(hue, rgb_c, jmh_c, limit):
    upper = next((i for i in range(1, CORNERS + 2) if jmh_c[i][2] > hue), 1)
    lower = upper - 1
    if jmh_c[lower][2] == hue:
        return jmh_c[lower][:2]
    lo_t, hi_t = 0.0, 1.0
    while hi_t - lo_t > DISPLAY_CUSP_TOL:
        t = (lo_t + hi_t) / 2
        h = rgb_to_jmh(lerp(rgb_c[lower], rgb_c[upper], t), limit)[2]
        if h < jmh_c[lower][2]:
            hi_t = t
        elif h >= jmh_c[upper][2]:
            lo_t = t
        elif h > hue:
            hi_t = t
        else:
            lo_t = t
    return rgb_to_jmh(lerp(rgb_c[lower], rgb_c[upper], (lo_t + hi_t) / 2), limit)[:2]


def make_cusp_table(reach, limit, J_max, ts):
    rgb_c, jmh_c = limiting_corners(limit)
    sorted_hues = sorted_cube_hues(reach_corners(reach, J_max, ts["forward_limit"]), jmh_c)
    assert len(sorted_hues) == 2 * CORNERS          # the CTL's build_hue_table assumes 12 distinct corner hues
    hues = build_hue_table(sorted_hues)
    out = np.zeros((TOTAL, 3))
    for i in range(BASE, TOTAL):
        J, M = display_cusp(hues[i], rgb_c, jmh_c, limit)
        out[i] = J, M * (1 + SMOOTH_M * SMOOTH_CUSPS), hues[i]
    out[0] = out[TABLE_SIZE][0], out[TABLE_SIZE][1], hues[0]
    out[BASE + TABLE_SIZE] = out[BASE][0], out[BASE][1], hues[BASE + TABLE_SIZE]
    return out


def make_reach_m_table(reach, J_max):
    out = np.zeros(TOTAL)
    for i in range(TABLE_SIZE):
        hue = i * HUE_LIMIT / TABLE_SIZE
        lo, hi, outside = 0.0, 50.0, False
        while not outside and hi < 1300.0:
            outside = bool((jmh_to_rgb((J_max, hi, hue), reach) < 0).any())
            if not outside:
                lo, hi = hi, hi + 50.0
        while hi - lo > 1e-2:
            m = (hi + lo) / 2
            if (jmh_to_rgb((J_max, m, hue), reach) < 0).any():
                hi = m
            else:
                lo = m
        out[i + BASE] = hi
    out[0], out[BASE + TABLE_SIZE] = out[TABLE_SIZE], out[BASE]
    return out


def make_upper_hull_gamma_table(cusps, P, limit):
    J_max, lum_limit = P["limit_J_max"], PEAK / REF_LUM
    out = np.zeros(TOTAL)
    for i in range(BASE, BASE + TABLE_SIZE):
        cJ, cM, hue = cusps[i]
        thr = lerp(cJ, J_max, FOCUS_GAIN_BLEND)
        focusJ = compute_focus_j(cJ, P["mid_J"], J_max)
        tests = []
        for pos in (0.01, 0.1, 0.5, 0.8, 0.99):
            tJ = lerp(cJ, J_max, pos)
            gain = get_focus_gain(tJ, thr, J_max, P["focus_dist"])
            Ji = solve_j_intersect(tJ, cM, focusJ, J_max, gain)
            tests.append((Ji, slope_of(Ji, focusJ, J_max, gain), solve_j_intersect(cJ, cM, focusJ, J_max, gain)))

        def fits(g_top_inv):
            for Ji, sl, Jc in tests:
                M = gamut_boundary_m((cJ, cM), J_max, g_top_inv, P["lower_hull_gamma_inv"], Ji, sl, Jc)
                if not (jmh_to_rgb((Ji + sl * M, M, hue), limit) > lum_limit).any():
                    return False
            return True

        lo, hi, found = GAMMA_MIN, GAMMA_MIN + GAMMA_STEP, False
        while not found and hi < GAMMA_MAX:
            found = fits(1 / hi)
            if not found:
                lo, hi = hi, hi + GAMMA_STEP
        while hi - lo > GAMMA_ACC:
            g = (hi + lo) / 2
            if fits(1 / g):
                hi = g
            else:
                lo = g
        out[i] = 1 / hi
    out[0], out[TABLE_SIZE + BASE] = out[TABLE_SIZE], out[BASE]
    return out


def init_odt():
    """init_ODTParams(100, Rec.709 D65) of Lib.Academy.OutputTransform.ctl."""
    inp, reach, limit = init_jmh(AP0), init_jmh(AP1), init_jmh(REC709)
    ts = init_ts(PEAK)
    P = dict(input=inp, limit=limit, ts=ts)
    P["limit_J_max"] = y_to_j(PEAK, inp)
    P["reach_M"] = make_reach_m_table(reach, P["limit_J_max"])
    P["sat"] = max(0.2, CHROMA_EXPAND - CHROMA_EXPAND * CHROMA_EXPAND_FACT * ts["log_peak"])
    P["sat_thr"] = CHROMA_EXPAND_THR / PEAK
    P["compr"] = CHROMA_COMPRESS + CHROMA_COMPRESS * CHROMA_COMPRESS_FACT * ts["log_peak"]
    P["chroma_compress_scale"] = (0.03379 * PEAK) ** 0.30596 - 0.45135
    P["mid_J"] = y_to_j(ts["c_t"] * REF_LUM, inp)
    P["focus_dist"] = FOCUS_DISTANCE + FOCUS_DISTANCE * FOCUS_DISTANCE_SCALING * ts["log_peak"]
    P["lower_hull_gamma_inv"] = 1 / (1.14 + 0.07 * ts["log_peak"])
    P["cusps"] = make_cusp_table(reach, limit, P["limit_J_max"], ts)
    P["upper_gamma"] = make_upper_hull_gamma_table(P["cusps"], P, limit)
    return P


# ---- DCTL text ----
def fmt(v):
    """9 significant digits (round-trips float32), as a C float literal."""
    s = np.format_float_positional(float(f"{v:.9g}"), trim="-", unique=True)
    return (s if "." in s else s + ".0") + "f"


def carray(name, values, per_line=8):
    vals = [fmt(v) for v in np.ravel(values)]
    lines = [", ".join(vals[i:i + per_line]) for i in range(0, len(vals), per_line)]
    return f"__CONSTANT__ float {name}[{len(vals)}] = {{\n    " + ",\n    ".join(lines) + "};"


def dctl_block():
    P = init_odt()
    inp, lim, ts = P["input"], P["limit"], P["ts"]
    ap0_to_ap1 = rgb_to_xyz_f33(AP0) @ np.linalg.inv(rgb_to_xyz_f33(AP1))
    scalars = [
        ("A2_F_L_N", inp["F_L_n"]), ("A2_CZ", inp["cz"]), ("A2_A_W_J", inp["A_w_J"]),
        ("A2_NL_OFFSET", CAM_NL_OFFSET), ("A2_J_MAX", P["limit_J_max"]), ("A2_MID_J", P["mid_J"]),
        ("A2_FOCUS_DIST", P["focus_dist"]), ("A2_LOWER_GAMMA_INV", P["lower_hull_gamma_inv"]),
        ("A2_SAT", P["sat"]), ("A2_SAT_THR", P["sat_thr"]), ("A2_COMPR", P["compr"]),
        ("A2_CC_SCALE", P["chroma_compress_scale"]),
        ("A2_TS_N", ts["n"]), ("A2_TS_NR", ts["n_r"]), ("A2_TS_G", ts["g"]), ("A2_TS_T1", ts["t_1"]),
        ("A2_TS_S2", ts["s_2"]), ("A2_TS_U2", ts["u_2"]), ("A2_TS_M2", ts["m_2"]),
        ("A2_TS_FWD_LIMIT", ts["forward_limit"]),
    ]
    out = [f"#define {n} {fmt(v)}" for n, v in scalars]
    out += ["// row-vector 3x3 matrices, out[j] = sum_i v[i] * M[3 i + j]",
            carray("A2_INPUT_RGB_TO_CAM", inp["to_cam"], 3),
            carray("A2_INPUT_CONE_TO_AAB", inp["to_aab"], 3),
            carray("A2_INPUT_AAB_TO_CONE", inp["from_aab"], 3),
            carray("A2_INPUT_CAM_TO_RGB", inp["from_cam"], 3),
            carray("A2_LIMIT_RGB_TO_CAM", lim["to_cam"], 3),
            carray("A2_LIMIT_CONE_TO_AAB", lim["to_aab"], 3),
            carray("A2_LIMIT_AAB_TO_CONE", lim["from_aab"], 3),
            carray("A2_LIMIT_CAM_TO_RGB", lim["from_cam"], 3),
            carray("A2_AP0_TO_AP1", ap0_to_ap1, 3),
            carray("A2_AP1_TO_AP0", np.linalg.inv(ap0_to_ap1), 3),
            "// reach M at J max per degree of hue (index = hue + 1)",
            carray("A2_REACH_M", P["reach_M"]),
            "// limiting gamut cusp (J, M, hue) on the non-uniform hue grid",
            carray("A2_CUSPS", P["cusps"], 6),
            "// upper hull gamma inverse on the same hue grid",
            carray("A2_UPPER_GAMMA_INV", P["upper_gamma"])]
    return "\n".join(out)


if __name__ == "__main__":
    print(dctl_block())
