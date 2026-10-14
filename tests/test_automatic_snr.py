import numpy as np
from processing.snr import estimate_snr
from processing.automatic_snr import block_noise


def test_automatic_noise_accuracy_and_slope_invariance():
    rng=np.random.default_rng(123)
    x=np.linspace(400,1000,4096)
    noise=rng.normal(0,5,len(x))
    for size in (8,16):
        first,_=block_noise(x,noise,size)
        second,_=block_noise(x,noise+7*x+100,size)
        assert abs(first-5)<0.3
        assert abs(first-second)<1e-10


def test_auto_dispatch_ignores_manual_ranges_and_uses_raw_noise():
    x=np.linspace(400,1000,2048)
    y=100+1000*np.exp(-0.5*((x-600)/25)**2)+np.random.default_rng(7).normal(0,5,len(x))
    result=estimate_snr(x,y,method='automatic_std',noise_intervals_nm=[],signal_start_nm=1,signal_stop_nm=2)
    assert result.valid
    assert 150<result.peak_snr<250
    assert abs(result.noise_sigma_counts-5)<0.5
    assert result.n_signal_pixels==2048


def test_auto_constant_and_short_data_are_invalid():
    assert not estimate_snr(np.arange(100),np.ones(100),method='automatic_std').valid
    assert not estimate_snr(np.arange(20),np.arange(20),method='automatic_std').valid
    assert not estimate_snr(np.arange(100),np.arange(100),method='bogus').valid


def test_auto_handles_descending_duplicate_nonfinite_data():
    x=np.linspace(400,800,128)
    y=np.sin(x)+np.random.default_rng(42).normal(size=128)
    a=estimate_snr(x,y,method='automatic_mad')
    b=estimate_snr(np.r_[x[::-1],x[-1],np.nan],np.r_[y[::-1],y[-1],1],method='automatic_mad')
    assert a.valid and b.valid
    assert abs(a.peak_snr-b.peak_snr)<1e-10
