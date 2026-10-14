"""Reproducible seeded speed and noise-bias comparison. No hardware required."""
import json
import platform
import time
import numpy as np
from processing.automatic_snr import estimate_automatic_snr, block_noise
from processing.snr import estimate_snr


def run():
    rng = np.random.default_rng(100726)
    results = []
    for size in (1024, 2048, 16384):
        x = np.linspace(400, 1000, size)
        # Broad PL peak, sloping offset and uncorrelated Gaussian noise sigma=5.
        y = 100 + 0.4*(x-400) + 1000*np.exp(-0.5*((x-600)/25)**2) + rng.normal(0, 5, size)
        methods = {'manual_windows': lambda: estimate_snr(x, y, signal_start_nm=500, signal_stop_nm=700,
                    noise_intervals_nm=[(750, 1000)], minimum_noise_pixels=20)}
        for block in (8, 16):
            for method in ('std', 'mad'):
                methods[f'auto_{block}_{method}'] = lambda b=block,m=method: estimate_automatic_snr(x,y,block_size=b,noise_method=m)
        for name, fn in methods.items():
            for _ in range(5): fn()
            times=[]
            for _ in range(150):
                start=time.perf_counter_ns(); result=fn(); times.append((time.perf_counter_ns()-start)/1e6)
            results.append(dict(size=size,method=name,median_ms=float(np.median(times)),p95_ms=float(np.percentile(times,95)),noise_estimate=result.noise_sigma_counts,true_noise=5))
    accuracy=[]
    for block in (8,16):
        for method in ('std','mad'):
            for slope in (0,1):
                estimates=[]
                x=np.linspace(400,1000,2048)
                for _ in range(100):
                    y=100+slope*x+200*np.exp(-0.5*((x-600)/30)**2)+rng.normal(0,5,len(x))
                    estimates.append(block_noise(x,y,block,method)[0])
                accuracy.append(dict(block=block,method=method,slope=slope,mean_noise=float(np.mean(estimates)),std_noise=float(np.std(estimates))))
    return dict(python=platform.python_version(),numpy=np.__version__,platform=platform.platform(),seed=100726,
                iterations=150,results=results,accuracy=accuracy,
                limitations='Synthetic independent Gaussian noise. Timing is local, not the lab PC. Sloping backgrounds still bias automatic signal baseline; dense narrow lines and correlated noise bias noise estimates.')

if __name__=='__main__':
    print(json.dumps(run(),indent=2))
