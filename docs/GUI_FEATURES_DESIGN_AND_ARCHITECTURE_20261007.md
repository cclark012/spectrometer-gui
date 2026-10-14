# October 7 feature and architecture guide

Revision 2026-10-07.1 continues the September 14 acquisition architecture and October 6 scan/temperature additions. This guide describes the implemented fixes and the new post-acquisition analysis workspace. The candidate is verified by numerical and state-machine checks; real Qt and lab acceptance remain open.

## Acquisition and controls

**Scans** is now a left-side workspace. Its selector routes to the existing laser power/calibration panel or the ordinary parameter scan panel. The coordinators and SequenceArbiter remain responsible for ownership, settings acknowledgements, settling, temperature qualification, acquisition, autosave and drain-before-release cancellation. Switching the displayed scan kind does not introduce a second acquisition route.

Exposure, center, elapsed-time and temperature scans retain their October 6 contracts. Scheduled times are software deadlines, not exposure triggers. Each acquired point autosaves and carries the scan UUID; late points serialize rather than overlap. Temperature targets are observed, not commanded. Center restoration and laser-off-at-end options retain their existing semantics.

AcquisitionPanel calls one shared state function for smoothing/background availability and sequence ownership. SpectrumPanel has explicit z values of 0 for background, 10 for unsmoothed and 20 for final data. PaddedViewBox provides independent per-axis auto margins for spectrum, monitor and power plots. PlotStyleSettings remains persisted through the existing dataclass preferences mechanism. Manual spectrum bounds remain exact.

The laser table computes the sum of row/header/scrollbar heights and caps it to 34% of available screen height. This removes unused space for fewer lasers while allowing all eight emulator rows when the screen permits. Window/screen sizing is still a real-Qt acceptance item.

## Range independent SNR

Noise is computed on unsmoothed data after any acquisition background subtraction. A vectorized local linear fit removes the slope in each complete 8- or 16-pixel block. The standard-deviation option corrects for the two fitted parameters and Gaussian standard-deviation bias. The MAD alternative uses approximate scaling and shows greater small-block bias in the supplied benchmark.

Block estimates use a symmetric 10% trimmed mean to reduce domination by narrow features. A trailing incomplete block is excluded from noise estimation. At least four complete blocks are required. The signal estimate is the maximum of a fixed nine-pixel symmetric smoother minus its 10th percentile; integrated automatic SNR covers the whole spectrum. Zero unresolved noise returns an invalid result rather than infinite SNR. The method description survives CSV save/load.

This is a spatial, single-frame estimate. Sloping backgrounds can bias the signal estimate; dense narrow features, clipping, correlated noise and wavelength-dependent noise can bias the noise estimate. Manual signal/noise windows remain available for a defined band. Repeat-acquisition uncertainty and fitted baselines are separate future analyses.

## Analysis data model and execution

SpectralTrace stores copied, read-only wavelength/intensity arrays, units, a unique trace ID, source IDs and ordered operation metadata. A derived trace does not overwrite its acquisition record. Analysis starts from the post-background, pre-smoothing array where available. Legacy smoothed and background-subtracted files without that intermediate stage are rejected instead of pretending the displayed data are raw.

AnalysisPanel provides GUI selection and plotting. Numerical operations live in processing/spectral_analysis.py; response/analysis persistence lives in io_utils/analysis_io.py. A QRunnable performs requested calculations or file operations, returning to GUI slots with explicitly queued signals. There are no instrument or laser commands in analysis. The workspace limits retention to 100 spectra and 2 million samples; stitching/profile grids are bounded to 250000 points.

The main Spectrum and Monitor views and acquisition autosave are not response-corrected automatically. Add current spectra or load saved files into Analysis, apply operations, and export derived results explicitly. This boundary makes later fitting/ARPLS stages extensible without changing hardware ownership or raw measurement records.

## Baselines and saved references

Constant offset subtraction uses input intensity units. Linear and quadratic fits require explicit baseline-only ranges such as 400:450, 750:800; the fitted model is subtracted across the spectrum. No automatic range selection or ARPLS is claimed. Input already baseline corrected is an explicit assertion recorded in provenance, intended for imported data whose prior correction is known.

