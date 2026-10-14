# Complementary spectroscopy features

Reviewed 2026-10-06. Project recommendations, with primary vendor comparisons where stated.

| Priority | Addition | Rationale |
|---|---|---|
| High | Response-calibrated spectra | Add validated wavelength-dependent system response, calibration provenance and uncertainty while preserving raw counts. OceanView provides relative/absolute irradiance workflows; Andor documents relative efficiency correction. [1, 2] |
| High | Multi-spectrum overlays and residuals | Compare references, normalized spectra and differences with linked wavelength cursors; add stacked/waterfall views for temperature/time runs. Solis documents stacked and overlaid views. [2] |
| High | Peak and band analysis | Track fitted peak position, centroid, FWHM, integrated bands and uncertainties with explicit baseline models. OceanView documents peak metrics including FWHM, centroid and integration. [1] |
| High | Drift-aware scan ordering | Add randomized/bidirectional order, interleaved reference points and zero-field blocks. This recommendation follows the project’s magneto-PL use case; it is not claimed as a feature verified in the comparison products. |
| Medium | Saved experiment recipes | Save validated plans, acquisition settings, sensor conditions and processing choices; provide a preview and emulator dry run. OceanView supports reusable project settings and a visual processing schematic. [1, 3] |
| Medium | Background/reference library | Load backgrounds only after strict identity/grid/settings validation; support a bank indexed by exposure/readout and background age checks. This is a project recommendation extending the current strict-signature path. |
| Medium | Absorbance/transmission workflows | Acquire explicit sample/reference/dark sets and expose reference validity. OceanView documents reference/background workflows for transmission and related applications. [1] |
| Later | Calibrated spectral stitching | Build overlap diagnostics, grating/response correction and uncertainty before stitching the new center-wavelength scans. Current code acquires segments only; this recommendation is not an implemented capability. |
| Later | Long-run temperature logging and thermal maps | Stream full-run readings to disk, link spectra to cached temperature windows, and add wavelength–temperature intensity maps. Current monitoring exports only the retained history; temperature control remains separate. |

[1] Ocean Optics. OceanView Installation and Operation Manual. [Source](https://www.oceanoptics.com/wp-content/uploads/2024/05/oceanviewioperation-manual.pdf)

[2] Oxford Instruments Andor. Solis S. [Source](https://andor.oxinst.com/products/solis-software/solis-s)

[3] Ocean Optics. OceanView 2.0. [Source](https://www.oceanoptics.com/software/oceanview/)

[4] Lake Shore Cryotronics. Model 335 Python Driver documentation. [Source](https://lake-shore-python-driver.readthedocs.io/en/latest/model_335.html)

[5] Lake Shore Cryotronics. Model 335 User’s Manual, USB communication section. [Source](https://www.lakeshore.com/docs/default-source/product-downloads/335_manual.pdf?sfvrsn=9f5c5b7f_7)
