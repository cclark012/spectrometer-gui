# Andor iDus timing analysis

Updated: 2026-09-14  
Camera: DU401_BVF, serial 26970  
Runtime: `atmcd64d_legacy.dll` on Windows 10 / Python 3.14.3

## Conclusion

The approximately 10 Hz GUI limit was caused by the SDK2 transaction mode, not
NumPy transfer, PySide6, or Python execution. Re-armed single scans spend tens of
milliseconds in `StartAcquisition` and roughly 78 to 153 ms waiting inside the
driver. An armed Run-Till-Abort stream delivers 15 ms frames at about 60 Hz and
50 ms frames at about 19.95 Hz. Array transfer is normally below 0.11 ms.

The implementation response is to keep SDK2 mode 5 armed for Live, automated
rapid, and gated acquisitions, then retrieve the oldest unread fresh frame. A
C/C++ rewrite or standalone executable would not remove the measured native
re-arm wait.

The 2026-09-14 follow-up confirms the selector correction. The unaligned gap
probe returned 100/100 frames but dated all 99 steady-state exposure midpoints
before their request target. With midpoint alignment enabled, it returned
100/100 frames, all steady-state midpoint offsets were positive, and the adapter
discarded two early candidates per post-warm-up request. The higher wall time is
the expected cost of waiting for an eligible exposure, not a throughput
regression in the armed stream.

## Meaning of the saved timing fields

All saved gated delays use the same process-local monotonic clock and are relative
to the software transition origin. That origin is recorded after the OBIS OFF
command has been acknowledged and its state query has succeeded; it is not an
optical edge measured by the laser box.

| Family | Exact meaning |
|---|---|
| Request | Time when the gated coordinator emits the spectrum request toward the runtime/worker queue. |
| Acquisition call | Worker timestamps immediately before and after the selected adapter's `acquire_spectrum` call; midpoint is the arithmetic midpoint of those bounds. It includes native waiting/transfer and adapter averaging, but not later GUI plotting or file writing. |
| Exposure | Backend-derived estimate of the detector integration window. For the Andor stream it is reconstructed from frame completion plus SDK-applied exposure/readout/cycle timing; for backends without detector timing it can fall back to call bounds. |
| Timing error | Signed residual `selected exposure reference - requested delay`. Positive is late and negative is early relative to the selected start/midpoint reference. It is not the uncertainty. |
| Exposure uncertainty | Conservative half-width/bound associated with the exposure estimate. It is not a standard deviation and does not quantify the unknown laser-command-to-optical-transition delay. |

A negative request or acquisition-call value would indicate work that began before
the software transition origin and deserves investigation. A negative exposure
start alone is valid at a zero-delay midpoint target when the finite exposure
straddles that origin. The 2026-09-14 supplied GUI files follow that pattern: all
negative minima/means are confined to the zero-delay exposure-start statistic;
accepted exposure midpoints, calls, requests, and timing-error means are not
negative.

## 2026-09-14 controls and stream evidence

The reversible controls archive verifies apply-and-restore for grating 1→2,
center wavelength 650→500 nm, Kymera filter 2→5, output flipper 0→1,
horizontal-speed index 0→2, vertical-speed index 1→2, and preamp-gain index 0→1.
The unavailable gain index 2 was rejected before a native write, as intended.

Two operations must not be advertised as verified controls on this configuration:
horizontal binning 4 returned SDK2 `DRV_NOT_SUPPORTED`, and focus 423→400 returned
`ATSPECTROGRAPH_P2INVALID`. Production capabilities now expose FVB binning 1 only
and show focus as read-only. The probe retains both native test paths for focused
future diagnosis.

| Gap probe | Completed | Pre-request discards | Target discards | Stale notifications | Steady-state call median | Exposure midpoint relative to target |
|---|---:|---:|---:|---:|---:|---:|
| Unaligned, 15 ms / 100 ms gap | 100/100 | 595 | 0 | 99 | 4.367 ms | median −16.562 ms; all 99 early |
| Midpoint-aligned, 15 ms / 100 ms gap | 100/100 | 594 | 198 | 99 | 34.353 ms | median +13.425 ms; all 99 eligible |

The first request in both files takes approximately 1.05–1.10 s because it arms
and prepares the stream. That one-time cost remains outside a GUI time-sensitive
sequence through the unsaved preparation frame.

