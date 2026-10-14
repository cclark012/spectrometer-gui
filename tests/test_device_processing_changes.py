import numpy as np
from controllers.device_controller import DeviceController
from core.settings import AcquisitionSettings, DeviceConfig
from processing.smoothing import smooth_spectrum


def test_worker_preserves_unsmoothed_after_background_and_before_smoothing():
    controller=DeviceController(DeviceConfig(emulate=True, fallback_emulator=True, newport_dll='', power_channel=0, spectrometer_mode='emulated'))
    records=[]; backgrounds=[]; errors=[]
    controller.spectrum_ready.connect(records.append)
    controller.background_ready.connect(backgrounds.append)
    controller.acquisition_failed.connect(errors.append)
    controller.background_failed.connect(errors.append)
    controller.connect_spectrometer()
    settings=AcquisitionSettings(1,1,5,False,False,None,measure_power=False,smoothing_window='boxcar')
    controller.capture_background(settings)
    assert len(backgrounds)==1 and not errors
    settings.subtract_background=True
    controller.acquire(settings)
    assert len(records)==1 and not errors
    record=records[0]; bg=backgrounds[0]
    np.testing.assert_allclose(record.unsmoothed_intensities_counts,
        record.raw_intensities_counts-bg.counts_per_s*record.integration_ms/1000.)
    np.testing.assert_allclose(record.intensities_counts,
        smooth_spectrum(record.unsmoothed_intensities_counts,window='boxcar',width=5,boundary='nearest'))
    controller.shutdown()


def test_explicit_andor_connection_preserves_sdk_arguments():
    from pathlib import Path
    from types import SimpleNamespace
    import devices.andor_adapter as adapter
    config=DeviceConfig(emulate=False,fallback_emulator=False,newport_dll=None,power_channel=0,
        spectrometer_mode='real',spectrometer_backend='andor',andor_solis_dir=Path('SOLIS'),
        andor_camera_dll=Path('atmcd64d_legacy.dll'),andor_camera_index=1,andor_spectrograph_index=2)
    calls=[]
    original=adapter.AndorKymeraSpectrometer
    def factory(directory,**kwargs):
        calls.append((directory,kwargs))
        return SimpleNamespace(camera=SimpleNamespace(dll_path=Path('atmcd64d_legacy.dll')))
    try:
        adapter.AndorKymeraSpectrometer=factory
        controller=DeviceController(config)
        spec,description,error,backend=controller._connect_spectrometer()
        assert spec is not None and backend=='andor' and not error
        assert calls==[(Path('SOLIS'),{'camera_index':1,'spectrograph_index':2,'camera_dll':Path('atmcd64d_legacy.dll')})]
        assert 'legacy' in description
    finally:adapter.AndorKymeraSpectrometer=original
