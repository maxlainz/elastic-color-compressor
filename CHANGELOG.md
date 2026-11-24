# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version of the DCTL is the version of the release; it is also stated in the header line `// Elastic Color Compressor X.Y.Z` of `dctl/ElasticColorCompressor.dctl`.

## [Unreleased]

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