Across the supplied schema-v4 GUI files, fixed-delay 100-frame traces produced
exposure-midpoint timing-error means around 6–7 ms. The 201-trace one-sample
interleaved scan covered roughly 0.14–31.08 ms error means and robustly rejected
two traces; the 85-trace ten-sample scan covered roughly 3.77–11.39 ms and
rejected one. These values characterize software timing relative to the serial
transition origin, not optical truth.

## Fixed-window retention mode

For averaged delayed-after-OFF and interleaved-decay measurements, the new
**Retain N inside fixed window** policy treats **Repeats** as the number of valid
spectra required for each delay trace. The user selects exposure start or midpoint,
a symmetric tolerance, and an attempt multiplier. Rejected frames are released,
accepted frames update incremental mean/variance, and full pump/OFF cycles continue
until every trace reaches N or the finite ceiling is exhausted. A partial series
is saved with the failure reason if the ceiling is reached.

The mode intentionally disables per-frame Newport reads and individual-frame
output. It also rejects duplicate fixed-delay trace identities. If a requested
±2 ms window is narrower than the approximately 15 ms iDus cadence, the preview
warns that some pump cycles may have no eligible frame. This policy guarantees
only compliance with the recorded software estimate; an external optical timing
reference is still required for a physical ±2 ms claim.

## Low-light phosphorescence implications

The Rhodamine B background-subtracted traces in the supplied archive remain near
zero on the tested geometry, so timing rejection alone is unlikely to recover a
signal. The highest-value path is to increase collected photons first: optimize
sample/slit imaging and numerical aperture, widen the entrance slit as resolution
allows, choose the appropriate grating/blaze and port, remove unnecessary lossy
optics, cool and stabilize the camera, and compare readout speed/gain choices on
blocked-light and weak-source data. Longer pump and exposure times plus repeated
accepted averages trade time for sensitivity.

A CCD-compatible “software lock-in” should be implemented as phase-cycled boxcar
detection rather than pretending the camera is an analog lock-in: alternate
matched pump-on/pump-off or early/late gated frames, subtract each paired phase,
and accumulate mean plus variance. This rejects static background and slow drift
but cannot create photons, and the reference frame adds noise. A later matched-
filter or wavelength-band estimator can improve detection when a trustworthy
spectral template exists, while raw traces and uncertainties remain saved.

## 2026-09-11 GUI follow-up

The first lab GUI run exposed a synchronization condition absent from the tight
timing-probe loop. `GetOldestImage` returned `DRV_NO_NEW_DATA` immediately after
`WaitForAcquisitionTimeOut` returned success. The SDK2 acquisition-event queue
and circular image buffer are separate. The observed sequence is consistent
with a pending notification surviving the GUI freshness drain; a short driver
race between event delivery and buffer availability would have the same visible
result and is handled by the same correction.

The adapter now treats `GetNumberNewImages`, not a wait return by itself, as the
readiness authority. It checks before waiting, ignores successful stale wakes,
and continues within one bounded timeout until an unread range exists. A focused
regression simulates the drain/stale-event/new-frame sequence. The new
`andor_stream_gap_probe` deliberately inserts a pause between adapter calls so
the production camera can verify this exact condition without GUI scheduling.

The follow-up gap probe passed 100 of 100 requests at 15 ms exposure with a
100 ms pause. Driver indices increased monotonically from 1 to 694, with an
exact increment of seven per request after the first. It discarded 594 frames
that were already complete at request entry and recovered 99 stale wait
notifications. GUI Live and multi-frame gated acquisition also completed. This
closes the original `DRV_NO_NEW_DATA` reproduction, while preserving the stale-
notification handling as a required stream invariant.

The three gated CSV files expose a separate selection error. The adapter chose
the first frame whose readout completed after the request, but that frame's
exposure could have occurred before the requested midpoint. The offset repeats
at every delay, so it is not caused by resetting a timer during save or result
processing.

