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

## Target as sRGB

Brand colors are specified as sRGB hex values, and a DCTL has no text field: the color picker is the only way to enter one by paste. The picker value is decoded with the sRGB curve and the Rec.709 primaries, independently of the working space, so the same hex means the same color in every space.

## Verification on the real code

`tools/verify.py` compiles the DCTL as C++ instead of checking a separate numpy model: a model copied by hand can drift from the code it checks. The float64 build (the same source with `float` defined as `double`) gives clean derivatives for the Jacobian determinant; the float32 build is what the GPU runs and is used for the identity and grey checks.
