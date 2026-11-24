# Design notes

Why the compressor is built the way it is. These choices are settled; change them only with a measured reason.

## Three one-axis steps instead of one 3D pull

Pulling a color toward a target in 3D at once (for example moving `(L, a, b)` a fraction of the way toward the target with a weight that depends on the distance) can fold: two neighboring colors can cross. Splitting the move into three steps, each changing **one** coordinate with a strictly increasing curve and weighted only by coordinates that step leaves alone, makes every step a triangular, invertible map. The composition of invertible maps is invertible, so the whole map cannot fold. The order is luma, then hue (weight from saturation), then saturation (weight from the **new** hue): the last step reads the hue the previous step produced, never the old one, so it never reads a coordinate that has already moved under it.

## Saturation as ln(C / L)

Chroma alone changes with exposure: a darker version of the same color has lower chroma. Measuring saturation as `s = ln(C / L)` makes hue and `s` invariant under exposure (scaling linear RGB), so steps 2 and 3 see the same input along an exposure ramp and apply the same change. The luma step only moves `L` with a monotonic curve. The result: an exposure ramp comes out as an exposure ramp, with strictly increasing luminance and constant chromaticity. The logarithm also makes the saturation distance symmetric: half and double the target saturation are equally far.

## The attractor curve

`x' = x - strength * x * exp(-x^2 / 2)` has slope `1 - strength * (1 - x^2) * exp(-x^2 / 2)`, which is at least `1 - strength` (the minimum is at `x = 0`). With strength clamped to 0.99 the slope is always >= 0.01 > 0: the curve is strictly increasing and never posterizes. It is C-infinity, 0 at the target, and fades to the identity far away. On the hue circle the periodic analogue `d - strength * sin(d) * exp((cos d - 1) / w^2)` has the same lower bound.

## The grey axis shear

With the published Oklab matrices, a neutral RGB does not land exactly on `a = b = 0`: it lands on `(a, b) = L * (NEUTRAL_A, NEUTRAL_B)`, about 1e-4 of L (Oklab's M1 is defined for a D65 white that differs in the last digits). Measuring `a` and `b` from that axis makes chroma exactly 0 on greys, so greys never move. The axis is the same in every working space to 1e-9, because every space is adapted to D65.

## Change added to the input

The output is `input + change`, with the change computed in Oklab and carried back through linear maps (the cube is taken as a difference of cubes, `p1^3 - p0^3 = dp (p1^2 + p1 p0 + p0^2)`). Where nothing moves the change is exactly 0, so the round trip error of the Oklab matrices never reaches untouched pixels, and bright pixels do not lose precision to cancellation.

## Mix scales the strengths

Mix multiplies the three strengths rather than blending output with input. Blending two invertible maps does not have to be invertible; scaling the strength keeps every slope >= `1 - strength * mix > 0`, so the guarantee holds at every Mix.

## Target as the delivered hex

Brand colors are specified as hex values, and a DCTL has no text field: the color picker is the only way to enter one by paste. In advertising the hex has to come out in the delivered master, not in the scene. Decoding the hex with the sRGB curve to a linear Rec.709 *scene* value (the first version) does not achieve that: in an ACES pipeline the node sits before the Output Transform, whose tone scale and gamut mapping change that value on the way to the display.

The target is therefore the scene value that the Output Transform renders to the hex: the hex is decoded with the display EOTF (BT.1886 gamma 2.4 or sRGB) and run through the **inverse** ACES 2.0 Output Transform (SDR 100 nits, Rec.709 limiting primaries, D65), giving ACES2065-1, then AP1 and XYZ (D65) like any pixel. Only the target goes through it; the pixels never do, so every guarantee of the compressor is unchanged. The *None* option decodes the hex in the working space itself, for timelines without color management where the node output is the master.

The official inverse is approximate where the gamut compressor's focus gain depends on J (above its analytical threshold, near the top of the gamut): rendering its result forward misses the hex by up to 0.07/255 (OpenColorIO's own inverse round trip). One fixed-point step against the forward transform, `x1 = x0 + (inv(hex) - inv(fwd(x0)))`, brings the miss to 0.016/255 at most, so the DCTL also carries the forward path. It costs two more transforms per pixel; the target is uniform, but DCTL has no per-frame stage.

The tables (limiting-gamut cusps on a non-uniform hue grid, upper hull gamma, reach M) are built at init in the CTL with searches over thousands of transform evaluations. A DCTL has no init stage and would repeat that per pixel, so `tools/gen_aces2_tables.py` ports the CTL init code to numpy and its output is pasted verbatim as constant arrays (checked by `verify.py`, as for the matrices). One deliberate difference from the CTL: the upper hull gamma is interpolated with the normalised hue weight `(h - h_lo) / (h_hi - h_lo)`, as OpenColorIO 2.6 does; the CTL uses `h - h_lo`, and with it the forward transforms disagree by up to 0.1/255 near white-yellow. OpenColorIO is the ground truth of the certification in `verify.py`.

## Verification on the real code

`tools/verify.py` compiles the DCTL as C++ instead of checking a separate numpy model: a model copied by hand can drift from the code it checks. The float64 build (the same source with `float` defined as `double`) gives clean derivatives for the Jacobian determinant; the float32 build is what the GPU runs and is used for the identity and grey checks.