| File or trace | Requested midpoint | Exposure start mean | Exposure midpoint mean | Exposure end mean | Midpoint error |
|---|---:|---:|---:|---:|---:|
| `0001`, delay 0 ms | 0 ms | -18.427 ms | -10.922 ms | -3.417 ms | -10.922 ms |
| `0002`, delay 0 ms | 0 ms | -18.988 ms | -11.483 ms | -3.978 ms | -11.483 ms |
| `0003`, delay 0 ms | 0 ms | -18.925 ms | -11.420 ms | -3.915 ms | -11.420 ms |
| `0003`, delay 150 ms | 150 ms | 133.083 ms | 140.588 ms | 148.093 ms | -9.412 ms |
| `0003`, delay 300 ms | 300 ms | 281.700 ms | 289.205 ms | 296.710 ms | -10.795 ms |
| `0003`, delay 450 ms | 450 ms | 432.732 ms | 440.237 ms | 447.742 ms | -9.763 ms |
| `0003`, delay 600 ms | 600 ms | 581.696 ms | 589.201 ms | 596.706 ms | -10.799 ms |

The corrected stream selector consumes an early candidate and continues under
the same bounded deadline until the first frame with an estimated exposure
midpoint at or after the requested target is available. When several images are
pending, it dates the oldest unread frame by its distance from the newest image
and the SDK kinetic-cycle readback. The number rejected specifically for target
alignment is retained separately from the backlog discarded at request entry.

The reference point remains the exposure midpoint, as selected in the answered
design questions. At a zero-millisecond target, an accepted 15 ms exposure can
still have a negative start time if it straddles laser OFF. That value is valid
and must not be clamped. Requiring the entire exposure to start after laser OFF
would be a different policy and would add approximately half an exposure to the
effective delay.

## Camera and readout facts

| Item | Reported value | Interpretation |
|---|---|---|
| Detector | 1024 × 127, 26 µm square pixels | Valid DU401_BVF geometry used for Kymera calibration. |
| Horizontal speeds | 0.1, 0.05, 0.033 MHz | 100, 50, and 33 kHz. SDK2 already reports MHz. |
| Vertical speeds | 8.25, 16.25, 32.25, 64.25 µs/pixel | 16.25 is the initial quality default; all remain selectable. |
| Single-scan applied minimum | 12.23 ms at 8.25 µs; 13.24 ms at 16.25 µs | Applied result of the probe request in single-scan mode, not proof of mode-5 minimum. |
| Initial full-frame period | 24.37 ms at 8.25 µs; 26.40 ms at 16.25 µs | Throughput quantity separate from exposure duration. |

## Measured throughput

| Requested exposure and V shift | Re-armed wall median | Re-armed p95 | Stream completion period mean | Stream SD | Stream rate |
|---|---:|---:|---:|---:|---:|
| 15 ms, 8.25 µs | 117.444 ms | 140.330 ms | 15.00349 ms | 0.13967 ms | 60.188 Hz |
| 15 ms, 16.25 µs | 124.510 ms | 141.845 ms | 15.01021 ms | 0.15834 ms | 59.471 Hz |
| 50 ms, 8.25 µs | 156.153 ms | 171.102 ms | 50.00343 ms | 0.07341 ms | 19.964 Hz |
| 50 ms, 16.25 µs | 142.132 ms | 173.067 ms | 49.99461 ms | 0.08139 ms | 19.962 Hz |
| 50 ms, 32.25 µs | 171.982 ms | 185.958 ms | 49.99852 ms | 0.17282 ms | 19.954 Hz |
| 50 ms, 64.25 µs | 200.376 ms | 208.561 ms | 49.99485 ms | 0.17667 ms | 19.951 Hz |

The long test includes 1,000 frames at each vertical speed. Its approximately
50 ms cadence and sub-0.18 ms period standard deviations provide much stronger
evidence than a short UI rate observation.

## Stage diagnosis

For re-armed 15 ms scans, median `StartAcquisition` costs about 31.9 to 47.2 ms
and median wait costs about 77.2 to 78.1 ms. At 50 ms, median start costs about
31.8 to 47.4 ms and median wait costs about 109.8 to 152.6 ms. Reusing prepared
state eliminates repeated `PrepareAcquisition`, but it does not eliminate those
per-frame re-arm costs.

Run-Till-Abort pays about 0.88 to 0.90 seconds to prepare once. Its first-frame
latency is approximately 96 to 178 ms, depending on exposure and vertical speed,
but subsequent completion periods match the requested exposure. This is why a
single unsaved preparation frame belongs before a gated transition, not inside
the time-critical sequence.

## Gated semantics implemented

At each requested gated deadline, the SDK2 adapter:

