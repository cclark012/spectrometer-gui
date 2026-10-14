# October 7 outstanding tasks and known issues

This update implements the requested fixes and analysis prototypes. Acceptance on the lab Python 3.14/PySide6 installation and the reported Andor connection failure remain open. Historical hardware issues in the September 14/October 6 records are not implicitly closed by this release.

## Immediate verification

1. Run the full target-environment pytest suite and the included real Qt emulator smoke test. Check MainWindow startup, settings dialogs, analysis background jobs and shutdown. This environment could not install or run PySide6 or pyqtgraph.
2. Recheck the real Andor connection using the same baseline DLL and configuration. The connection implementation matches September 14; no source change explains the reported regression. If it persists, provide the exact error/log and selected configuration/backend.
3. Confirm all eight emulator lasers fit at the lab resolution, and that smaller screens scroll sensibly. Check scan selection and Abort access while a run is active.
4. Verify smoothed data are visually above unsmoothed/background curves. Persist and reload custom colors, alpha, widths, grid/legend and independent plot margins.
5. Compare automatic SNR against representative PL spectra and the manual-window estimator, including weak peaks, clipped backgrounds, sloping baselines and short exposures. Benchmark again on the lab machine if high-rate throughput is important.
6. Confirm the supplied coefficient file uses wavelength in nm and that laboratory detector identifier 1 maps to the intended camera/output configuration. Its filename does not establish the SDK camera index or serial. Confirm the same nm convention for the lamp equation before generating lab calibration profiles.
7. Validate one corrected lamp/sample dataset and overlapping center scan against the existing laboratory correction calculations. Check intensity units, baseline selection, interpolation edges and overlap seams. Relative correction is not absolute irradiance calibration.
8. Continue existing monitor-channel/timer, timing-label, toolbar/autosave, long-exposure scan abort/close, cooler-shutdown and device reconnect acceptance checks. Lake Shore communication and front-panel agreement remain unverified.

## Prototype boundaries

Analysis is post-acquisition. Adding/loading spectra and exporting derived spectra are explicit actions; acquisition autosave and monitor values remain on the acquisition processing path. Analysis retains up to 100 spectra and 2 million points. Stitching and response grids have 250000-point limits.

Saved background loading is available as a counts/s analysis reference. It does not install a live acquisition background or provide a searchable reference bank. Live backgrounds retain the existing strict signature checks. Polynomial baseline fits use user-selected baseline-only ranges; an incorrect range can subtract real signal.

Response profiles require laboratory grating/detector identifiers. Derived profiles retain known serial/gain/binning constraints; imported two-column coefficients cannot recover absent hardware metadata. The code rejects known conflicts but cannot verify a manual detector label. Saturation rejection relies on recorded detector full-scale evidence.

Calibrated stitching requires the same profile fingerprint and compatible known setup. Independent profile normalizations, intensity drift, detector nonlinearity, cosmic-ray rejection, spectral-resolution matching and uncertainty propagation are not solved. Uncalibrated overlap scale matching can be unreliable on noisy or changing samples; saved scale factors make the operation reviewable.

Automatic SNR is a single-frame spatial estimate. It excludes a final partial block and needs four complete blocks. Dense spectral structure, clipping or correlated/heteroscedastic noise can bias it; a sloping offset can bias the automatic signal baseline. Use manual windows where a defined band/noise reference is needed.

Peak widths are widths at half prominence, not fitted FWHM. There is no fitting or ARPLS stage. Residuals are direct differences on overlapping wavelength coverage; display normalization does not change their units.

## Revised future priorities

| Feature | Direction |
|---|---|
| Drift-aware/randomized ordering and reference zeroing | Next-round candidate; no implementation now |
| ARPLS and related baselines; fitting | Future modular analysis stages; await supplied implementations/design |
| Experimental recipes and user procedures | Later; no recipe/procedure runtime added |
| Background bank with dedicated reference tracking | Later; current scope is analysis loading and subtraction |
| Long-run temperature logging/mapping | After Lake Shore communication is verified |
| Absorbance/transmission | Lowest priority; current lab is PL and lacks the required measurement setup |
| Gated low-exposure timing development | Deferred; keep September 14 implementation pending suitable lab setup |

## Questions for the next lab check

If Andor still fails after this build starts correctly, what is the exact connection error and which configuration/DLL selection was used? Which physical detector/output path corresponds to detector 1 in the supplied coefficient filename? Are wavelengths in the coefficient file and lamp equation both in nm, as used in this implementation?
