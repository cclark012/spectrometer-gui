import json
from pathlib import Path
import numpy as np
import pytest
from core.records import BackgroundSpectrum
from io_utils.background_io import save_background,load_background
from io_utils.analysis_io import load_trace,save_trace,load_response,save_response
from processing.spectral_analysis import (SpectralTrace,ResponseProfile,subtract_baseline,subtract_reference,
    as_count_rate,apply_response,derive_response,stitch,residual,analyze_band,interpolate)


def trace(x,y,**metadata):
    return SpectralTrace(np.asarray(x),np.asarray(y),'sample','counts',{'integration_ms':1000,**metadata})


def test_baseline_polynomials_exact_and_original_unchanged():
    x=np.linspace(400,800,401)
    peak=100*np.exp(-((x-600)/10)**2)
    for method,bg in [('linear',20+0.5*x),('quadratic',30+0.03*(x-550)**2)]:
        original=trace(x,peak+bg)
        result,baseline=subtract_baseline(original,method,intervals=[(400,450),(750,800)])
        np.testing.assert_allclose(baseline,bg,atol=1e-8)
        np.testing.assert_allclose(result.y,peak,atol=1e-8)
        np.testing.assert_array_equal(original.y,peak+bg)
        assert not original.y.flags.writeable
    result,_=subtract_baseline(trace(x,peak+10),'constant',offset=10)
    np.testing.assert_allclose(result.y,peak,atol=1e-12)


def test_response_interpolation_order_identity_and_no_double_apply():
    raw=trace([400,500,600],[12,22,32],grating='1')
    profile=ResponseProfile(np.array([400,600]),np.array([0.5,1]),'1','1')
    with pytest.raises(ValueError,match='baseline'):apply_response(raw,profile,grating='1',detector='1')
    base,_=subtract_baseline(raw,offset=2)
    corrected=apply_response(base,profile,grating='1',detector='1')
    np.testing.assert_allclose(corrected.y,[5,15,30])
    with pytest.raises(ValueError,match='match'):apply_response(base,profile,grating='2',detector='1')
    with pytest.raises(ValueError,match='already'):apply_response(corrected,profile,grating='1',detector='1')
    with pytest.raises(ValueError,match='precede'):subtract_baseline(corrected)
    wrong=trace([400,500,600],[1,2,3],grating='2',baseline_applied=True)
    with pytest.raises(ValueError,match='conflicts'):apply_response(wrong,profile,grating='1',detector='1')


def test_response_masks_outside_coverage_and_internal_gaps():
    t=trace(np.arange(1,11),np.ones(10),baseline_applied=True)
    p=ResponseProfile(np.array([2,4,7,9]),np.ones(4),'1','1',metadata={'coverage_nm':[[2,4],[7,9]]})
    with pytest.raises(ValueError,match='cover'):apply_response(t,p,grating='1',detector='1')
    result=apply_response(t,p,grating='1',detector='1',crop=True)
    np.testing.assert_array_equal(np.flatnonzero(np.isnan(result.y)),[0,4,5,9])


def test_profile_roundtrip_and_supplied_coefficients(tmp_path):
    path=Path(__file__).resolve().parents[1]/'processing/data/grating1_detector1_coefficients.csv'
    p=load_response(path,grating='1',detector='1')
    assert len(p.x)==2046 and p.coefficients.max()==1
    destination=tmp_path/'profile.csv';save_response(destination,p);q=load_response(destination)
    assert p.fingerprint==q.fingerprint
    with pytest.raises(ValueError,match='profile'):load_trace(destination)


def test_derive_lamp_response_across_exposures_and_centers():
    def lamp(x):return np.exp(41.856-4808.1/x-5*np.log(x))
    traces=[]
    for x,ms in [(np.arange(400,651,dtype=float),100),(np.arange(600,801,dtype=float),500)]:
        response=0.5+x/1000
        traces.append(trace(x,lamp(x)/response*ms/1000,integration_ms=ms,baseline_applied=True,grating='1'))
    p=derive_response(traces,grating='1',detector='1',step_nm=1)
    target=(0.5+p.x/1000)/(0.5+p.x[-1]/1000)
    np.testing.assert_allclose(p.coefficients,target,rtol=1e-12)
    assert p.metadata['normalization']=='one global maximum'


def test_lamp_rejects_unsubtracted_saturated_nonpositive_and_mixed_detectors():
    good=trace([400,500,600],[10,20,30],baseline_applied=True)
    with pytest.raises(ValueError,match='Baseline'):derive_response([trace([400,500],[1,2])],grating='1',detector='1')
    with pytest.raises(ValueError,match='Saturated'):derive_response([trace([400,500],[1,2],baseline_applied=True,saturation_observed=True)],grating='1',detector='1')
    with pytest.raises(ValueError,match='positive'):derive_response([trace([400,500],[-1,2],baseline_applied=True)],grating='1',detector='1')
    other=trace([500,600,700],[1,2,3],baseline_applied=True,detector_serial='other')
    with pytest.raises(ValueError,match='detector'):derive_response([good,other],grating='1',detector='1')