Saved background CSVs load as counts/s references, retaining timestamp and configuration signature. In Analysis, select the sample traces, choose the loaded baseline as Reference spectrum, select Subtract reference, and convert sample counts to counts/s if necessary. Interpolation is limited to coverage. This explicit post-acquisition operation does not validate live detector compatibility or install the file as the acquisition background. The existing live capture/subtraction path retains its strict configuration signature rule.

## Response profiles and lamp acquisition

The supplied grating1_detector1_coefficients.csv is bundled unchanged and listed as an available profile, initially disabled. Its lab detector identifier 1 is not assumed to be camera SDK index 1 or a particular serial number. For other imported two-column files, enter the grating and laboratory detector identifiers before loading. Profile files created by this application preserve these identifiers internally.

To correct a sample, add or load its spectrum, subtract its baseline or use known prior background subtraction, choose the appropriate response profile, and select Process selected. Coefficients are interpolated linearly at the experimental wavelengths and multiplied into the baseline-corrected signal. Correction is applied once. Known acquisition grating/serial/gain/binning conflicts are rejected. Imported legacy profiles cannot supply missing serial or gain provenance, so the selected laboratory detector ID must be correct.

By default, missing profile coverage stops processing. Mask wavelengths outside response coverage explicitly produces NaN values and visible gaps. The code does not extrapolate coefficients. Output units are relative corrected counts or relative corrected counts/s; normalized coefficients do not establish absolute irradiance or cross-profile intensity equivalence.

To acquire a new profile, measure the standard lamp using normal acquisition or a Kymera center scan. Keep the lamp and optical setup stable; acquire suitable backgrounds for each configuration. Add/load the lamp segments, baseline-correct them, then select only those corrected lamp traces and choose Derive from selected lamp spectra. Counts are normalized by their recorded exposure, since averaged frames are not summed.

The lamp equation is exp(41.856 - 4808.1/x - 5 ln(x)), with x in nm. Each center produces lamp(x)/measured_rate(x). The code computes this in log space, combines coefficients across centers with edge-weighted overlap blending, resamples at the chosen spacing, and divides by one global maximum. Known saturation, nonpositive lamp signal, mixed detector identities and incompatible settings are rejected. Coverage gaps remain documented and masked on application. Save profile exports the full provenance with the coefficient table.

## Comparisons and residuals

Select multiple traces to overlay them. Choose a reference and Show differences from reference for wavelength-aligned subtraction within shared finite coverage. Normalizing overlays to their maximum changes only the display; residuals continue to use the original units. Mixed-unit residuals are rejected. No fitting model, fitted residuals or uncertainty propagation is implied.

## Spectral stitching

For an uncalibrated stitch, select two or more spectra with matching units and compatible known detector/filter settings. Use counts/s when exposures differ. Stitch selected merges the union of sample grids, blends overlaps with distance-to-segment-edge weights, and inserts explicit gaps where coverage is absent. Optional overlap matching uses a median positive overlap ratio and requires at least three shared positive samples; its scale factors are saved.

For calibrated stitching, first baseline- and response-correct each segment with the same profile. Enable Calibrated and stitch. Scale matching is disabled, and different profile fingerprints are rejected because independently normalized calibrations do not share an absolute scale. Segment identity, settings, processing history, coverage and blend policy are retained. This prototype does not estimate stitching uncertainty, correct drift, detect cosmic rays or solve independent grating normalizations.

## Peak and band analysis

Choose a band and minimum peak prominence, then Analyze selected. The report contains local peak positions/heights/prominences, widths at half prominence, signed trapezoidal band area, maximum and positive-signal centroid. Interpolated band endpoints are included. A band crossing a gap or outside coverage is rejected. Width at half prominence is not a fitted FWHM. Export analysis report writes JSON with units, band bounds, source identity and processing history.

## File formats and extension points

Acquisition spectrum CSV remains v5, with an optional SNR description. Derived spectra use file_type analysis_spectrum and schema_version 1, including trace ID, label, units, analysis_json and wavelength/intensity columns. Response files use file_type response_profile and schema_version 1, with grating, detector, profile_json and wavelength/coefficient columns. Both use atomic writes. The ordinary spectrum loader directs these file types to Analysis instead of interpreting corrected units as detector counts.

Future baseline algorithms can return a new trace and baseline array; future fitters can consume traces and return modeled/residual traces and reports. Acquisition workflows and device adapters remain separate. Drift-aware ordering, recipes, a background bank, ARPLS and fitting are not implemented in this release.
