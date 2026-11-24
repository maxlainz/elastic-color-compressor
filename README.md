# Elastic Color Compressor

An elastic color compressor for DaVinci Resolve, written as a single DCTL file.

Pick a target color (a brand color, a product, a sky) and colors near it are pulled toward it in hue, saturation and lightness, like a color compressor, but with smooth elastic curves instead of a key. There are no masks or qualifiers: neighboring colors move together, so a gradient stays a gradient (no inversions, no steps, no banding), and an exposure ramp stays an exposure ramp. Work happens in Oklab. A soft "zone weight" can also be written to the alpha channel.

The target is entered as an **sRGB color** in a color picker, so a hex value from a brand guide or a design file can be pasted directly.

## Contents

- [Install](#install)
- [Working spaces](#working-spaces)
- [Quick start](#quick-start)
- [Parameters](#parameters)
- [How it works](#how-it-works)
- [Design guarantees](#design-guarantees)
- [Limitations](#limitations)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Install

DCTL effects require **DaVinci Resolve Studio**. The color picker and the alpha output need Resolve 19.1 or later.

1. Download `ElasticColorCompressor.dctl` from the [latest GitHub Release](https://github.com/maxlainz/elastic-color-compressor/releases/latest).
2. Copy it into Resolve's LUT folder (or a subfolder of it). In Resolve, *Project Settings > Color Management > Lookup Table > Open LUT Folder* opens it. Default locations:

   | OS | Path |
   |---|---|
   | macOS | `/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT` |
   | Windows | `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT` |
   | Linux | `/opt/resolve/LUT` |

3. In Resolve, press *Update Lists* in the same Project Settings page, or restart Resolve.
4. Use the **ResolveFX DCTL** plugin. On the Color page, add a node and open *OpenFX > ResolveFX Color > DCTL*, then pick `ElasticColorCompressor` in the plugin's DCTL list.

## Working spaces

Set *Working Space* to the space in which the node receives its pixels. Input and output are always in the same space.

| Working Space | Use it for |
|---|---|
| ACEScct (default) | ACES pipelines, grading between IDT and ODT. Preferred over ACEScc in ACES |
| ACEScc | ACES pipelines using ACEScc |
| DaVinci Intermediate | DaVinci Wide Gamut / DaVinci Intermediate timelines (RCM) |
| ARRI LogC4 | ARRI LogC4 / AWG4 material without a transform |
| Rec.709 Gamma 2.4 | Timelines without color management, clips already in Rec.709 (display-referred) |
| sRGB | Same, with the sRGB transfer function |

If the timeline is in another space, wrap the node with a Color Space Transform. The node cannot detect which space it receives; a wrong space moves the wrong colors.

The **Target Color is always sRGB**, whatever the working space: it is converted to Oklab directly from sRGB, so a hex value means the same color in every space. Note that a brand color as it appears in a graded, display-transformed image is not necessarily the same as its sRGB value; pick it from the brand guide, then refine by eye.

## Quick start

1. **Set the target.** Click the *Target Color* swatch and paste the hex value (in the color dialog, the hex field is in the RGB sliders panel on macOS and labeled *HTML* in Qt dialogs).
2. **See the zone.** Temporarily turn on *Output Zone Alpha* and view the alpha with Resolve's **Highlight** (Shift+H) and *Use OFX Alpha* on the node. Adjust *Zone Hue Range*, *Zone Sat Range* and *Zone Luma Range* until the zone covers the colors you want to pull. Turn *Output Zone Alpha* off again before grading (see [Limitations](#limitations)).
3. **Pull.** Raise *Hue Compress* first, then *Sat Compress*. *Luma Compress* is off by default: it pulls lightness toward the target's lightness and is the strongest move.
4. **Blend.** Use *Mix* to back the whole effect off.

Always test with a continuous hue ramp and an exposure ramp as well as real footage: there must be no steps.

## Parameters

Controls are grouped top to bottom: *Working Space*, **Target**, **Zone** (which colors are pulled), **Compress** (how hard each axis is pulled), then *Mix* and *Output Zone Alpha*.

| Control | Default | Range | What it does |
|---|---|---|---|
| Working Space | ACEScct | see above | Input and output space. Only changes the conversion to and from Oklab. |
| **Target Color** | #003087 | color picker | The color others are pulled toward, as sRGB (hex). |
| **Zone Hue Range deg** | 25 | 3-120 | Width of the zone around the target hue. Wider = more hues pulled, gentler per hue. |
| **Zone Sat Range** | 0.7 | 0.1-3 | Width of the zone around the target saturation, in natural-log units of chroma / lightness (0.7 = roughly half to double). |
| **Zone Luma Range** | 0.25 | 0.02-1 | Width of the zone around the target lightness (Oklab L). Shapes the luma pull and the zone alpha only. |
| **Hue Compress** | 0.5 | 0-0.99 | How strongly hue is pulled toward the target hue. |
| **Sat Compress** | 0.3 | 0-0.99 | How strongly saturation is pulled toward the target's saturation. |
| **Luma Compress** | 0 | 0-0.99 | How strongly Oklab lightness is pulled toward the target's lightness. |
| Mix | 1.0 | 0-1 | Scales all three strengths. Mix 0 returns the input. |
| Output Zone Alpha | off | checkbox | Writes the **zone weight** (where the compressor acts, 0 to 1) to alpha, multiplied by the input alpha. Off = the input alpha passes through. For viewing the zone only. |

Compress 0.99 is the maximum: at 1.0 the curve would flatten to a step and posterize.

## How it works

```
working space -> linear RGB -> XYZ (D65) -> Oklab (L, a, b) -> (L, h, s) with s = ln(C / L)
  1. luma        L -> L'   weight from hue and saturation
  2. hue         h -> h'   weight from saturation
  3. saturation  s -> s'   weight from the new hue h'
-> change in Oklab -> linear RGB (input + change) -> working space
```

Each axis uses the same attractor curve, with x the distance to the target in units of its range:

```
x' = x - strength * x * exp(-x^2 / 2)
```

It pulls hardest near the target and fades to the identity far from it. On the hue circle the periodic version `sin(d) * exp((cos d - 1) / w^2)` is used, so there is no seam at 180 degrees.

Saturation is measured as `s = ln(C / L)` (chroma relative to lightness) rather than chroma: hue and `s` do not change with exposure, so the color change is the same at every exposure and an exposure ramp comes out as an exposure ramp.

The result is applied as a **change** added to the input (not rebuilt from scratch): where nothing moves the change is exactly 0, and greys (chroma 0) never move.

## Design guarantees

These are the invariants the code is written and verified against (`tools/verify.py`):

- **No inversions, no folds.** Each step changes one coordinate with a curve whose slope is at least `1 - strength > 0`, weighted only by coordinates that step does not change. Each step is invertible, so the whole map is: neighboring colors never swap order. Verified as a positive Jacobian determinant over random settings across the full UI ranges, in every working space.
- **Exposure ramps stay exposure ramps.** Along `k * rgb` the output luminance strictly increases and the output chromaticity stays constant.
- **Greys never move**, whatever the target or strength.
- **The target is a fixed point**: a pixel of exactly the target color is not changed.
- **Mix 0 and strengths 0 return the input** (to the float32 round trip of the transfer curve).
- **No hard masks.** Every weight is a smooth (C-infinity) field. The only branches are on UI parameters, the published transfer-function segments, and the `C > 0` guard of `atan2`.
- **Alpha is the zone weight**: a smooth field in [0, 1], 1 at the target, multiplied by the input alpha.

## Limitations

- **Output Zone Alpha changes the picture in Resolve.** Resolve can use a plugin's alpha downstream, so with it on, the graded image changes. It is off by default; use it only to look at the zone.
- Per pixel: the node does not know what the target object is, only its color. Anything with a similar color is pulled too; narrow the ranges or use a window.
- The working space is chosen by hand; the node cannot detect the space of its input.
- The target is sRGB. A color outside the sRGB gamut cannot be entered as a target.
- ACEScc is not invertible below linear 0: the official encoding sends every linear value <= 0 to the floor -0.3584. A pixel below the floor does not come back bit-for-bit, and a move that pushes a channel negative is flattened at the floor. ACEScct has a linear segment below 0 and does not have this problem: prefer ACEScct in ACES pipelines.
- Rec.709 Gamma 2.4 is a pure mirrored power: encoding has infinite slope at 0, so tiny linear values around black give visible codes. sRGB has a linear toe and does not suffer from it.
- Below Oklab L 0.01 (deep below black) saturation is measured against a lightness floor of 0.01.
- Far from the target the move fades smoothly but is not exactly zero until the kernels underflow; with the defaults a color of the opposite hue moves less than 1e-5 code values.
- Always verify by eye: no numerical test replaces looking at real footage and ramps on a monitor.

## Development

```
python3 tools/verify.py        # must end in ALL OK (requires numpy and a C++17 compiler)
python3 tools/gen_matrices.py  # prints the matrix blocks pasted into the DCTL (only if a primary, white or CAT changes)
```

- `tools/verify.py` compiles the DCTL itself as C++ through a small prelude that maps the DCTL built-ins to `<cmath>`, once in float32 and once in float64, and runs every check on that code: UI spec, matrices, transfer curves, identity, greys, target fixed point, exposure ramps, Jacobian determinant, alpha. There is no hand-written model to keep in sync. It runs in CI in a few seconds.
- `tools/gen_matrices.py` derives each space's `RGB -> XYZ(D65)` matrix (primaries normalized per SMPTE RP 177 + Bradford) and the Oklab matrices. Its output is pasted into the DCTL; `verify.py` detects any mismatch.
- Gotchas of DCTL UI parameters: labels in `DEFINE_UI_PARAMS` must not contain parentheses or commas. `Resolve.ValidateDCTL` only parses the UI parameters, so a broken kernel can pass it; real compilation happens when the node loads in Resolve.

See [docs/design-notes.md](docs/design-notes.md) for the reasoning behind the design choices.

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

GPL-3.0-or-later. Copyright (C) 2026 Max Lainz. See [LICENSE](LICENSE).