def test_stitching_blends_preserves_gaps_and_exposure_rules():
    a=trace([1,2,3,4],[2,4,6,8]);b=trace([3,4,5,6],[6,8,10,12])
    result=stitch([a,b]);np.testing.assert_allclose(result.y,2*result.x)
    c=trace([8,9,10],[16,18,20]);gap=stitch([a,c])
    assert np.isnan(gap.y[(gap.x>4)&(gap.x<8)]).all()
    assert np.isnan(interpolate(gap,[5])[0])
    different=trace([3,4,5],[3,4,5],integration_ms=500)
    with pytest.raises(ValueError,match='exposures'):stitch([a,different])
    normalized=stitch([as_count_rate(a),as_count_rate(different)])
    np.testing.assert_allclose(normalized.y,2*normalized.x)


def test_uncalibrated_overlap_matching_and_calibrated_profile_rule():
    a=trace(np.arange(1,7),np.arange(1,7)*2.)
    b=trace(np.arange(3,9),np.arange(3,9)*4.)
    result=stitch([a,b],match_overlap=True)
    np.testing.assert_allclose(result.y,result.x*2)
    corrected=[t.derive(t.y,{'type':'test'},metadata={'response_profile':'same'}) for t in (a,b)]
    with pytest.raises(ValueError,match='calibrated stitching'):stitch(corrected)
    with pytest.raises(ValueError,match='scale'):stitch(corrected,calibrated=True,match_overlap=True)
    assert stitch(corrected,calibrated=True).metadata['response_profile']=='same'
    different=corrected[1].derive(corrected[1].y,{'type':'test'},metadata={'response_profile':'different'})
    with pytest.raises(ValueError,match='same response'):stitch([corrected[0],different],calibrated=True)


def test_residuals_and_peak_band_measurements():
    x=np.linspace(400,800,2001);sigma=10.;y=100*np.exp(-0.5*((x-600)/sigma)**2)
    t=trace(x,y);report=analyze_band(t,500,700,prominence=1)
    assert abs(report['signed_area']-100*sigma*np.sqrt(2*np.pi))<0.01
    assert abs(report['positive_centroid_nm']-600)<1e-9
    assert len(report['peaks'])==1
    assert abs(report['peaks'][0]['width_half_prominence_nm']-2.35482*sigma)<0.01
    np.testing.assert_allclose(residual(t,t).y,0)
    with pytest.raises(ValueError,match='outside'):analyze_band(t,200,500)


def test_analysis_and_background_roundtrip(tmp_path):
    t=trace([1,2,3],[2,4,6],grating='1')
    corrected,_=subtract_baseline(t,offset=1)
    path=tmp_path/'analysis.csv';save_trace(path,corrected);loaded=load_trace(path)
    assert loaded.trace_id==corrected.trace_id and loaded.metadata==corrected.metadata
    np.testing.assert_array_equal(loaded.y,corrected.y)
    bg=BackgroundSpectrum('now',np.array([1.,2.,3.]),np.array([1.,1.,1.]),100,1,True,False,configuration_signature='abc')
    path=tmp_path/'bg.csv';save_background(path,bg)
    assert load_background(path).configuration_signature=='abc'
    reference=load_trace(path)
    sample=as_count_rate(t)
    result,_=subtract_reference(sample,reference)
    np.testing.assert_array_equal(result.y,[1,3,5])
    with pytest.raises(ValueError,match='units'):subtract_reference(t,reference)


def test_invalid_arrays_and_baseline_windows():
    with pytest.raises(ValueError,match='Duplicate'):trace([1,1,2],[1,2,3])
    with pytest.raises(ValueError,match='distinct'):subtract_baseline(trace([1,2,3],[1,2,3]),'linear',intervals=[])
    with pytest.raises(ValueError,match='positive'):ResponseProfile(np.array([1,2]),np.array([0,1]),'1','1')


def test_stitch_rejects_known_optical_changes_and_preserves_exposure():
    a=trace([1,2,3],[1,2,3],filter_state='open')
    b=trace([2,3,4],[2,3,4],filter_state='ND2')
    with pytest.raises(ValueError,match='filter_state'):stitch([a,b])
    c=trace([2,3,4],[2,3,4],filter_state='open')
    result=stitch([a,c])
    assert result.metadata['integration_ms']==1000
    assert result.metadata['filter_state']=='open'
    d=trace([3,4,5],[3,4,5],filter_state='open')
    np.testing.assert_allclose(stitch([result,d]).y,[1,2,3,4,5])


def test_derived_profile_checks_gain_and_serial():
    x=np.arange(400.,701.)
    lamp=np.exp(41.856-4808.1/x-5*np.log(x))
    source=trace(x,lamp,baseline_applied=True,detector_serial='camera',instrument_state={'preamp_gain':2})
    profile=derive_response([source],grating='1',detector='1')
    assert apply_response(source,profile,grating='1',detector='1').metadata['response_profile']
    changed=trace(x,lamp,baseline_applied=True,detector_serial='camera',instrument_state={'preamp_gain':4})
    with pytest.raises(ValueError,match='preamp_gain'):apply_response(changed,profile,grating='1',detector='1')
