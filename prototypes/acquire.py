from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from newport_2936r import (
    Newport2936R,
    PowerMeasurement,
    SerialTransport,
    VisaTransport,
    list_serial_ports,
    list_visa_resources,
)
from qe_pro import QEProSpectrometer, list_spectrometers


def _make_power_meter(args: argparse.Namespace) -> Newport2936R:
    if bool(args.serial_port) == bool(args.visa_resource):
        raise SystemExit("Specify exactly one of --serial-port or --visa-resource")

    if args.serial_port:
        transport = SerialTransport(port=args.serial_port, timeout_s=args.timeout_s)
    else:
        transport = VisaTransport(
            resource_name=args.visa_resource,
            timeout_s=args.timeout_s,
            write_termination=args.write_termination,
            read_termination=args.read_termination,
        )
    meter = Newport2936R(transport)
    meter.set_run(True)
    meter.set_units(args.units_code)
    meter.set_channel(args.channel)
    return meter


def cmd_list_ports(args: argparse.Namespace) -> int:
    print("Serial ports:")
    for port in list_serial_ports():
        print(f"  {port}")
    print("VISA resources:")
    for resource in list_visa_resources():
        print(f"  {resource}")
    return 0



def cmd_list_spectrometers(args: argparse.Namespace) -> int:
    devices = list_spectrometers(backend=args.backend)
    if not devices:
        print("No spectrometers found.")
        return 1
    print(json.dumps(devices, indent=2))
    return 0



def cmd_power(args: argparse.Namespace) -> int:
    outfile = Path(args.csv) if args.csv else None
    with _make_power_meter(args) as meter:
        print(meter.identify())
        if outfile:
            outfile.parent.mkdir(parents=True, exist_ok=True)
            if not outfile.exists():
                outfile.write_text("timestamp_utc,channel,value,units_code,units_name\n", encoding="utf-8")
        for _ in range(args.repeat):
            reading = meter.read_power(channel=args.channel)
            print(f"{reading.timestamp_utc}  ch={reading.channel}  {reading.value:.8e} {reading.units_name}")
            if outfile:
                with outfile.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"{reading.timestamp_utc},{reading.channel},{reading.value:.12e},"
                        f"{reading.units_code},{reading.units_name}\n"
                    )
            if args.repeat > 1:
                time.sleep(args.interval_s)
    return 0



def cmd_spectrum(args: argparse.Namespace) -> int:
    with QEProSpectrometer(serial_number=args.serial_number, backend=args.backend) as spec:
        spectrum = spec.acquire_spectrum(
            integration_time_ms=args.integration_ms,
            averages=args.averages,
        )
        csv_path = QEProSpectrometer.save_csv(args.outfile, spectrum)
        meta_path = QEProSpectrometer.save_metadata_json(Path(args.outfile).with_suffix(".json"), spectrum)
        print(f"Saved spectrum to {csv_path}")
        print(f"Saved metadata to {meta_path}")
    return 0



def _power_as_dict(reading: PowerMeasurement) -> dict:
    return {
        "timestamp_utc": reading.timestamp_utc,
        "channel": reading.channel,
        "value": reading.value,
        "units_code": reading.units_code,
        "units_name": reading.units_name,
    }



def cmd_both(args: argparse.Namespace) -> int:
    prefix = Path(args.out_prefix)
    with _make_power_meter(args) as meter, QEProSpectrometer(
        serial_number=args.serial_number,
        backend=args.backend,
    ) as spec:
        power_before = meter.read_power(channel=args.channel)
        spectrum = spec.acquire_spectrum(args.integration_ms, averages=args.averages)
        power_after = meter.read_power(channel=args.channel)

        spectrum_csv = QEProSpectrometer.save_csv(prefix.with_suffix(".csv"), spectrum)
        metadata = {
            "power_before": _power_as_dict(power_before),
            "power_after": _power_as_dict(power_after),
            "power_mean": 0.5 * (power_before.value + power_after.value),
        }
        metadata_json = QEProSpectrometer.save_metadata_json(prefix.with_suffix(".json"), spectrum, metadata)
        print(f"Saved spectrum to {spectrum_csv}")
        print(f"Saved metadata to {metadata_json}")
    return 0



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QE-Pro and Newport 2936-R starter CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-ports", help="List serial ports and VISA resources")
    p.set_defaults(func=cmd_list_ports)

    p = sub.add_parser("list-spectrometers", help="List Ocean Optics spectrometers visible to seabreeze")
    p.add_argument("--backend", choices=["cseabreeze", "pyseabreeze"], default=None)
    p.set_defaults(func=cmd_list_spectrometers)

    p = sub.add_parser("power", help="Read power from the Newport meter")
    _add_power_meter_args(p)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--interval-s", type=float, default=0.5)
    p.add_argument("--csv", default=None, help="Optional CSV output file")
    p.set_defaults(func=cmd_power)

    p = sub.add_parser("spectrum", help="Acquire a QE-Pro spectrum and save it")
    _add_spectrometer_args(p)
    p.add_argument("--outfile", required=True, help="CSV output path")
    p.set_defaults(func=cmd_spectrum)

    p = sub.add_parser("both", help="Acquire a spectrum and bracket it with power readings")
    _add_power_meter_args(p)
    _add_spectrometer_args(p)
    p.add_argument("--out-prefix", required=True, help="Path prefix; writes .csv and .json")
    p.set_defaults(func=cmd_both)

    return parser



def _add_power_meter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--serial-port", default=None, help="Example: COM5 or /dev/ttyUSB0")
    parser.add_argument("--visa-resource", default=None, help="Example: ASRL5::INSTR")
    parser.add_argument("--write-termination", default="\r", help="VISA only; use '' for Newport USB if needed")
    parser.add_argument("--read-termination", default="\n", help="VISA only")
    parser.add_argument("--timeout-s", type=float, default=1.0)
    parser.add_argument("--channel", type=int, default=1, choices=[1, 2])
    parser.add_argument(
        "--units-code",
        type=int,
        default=2,
        help="0=A, 1=V, 2=W, 3=W/cm^2, 4=J, 5=J/cm^2, 6=dBm, 11=sun",
    )



def _add_spectrometer_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--serial-number", default=None)
    parser.add_argument("--backend", choices=["cseabreeze", "pyseabreeze"], default=None)
    parser.add_argument("--integration-ms", type=float, required=True)
    parser.add_argument("--averages", type=int, default=1)



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
