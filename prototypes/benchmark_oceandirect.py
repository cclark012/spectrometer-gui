# benchmark_oceandirect_qepro.py

import time
import numpy as np
from oceandirect.OceanDirectAPI import OceanDirectAPI, OceanDirectError


def main():
    od = OceanDirectAPI()
    device_count = od.find_usb_devices()
    ids = od.get_device_ids()

    if not ids:
        print("No OceanDirect devices.")
        return

    dev = od.open_device(ids[0])

    try:
        print("serial:", dev.get_serial_number())

        integration_us = 200_000
        averages = 20

        dev.set_integration_time(integration_us)

        # Software averaging.
        dev.set_scans_to_average(1)
        t0 = time.perf_counter()
        spectra = []
        for _ in range(averages):
            spectra.append(np.asarray(dev.get_formatted_spectrum(), dtype=float))
        y_sw = np.mean(np.vstack(spectra), axis=0)
        t_sw = time.perf_counter() - t0

        # Device/accelerated averaging.
        dev.set_scans_to_average(averages)
        t0 = time.perf_counter()
        y_dev = np.asarray(dev.get_formatted_spectrum(), dtype=float)
        t_dev = time.perf_counter() - t0

        print(f"software averaging: {t_sw:.3f} s")
        print(f"device averaging:   {t_dev:.3f} s")
        print(f"speedup:            {t_sw / t_dev:.2f}x")
        print("mean abs diff:", np.mean(np.abs(y_sw - y_dev)))

    finally:
        od.close_device(ids[0])
        od.shutdown()


if __name__ == "__main__":
    main()
