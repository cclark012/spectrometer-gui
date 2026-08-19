"""Compare OceanDirect software averaging with on-device averaging.

Run from the repository root after installing OceanDirect:

    python -m benchmarks.oceandirect_averaging
"""

from __future__ import annotations

import time

import numpy as np


def main() -> int:
    try:
        from oceandirect.OceanDirectAPI import OceanDirectAPI
    except ImportError as exc:
        raise SystemExit(
            "OceanDirect is not installed. Install the vendor SDK before running "
            "this benchmark."
        ) from exc

    api = OceanDirectAPI()
    device_id: int | None = None

    try:
        api.find_usb_devices()
        device_ids = list(api.get_device_ids())
        if not device_ids:
            print("No OceanDirect devices found.")
            return 1

        device_id = int(device_ids[0])
        device = api.open_device(device_id)
        print("Serial:", device.get_serial_number())

        integration_us = 200_000
        averages = 20
        device.set_integration_time(integration_us)

        device.set_scans_to_average(1)
        start = time.perf_counter()
        spectra = [
            np.asarray(device.get_formatted_spectrum(), dtype=float)
            for _ in range(averages)
        ]
        software_average = np.mean(np.vstack(spectra), axis=0)
        software_seconds = time.perf_counter() - start

        device.set_scans_to_average(averages)
        start = time.perf_counter()
        device_average = np.asarray(device.get_formatted_spectrum(), dtype=float)
        device_seconds = time.perf_counter() - start

        print(f"Integration time:   {integration_us / 1000.0:.1f} ms")
        print(f"Averages:           {averages}")
        print(f"Software averaging: {software_seconds:.3f} s")
        print(f"Device averaging:   {device_seconds:.3f} s")
        if device_seconds > 0:
            print(f"Speedup:            {software_seconds / device_seconds:.2f}x")
        print(
            "Mean absolute difference:",
            float(np.mean(np.abs(software_average - device_average))),
        )
        return 0
    finally:
        if device_id is not None:
            try:
                api.close_device(device_id)
            except Exception:
                pass
        try:
            api.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
