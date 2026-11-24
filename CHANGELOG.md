# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version of the DCTL is the version of the release; it is also stated in the header line `// Elastic Color Compressor X.Y.Z` of `dctl/ElasticColorCompressor.dctl`.

## [Unreleased]

### Added
- Show Target: fills the frame with the target color in the working space, to check the delivered hex through the Output Transform.
- *Target Output* control, after *Target Color*: ACES 2.0 SDR Rec.709 (default), ACES 2.0 SDR sRGB, None - node code values. With the ACES 2.0 options the target is the scene color that the ACES 2.0 SDR 100-nit Output Transform (Rec.709 limiting primaries, D65) renders to the hex, computed with the inverse Output Transform plus one refinement step against the forward transform, so the hex comes out in the delivered master. *None* takes the hex as code values in the working space.
- `tools/gen_aces2_tables.py`: port of the ACES 2.0 CTL init code (parameters, reach, cusp and upper hull gamma tables) whose output is embedded in the DCTL.
- `tools/verify.py`: certification of the target against OpenColorIO 2.6 (hex -> target -> ACES 2.0 view = hex within 0.02/255 over an 8-bit grid and typical brand colors), the existing checks for every Target Output, and Target Output None against the plain decode. CI installs `opencolorio==2.6.0`.

### Changed
- **The Target Color is no longer decoded as sRGB scene-linear.** Saved grades keep their hex but get the default *Target Output* (ACES 2.0 SDR Rec.709), which moves the target. The old behavior is reproduced exactly by the sRGB working space with *None*.

## [0.5.0] - 2026-11-24

Initial release.

### Added
- `ElasticColorCompressor.dctl`: elastic color compressor in Oklab for DaVinci Resolve. Pulls the hue, saturation and lightness of colors near a target color toward it in three invertible steps; no masks, no inversions, exposure ramps preserved.
- Target Color as an sRGB color picker, so a hex value can be pasted.
- Working spaces: ACEScct, ACEScc, DaVinci Intermediate, ARRI LogC4, Rec.709 Gamma 2.4 and sRGB. Input and output are in the same space.
- Controls, in panel order: Target Color; Zone Hue / Sat / Luma Range (which colors are pulled); Hue / Sat / Luma Compress (how hard); Mix; Output Zone Alpha.
- Zone weight output on the alpha channel (Output Zone Alpha, off by default), multiplied by the input alpha.
- `tools/verify.py`: compiles the DCTL as C++ and checks it numerically (UI spec, matrices, transfer curves, identity, greys, target fixed point, exposure ramps, Jacobian determinant, alpha), run in CI.
- `tools/gen_matrices.py`: derives the RGB to XYZ(D65) matrices of each working space and the Oklab matrices.

[Unreleased]: https://github.com/maxlainz/elastic-color-compressor/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/maxlainz/elastic-color-compressor/releases/tag/v0.5.0
