"""Independent, immutable spectral analysis operations.

Acquisition records are never overwritten. Every derived trace carries the
source identifiers and ordered operations required to interpret its values.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
import json
import math
import uuid
import numpy as np


def canonical_xy(x, y):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 1 or x.shape != y.shape or x.size < 2:
        raise ValueError('Spectra require matching one-dimensional arrays with at least two points')
    if not np.all(np.isfinite(x)) or np.any(np.isinf(y)) or np.count_nonzero(np.isfinite(y)) < 2:
        raise ValueError('Wavelengths must be finite and at least two intensities must be finite')
    order = np.argsort(x, kind='stable'); x, y = x[order], y[order]
    if np.any(np.diff(x) <= 0):
        raise ValueError('Duplicate wavelengths are ambiguous; resample or combine them explicitly')
    return x.copy(), y.copy()


@dataclass(frozen=True)
class SpectralTrace:
    x: np.ndarray
    y: np.ndarray
    label: str = 'Spectrum'
    units: str = 'counts'
    metadata: dict = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self):
        x,y = canonical_xy(self.x,self.y)
        x.setflags(write=False); y.setflags(write=False)
        object.__setattr__(self,'x',x); object.__setattr__(self,'y',y)
        object.__setattr__(self,'metadata',deepcopy(self.metadata))

    def derive(self, y, operation, *, x=None, units=None, label=None, metadata=None):
        meta = deepcopy(self.metadata)
        meta.setdefault('source_ids', []).append(self.trace_id)
        meta.setdefault('operations', []).append(deepcopy(operation))
        if metadata: meta.update(deepcopy(metadata))
        return SpectralTrace(self.x if x is None else x, y, label or self.label,
                             self.units if units is None else units, meta)


def trace_from_record(record, label='Acquired spectrum'):
    y = record.unsmoothed_intensities_counts
    if y is None:
        if record.smoothing_window != 'none' and record.boxcar_width > 1:
            if record.background_subtracted or record.raw_intensities_counts is None:
                raise ValueError('This legacy smoothed record has no unsmoothed baseline-corrected data')
            y = record.raw_intensities_counts
        else:
            y = record.intensities_counts
    state = deepcopy(record.instrument_state)
    spectrograph = state.get('spectrograph', {})
    return SpectralTrace(record.wavelengths_nm, y, label, 'counts', {
        'timestamp_utc': record.timestamp_utc, 'integration_ms': record.integration_ms,
        'instrument_state': state, 'detector_serial': record.spectrometer_serial,
        'grating': str(state.get('grating', spectrograph.get('grating', '')) or ''),
        'background_subtracted': record.background_subtracted,
        'background_signature': record.background_signature,
        'baseline_applied': bool(record.background_subtracted),
        'filter_state': record.filter_state, 'parameter_scan': deepcopy(record.parameter_scan),
        'correct_dark':record.correct_dark, 'correct_nonlinearity':record.correct_nonlinearity,
        'saturation_observed': bool(math.isfinite(record.spectrometer_max_intensity)
            and math.isfinite(record.signal_max_counts)
            and record.signal_max_counts >= 0.98 * record.spectrometer_max_intensity),
        'operations': []})


def intervals_mask(x, intervals):
    mask = np.zeros(x.shape, dtype=bool)
    for lo,hi in intervals:
        if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
            raise ValueError('Each baseline interval must have finite, increasing bounds')
        mask |= (x >= lo) & (x <= hi)
    return mask


def subtract_baseline(trace, method='constant', *, offset=0., intervals=()):
    if trace.metadata.get('response_profile'):
        raise ValueError('Baseline subtraction must precede response correction; select the original trace')
    if method == 'constant':
        if not math.isfinite(offset): raise ValueError('Offset must be finite')
        baseline = np.full_like(trace.y, offset)
        operation = {'type':'baseline','method':method,'offset':float(offset),'units':trace.units}
    elif method in ('linear','quadratic'):
        degree = 1 if method == 'linear' else 2
        intervals = tuple(intervals)
        mask = intervals_mask(trace.x, intervals) & np.isfinite(trace.y)
        if np.count_nonzero(mask) < degree+1:
            raise ValueError('Select baseline-only intervals containing enough distinct wavelengths')
        center = float(np.mean(trace.x[mask])); scale = float(np.ptp(trace.x[mask]))
        z = (trace.x-center)/scale
        coefficients = np.polynomial.polynomial.polyfit(z[mask], trace.y[mask], degree)
        baseline = np.polynomial.polynomial.polyval(z,coefficients)
        operation = {'type':'baseline','method':method,'intervals_nm':intervals,
                     'center_nm':center,'scale_nm':scale,'coefficients':coefficients.tolist()}
    else:
        raise ValueError('Unknown baseline method')
    return trace.derive(trace.y-baseline,operation,label=trace.label+' | baseline',metadata={'baseline_applied':True}), baseline


def finite_runs(trace):
    good = np.isfinite(trace.y)
    edges = np.diff(np.r_[False,good,False].astype(int))
    return list(zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)))


def interpolate(trace, grid):
    """Interpolation within finite contiguous coverage only, with NaN elsewhere."""
    grid=np.asarray(grid,dtype=float); out=np.full(grid.shape,np.nan)
    for start,stop in finite_runs(trace):
        if stop-start<2: continue
        mask=(grid>=trace.x[start]) & (grid<=trace.x[stop-1])
        out[mask]=np.interp(grid[mask],trace.x[start:stop],trace.y[start:stop])
    return out


def subtract_reference(trace, reference):
    if trace.metadata.get('response_profile'):
        raise ValueError('Subtract a baseline before response correction')
    if trace.units != reference.units: raise ValueError('Baseline and spectrum units must match')
    baseline=interpolate(reference,trace.x)
    if np.any(np.isfinite(trace.y) & ~np.isfinite(baseline)):
        raise ValueError('Baseline does not cover the spectrum; extrapolation is disabled')
    return trace.derive(trace.y-baseline,{'type':'baseline_reference','reference_id':reference.trace_id,
        'reference_label':reference.label,'reference_metadata':reference.metadata},
        label=trace.label+' | reference subtracted',metadata={'baseline_applied':True}),baseline


def as_count_rate(trace):
    if trace.units.endswith('/s'): return trace
    if trace.units not in ('counts','relative corrected counts'):
        raise ValueError('Only counts can be divided by exposure')
    ms=float(trace.metadata.get('integration_ms',float('nan')))
    if not math.isfinite(ms) or ms<=0: raise ValueError('A valid exposure is required for counts/s')
    return trace.derive(trace.y/(ms/1000),{'type':'exposure_normalization','integration_ms':ms},units=trace.units+'/s')


@dataclass(frozen=True)
class ResponseProfile:
    x: np.ndarray
    coefficients: np.ndarray
    grating: str
    detector: str
    label: str = 'Response'
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        x,y=canonical_xy(self.x,self.coefficients)
        if np.any(~np.isfinite(y)) or np.any(y<=0): raise ValueError('Response coefficients must be finite and positive')
        if not str(self.grating).strip() or not str(self.detector).strip():
            raise ValueError('A response profile requires grating and detector identifiers')
        x.setflags(write=False);y.setflags(write=False)
        object.__setattr__(self,'x',x);object.__setattr__(self,'coefficients',y)
        object.__setattr__(self,'metadata',deepcopy(self.metadata))

    @property
    def fingerprint(self):
        h=hashlib.sha256()
        h.update(self.x.astype('<f8').tobytes());h.update(self.coefficients.astype('<f8').tobytes())
        h.update(json.dumps({'grating':self.grating,'detector':self.detector,'metadata':self.metadata},sort_keys=True).encode())
        return h.hexdigest()


def apply_response(trace, profile, *, grating, detector, crop=False):
    if trace.metadata.get('response_profile'): raise ValueError('Response correction is already applied')
    if not trace.metadata.get('baseline_applied'):
        raise ValueError('Subtract a baseline or explicitly mark the input as baseline corrected first')
    if str(grating)!=profile.grating or str(detector)!=profile.detector:
        raise ValueError('Selected grating/detector does not match the response profile')
    recorded_grating=str(trace.metadata.get('grating',''))
    if recorded_grating and recorded_grating != str(grating):
        raise ValueError('Acquisition grating metadata conflicts with the selected profile')
    serial=profile.metadata.get('detector_serial')
    if serial and str(trace.metadata.get('detector_serial','')) != str(serial):
        raise ValueError('Detector serial does not match the response profile')
    for key,expected in profile.metadata.get('acquisition_constraints', {}).items():
        actual=trace.metadata.get('instrument_state', {}).get(key)
        if actual != expected:
            raise ValueError('Response profile requires detector setting ' + key + '=' + str(expected))
    valid=(trace.x>=profile.x[0]) & (trace.x<=profile.x[-1])
    coverage=profile.metadata.get('coverage_nm', [[float(profile.x[0]),float(profile.x[-1])]])
    covered=np.zeros_like(valid)
    for lo,hi in coverage: covered |= (trace.x>=lo) & (trace.x<=hi)
    valid &= covered
    if not crop and np.any(np.isfinite(trace.y) & ~valid):
        raise ValueError('Profile does not cover this spectrum; enable crop or use another profile')
    y=trace.y*np.interp(trace.x,profile.x,profile.coefficients)
    y[~valid]=np.nan
    return trace.derive(y,{'type':'response','profile_sha256':profile.fingerprint,'profile_label':profile.label,
                          'grating':grating,'detector':detector,'crop':crop},
        label=trace.label+' | response', units='relative corrected '+trace.units,
        metadata={'response_profile':profile.fingerprint,'response_label':profile.label,
                  'response_grating':grating,'response_detector':detector})


def _check_shared_setup(traces):
    # Center wavelength is intentionally variable; gains, detector and filters
    # cannot silently change across segments of a single relative spectrum.
    keys = ('detector_serial', 'grating', 'filter_state', 'correct_dark', 'correct_nonlinearity')
    state_keys = ('output_amplifier', 'preamp_gain', 'horizontal_binning')
    for key in keys:
        known = {json.dumps(t.metadata[key], sort_keys=True) for t in traces
                 if key in t.metadata and t.metadata[key] not in (None, '')}
        if len(known) > 1:
            raise ValueError('Spectra have conflicting ' + key + ' metadata')
    for key in state_keys:
        known = {json.dumps(t.metadata.get('instrument_state', {})[key], sort_keys=True)
                 for t in traces if t.metadata.get('instrument_state', {}).get(key) is not None}
        if len(known) > 1:
            raise ValueError('Spectra have conflicting detector ' + key + ' metadata')


def stitch(traces, *, calibrated=False, match_overlap=False):
    traces=list(traces)
    if len(traces)<2: raise ValueError('Select at least two spectra to stitch')
    if len({t.units for t in traces})!=1: raise ValueError('Stitching requires matching intensity units')
    _check_shared_setup(traces)
    profiles={t.metadata.get('response_profile','') for t in traces}
    if calibrated:
        if len(profiles)!=1 or not all(profiles):
            raise ValueError('Calibrated stitching requires the same response profile on every segment')
        if match_overlap: raise ValueError('Calibrated stitching preserves scale; overlap matching is disabled')
    elif any(profiles):
        raise ValueError('Use calibrated stitching for response-corrected segments')
    if not traces[0].units.endswith('/s'):
        exposures=[t.metadata.get('integration_ms') for t in traces]
        if any(v is None for v in exposures) or len(set(exposures))!=1:
            raise ValueError('Unequal or unknown exposures: normalize segments to counts/s first')
    if sum(len(t.x) for t in traces)>250000: raise ValueError('Stitching is limited to 250000 total input points')
    traces.sort(key=lambda t:t.x[0])
    intervals=sorted((float(t.x[a]),float(t.x[b-1])) for t in traces for a,b in finite_runs(t) if b-a>=2)
    merged=[]
    for lo,hi in intervals:
        if merged and lo<=merged[-1][1]: merged[-1][1]=max(merged[-1][1],hi)
        else: merged.append([lo,hi])
    gaps=[(a[1]+b[0])/2 for a,b in zip(merged,merged[1:])]
    grid=np.unique(np.r_[np.concatenate([t.x for t in traces]),gaps])
    total=np.zeros_like(grid);weights=np.zeros_like(grid);factors=[]
    for trace in traces:
        values=interpolate(trace,grid); valid=np.isfinite(values); factor=1.
        if match_overlap and np.any(weights>0):
            overlap=valid & (weights>0) & (values>0)
            previous=np.divide(total,weights,out=np.full_like(total,np.nan),where=weights>0)
            overlap &= previous>0
            if np.count_nonzero(overlap)<3: raise ValueError('Overlap matching needs at least three positive shared samples per segment')
            factor=float(np.median(previous[overlap]/values[overlap]))
        w=np.zeros_like(grid)
        for start,stop in finite_runs(trace):
            if stop-start<2:continue
            lo,hi=trace.x[start],trace.x[stop-1]
            mask=valid & (grid>=lo) & (grid<=hi)
            w[mask]=np.maximum(np.minimum(grid[mask]-lo,hi-grid[mask]),max((hi-lo)*1e-6,1e-12))
        total[valid]+=values[valid]*factor*w[valid];weights+=w;factors.append(factor)
    values=np.divide(total,weights,out=np.full_like(total,np.nan),where=weights>0)
    operation={'type':'stitch','calibrated':calibrated,'overlap_matching':match_overlap,'scale_factors':factors,
               'blend':'distance-to-segment-edge weighted mean','coverage_nm':merged,
               'segments':[dict(id=t.trace_id,label=t.label,metadata=t.metadata) for t in traces]}
    meta={'operations':[operation],'source_ids':[t.trace_id for t in traces],
          'baseline_applied':all(t.metadata.get('baseline_applied') for t in traces)}
    for key in ('integration_ms','detector_serial','grating','filter_state','instrument_state',
                'correct_dark','correct_nonlinearity','response_label','response_grating','response_detector'):
        metadata_values=[t.metadata.get(key) for t in traces]
        if metadata_values[0] is not None and all(value==metadata_values[0] for value in metadata_values):
            meta[key]=deepcopy(metadata_values[0])
    if calibrated:meta['response_profile']=next(iter(profiles))
    return SpectralTrace(grid,values,'Calibrated stitch' if calibrated else 'Uncalibrated stitch',traces[0].units,meta)


def derive_response(traces, *, grating, detector, step_nm=0.5, label='Lamp response'):
    traces=list(traces)
    if not traces or not math.isfinite(step_nm) or step_nm<=0: raise ValueError('Select lamp spectra and a positive resampling step')
    if any(t.metadata.get('response_profile') for t in traces): raise ValueError('Use uncorrected lamp data')
    if any(not t.metadata.get('baseline_applied') for t in traces): raise ValueError('Baseline-correct every lamp spectrum first')
    if any(t.metadata.get('saturation_observed') for t in traces): raise ValueError('Saturated lamp acquisitions cannot calibrate response')
    _check_shared_setup(traces)
    rates=[as_count_rate(t) for t in traces]
    for t in rates:
        if t.metadata.get('grating') and str(t.metadata['grating'])!=str(grating):
            raise ValueError('Lamp acquisition grating conflicts with selected grating')
    # Compute lamp/measured for each center before combining coefficients.
    # Use one global scale across all centers, never normalize centers separately.
    coefficients=[]; maxima=[]; coverage=[]
    for trace in rates:
        if trace.x[0]<=0:raise ValueError('Lamp equation uses positive wavelengths in nm')
        good=np.isfinite(trace.y)
        if np.any(trace.y[good]<=0):
            raise ValueError('Lamp counts after baseline subtraction must be positive throughout coverage')
        values=np.full(trace.y.shape,np.nan)
        values[good]=41.856-4808.1/trace.x[good]-5*np.log(trace.x[good])-np.log(trace.y[good])
        maxima.append(float(np.nanmax(values)));coefficients.append(values)
        coverage.extend([float(trace.x[a]),float(trace.x[b-1])] for a,b in finite_runs(trace) if b-a>=2)
    lo=min(t.x[0] for t in rates);hi=max(t.x[-1] for t in rates)
    if (hi-lo)/step_nm>250000:raise ValueError('Response grid is limited to 250000 points')
    grid=np.arange(lo,hi+step_nm*0.01,step_nm)
    total=np.zeros_like(grid);weights=np.zeros_like(grid)
    global_max=max(maxima)
    for trace,log_values in zip(rates,coefficients,strict=True):
        coeff_trace=SpectralTrace(trace.x,np.exp(log_values-global_max),'Center coefficients','relative coefficient')
        values=interpolate(coeff_trace,grid)
        for start,stop in finite_runs(coeff_trace):
            if stop-start<2:continue
            left,right=trace.x[start],trace.x[stop-1]
            mask=(grid>=left)&(grid<=right)&np.isfinite(values)
            weight=np.maximum(np.minimum(grid[mask]-left,right-grid[mask]),max((right-left)*1e-6,1e-12))
            total[mask]+=values[mask]*weight;weights[mask]+=weight
    good=weights>0
    if np.count_nonzero(good)<2:raise ValueError('Insufficient lamp coverage')
    coeff=total[good]/weights[good]
    coeff/=np.max(coeff)
    merged=[]
    for left,right in sorted(coverage):
        if merged and left<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],right)
        else:merged.append([left,right])
    coverage=merged
    serials={str(t.metadata.get('detector_serial','')) for t in traces}
    if len(serials)>1:raise ValueError('Lamp spectra have different detector serials')
    metadata={'equation':'exp(41.856 - 4808.1/x - 5*ln(x))','wavelength_units':'nm',
              'normalization':'one global maximum','resample_step_nm':step_nm,'coverage_nm':coverage,
              'detector_serial':next(iter(serials)),
              'acquisition_constraints':{key:traces[0].metadata.get('instrument_state', {})[key]
                  for key in ('output_amplifier','preamp_gain','horizontal_binning')
                  if traces[0].metadata.get('instrument_state', {}).get(key) is not None},
              'sources':[dict(id=t.trace_id,label=t.label,metadata=t.metadata) for t in traces]}
    return ResponseProfile(grid[good],coeff,str(grating),str(detector),label,metadata)


def residual(trace, reference):
    if trace.units!=reference.units:raise ValueError('Residuals require matching intensity units')
    values=trace.y-interpolate(reference,trace.x)
    if np.count_nonzero(np.isfinite(values))<2:raise ValueError('Spectra have no overlapping coverage')
    return trace.derive(values,{'type':'residual','reference_id':reference.trace_id,
                               'reference_metadata':reference.metadata},label=trace.label+' − '+reference.label)


def analyze_band(trace, lo, hi, *, prominence=0.):
    from scipy.signal import find_peaks, peak_widths
    if not math.isfinite(lo) or not math.isfinite(hi) or hi<=lo:raise ValueError('Band limits must increase')
    if lo<trace.x[0] or hi>trace.x[-1]:raise ValueError('Band lies outside the spectrum')
    x=np.unique(np.r_[lo,trace.x[(trace.x>lo)&(trace.x<hi)],hi])
    y=interpolate(trace,x)
    if len(x)<3 or np.any(~np.isfinite(y)):raise ValueError('Band needs at least three samples with continuous coverage')
    area=float(np.trapezoid(y,x));positive=np.maximum(y,0);p_area=float(np.trapezoid(positive,x))
    centroid=float(np.trapezoid(x*positive,x)/p_area) if p_area>0 else None
    indices,props=find_peaks(y,prominence=max(0.,float(prominence)))
    widths=peak_widths(y,indices,rel_height=0.5) if len(indices) else ([],[],[],[])
    peaks=[]
    for i,idx in enumerate(indices):
        left=float(np.interp(widths[2][i],np.arange(len(x)),x));right=float(np.interp(widths[3][i],np.arange(len(x)),x))
        peaks.append({'wavelength_nm':float(x[idx]),'height':float(y[idx]),'prominence':float(props['prominences'][i]),
                      'width_half_prominence_nm':right-left,'left_nm':left,'right_nm':right})
    return {'trace_id':trace.trace_id,'trace_label':trace.label,'units':trace.units,'band_nm':[lo,hi],
            'signed_area':area,'area_units':trace.units+' nm','positive_centroid_nm':centroid,
            'maximum_wavelength_nm':float(x[np.argmax(y)]),'maximum':float(np.max(y)),
            'peaks':peaks,'width_definition':'half prominence, not fitted FWHM','metadata':trace.metadata}