1. identifies and removes frames that were already complete when the request
   arrived;
2. waits until `GetNumberNewImages` confirms unread data, ignoring stale event
   notifications;
3. reads the oldest unread frame and estimates its exposure window from applied
   exposure, readout, and kinetic-cycle timing;
4. discards the candidate if its exposure midpoint is earlier than the absolute
   gated target, then repeats under the same bounded deadline;
5. returns the first midpoint-eligible frame and records its driver index;
6. records pre-request backlog, target-alignment discards, maximum pending depth,
   exposure/readout estimates, and conservative bounds;
7. stops with an explicit error if SDK indices are invalid, required timing is
   unavailable, or the circular buffer appears to have overwritten a frame.

This implements the requested exposure-midpoint policy without re-arming SDK2.
It does not claim that the exposure begins at the requested delay. The saved
exposure window and uncertainty remain the analysis evidence; a future hardware
trigger or camera FIRE signal is required for a stronger timing guarantee.

## Exposure request policy

The single-scan minimum probe and mode-5 exposure are intentionally separate.
The GUI accepts any positive whole-millisecond request. After configuring the
actual acquisition mode, SDK2 readback determines the applied exposure. The CSV
uses the applied value as `acquisition.integration_ms` and writes
`integration_requested_ms` only when it differs.

No supplied run tests 1, 5, or 10 ms in mode 5, so sub-12 ms temporal resolution
is not yet established. It is available in the interface without being claimed
as achieved.

## Next bench measurements

1. Run mode 5 at requested 1, 5, and 10 ms for both 8.25 and 16.25 µs vertical
   shift. Record SDK-applied exposure, kinetic cycle, completion period, and any
   return code.
2. Resolve focus semantics/range with a narrowly bounded probe around the current
   423 readback before re-enabling its GUI editor. Keep production FVB binning at
   1 unless a different acquisition/read mode demonstrably supports another value.
3. Calibrate the software laser-OFF reference against a photodiode recorded by an
   oscilloscope or DAQ. Measure command-to-optical delay and jitter for each box/
   channel; do not interpret the fixed-window tolerance as optical timing before
   this test.
4. Bench-test **Retain N inside fixed window** first at 10 ms, exposure midpoint,
   20 accepted spectra, ±8 ms, and a 3× ceiling. Inspect retained/rejected counts,
   then narrow the tolerance only if the measured acceptance rate supports it.
5. Change exposure and each readout setting during separate GUI Live runs. Verify
   the old stream aborts and exactly one new stream prepares before data resume.
6. Add controlled CPU/UI load and determine whether multiple frames become pending.
   Oldest-frame dating, target selection, and overwrite guards must remain correct.
7. Compare a camera FIRE/trigger edge against recorded software timing before
   choosing production timing-guard thresholds.

## Source evidence

- `andor_timing.json`, recorded 2026-09-10T15:46:19.848001+00:00
- `andor_timing_long.json`, recorded 2026-09-10T16:05:26.863620+00:00
- `log_20260911.txt`, complete 287-line GUI session log; the repeated failure is
  `WaitForAcquisitionTimeOut` success followed by `GetOldestImage` return 20024.
- `andor_stream_gap.json`, recorded 2026-09-11T17:03:10.411720+00:00; 100/100
  completed frames, indices 1 through 694, 594 pre-request discards, and 99
  recovered stale notifications.
- `Kymera_gated_delayed_after_off_20260911_0001.csv`, 100 frames at delay 0 ms.
- `Kymera_gated_delayed_after_off_20260911_0002.csv`, a second 100-frame delay
  0 ms run.
- `Kymera_gated_delayed_after_off_20260911_0003.csv`, 100 frames at each of 0,
  150, 300, 450, and 600 ms.
- `andor_probe_results_20260914.zip`: 13 complete JSON probe records and eight
  complete gated CSVs. It includes the read-only and reversible control matrix,
  unaligned/aligned stream-gap runs, fixed/transition/interleaved blank-light
  runs, and Rhodamine B runs. Every JSON object and every CSV metadata/data row
  was parsed before this revision.
- [Andor SDK2 programming guide](https://andor.oxinst.com/downloads/uploads/Andor_Software_Development_Kit_2.pdf):
  Run Till Abort acquisition and circular-buffer retrieval functions
  `GetNumberNewImages` and `GetOldestImage`.
