# log_newport_power.py

from __future__ import annotations

import csv
import time
from datetime import UTC, datetime
from pathlib import Path

from devices.newport_2936r_dotnet import Newport2936R

DLL_PATH = Path(
    r"C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll"
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def main() -> int:
    out_path = Path("newport_power_log.csv")

    with Newport2936R(DLL_PATH, channel=1, units=2) as pm:
        with out_path.open("w", newline="") as f:
            writer = csv.writer(f)

            writer.writerow(
                [
                    "timestamp_utc",
                    "elapsed_s",
                    "active_channel",
                    "active_power_W",
                    "ch1_power_W",
                    "ch2_power_W",
                    "ch1_pm_status",
                    "ch2_pm_status",
                    "command_status",
                ]
            )

            t0 = time.perf_counter()

            for i in range(1000):
                elapsed_s = time.perf_counter() - t0

                active_power = pm.read_active_power_watts()
                pws = pm.read_all_power_with_status()

                ch1_power = pws.powers_w[0] if len(pws.powers_w) >= 1 else ""
                ch2_power = pws.powers_w[1] if len(pws.powers_w) >= 2 else ""

                ch1_status = pws.pm_status[0] if len(pws.pm_status) >= 1 else ""
                ch2_status = pws.pm_status[1] if len(pws.pm_status) >= 2 else ""

                writer.writerow(
                    [
                        utc_now_iso(),
                        f"{elapsed_s:.6f}",
                        pm.channel,
                        f"{active_power:.12e}",
                        f"{ch1_power:.12e}" if ch1_power != "" else "",
                        f"{ch2_power:.12e}" if ch2_power != "" else "",
                        ch1_status,
                        ch2_status,
                        pws.command_status,
                    ]
                )

                print(
                    f"{i:04d}, "
                    f"active={active_power:.12e} W, "
                    f"channels={pws.powers_w}, "
                    f"status={pws.pm_status}"
                )

                time.sleep(0.05)

    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
