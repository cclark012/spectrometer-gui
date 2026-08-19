# test_newport_clean.py

from __future__ import annotations

import time
from pathlib import Path

DLL_PATH = Path(
    r"C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll"
)


def main() -> int:
    from devices.newport_2936r_dotnet import Newport2936R

    with Newport2936R(DLL_PATH, channel=1, units=2) as pm:
        print("Device key :", pm.device_key)
        print("ID         :", pm.identify())
        print("Channels   :", pm.n_channels)
        print("Run        :", pm.get_run())
        print("Units      :", pm.get_units())
        print("Power text :", pm.get_power_strings())

        print()
        print("Active channel readings:")
        for i in range(10):
            p = pm.read_active_power_watts()
            all_p = pm.read_all_power_with_status()

            print(
                f"{i:04d}, "
                f"active_power_W={p:.12e}, "
                f"all_powers_W={all_p.powers_w}, "
                f"pm_status={all_p.pm_status}"
            )

            time.sleep(0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
