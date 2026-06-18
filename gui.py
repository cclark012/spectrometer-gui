# spectroscopy_gui.py

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core.settings import DeviceConfig
from panels.main_window import MainWindow


def load_json_defaults(path: str | None) -> dict:
    if not path:
        return {}

    p = Path(path)

    if not p.exists():
        return {}

    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def value_from_args_or_config(args, config: dict, key: str, fallback):
    value = getattr(args, key, None)

    if value is not None:
        return value

    return config.get(key, fallback)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Magneto-PL acquisition GUI")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--emulate", action="store_true", help="Use spectrometer and power-meter emulators") # noqa
    mode.add_argument("--real", action="store_true", help="Use real QE-PRO and Newport 2936-R")

    parser.add_argument(
        "--config",
        default="config/lab_defaults.json"
    )

    parser.add_argument(
        "--fallback-emulator",
        action="store_true",
        help="Fall back to emulators if real-device connection fails",
    )

    parser.add_argument(
        "--newport-dll",
        default=None,
        help="Path to Newport PowerMeterCommands.dll",
    )

    parser.add_argument(
        "--power-channel",
        type=int,
        default=1,
        help="Newport active channel for CmdGetPower; all-channel reads are still logged",
    )

    parser.add_argument(
        "--laser-mode",
        choices=["real", "emulated", "auto"],
        default=None,
        help=(
            "OBIS laser mode. "
            "'real' tries real OBIS boxes only; "
            "'emulated' uses fake COM3/COM5 boxes; "
            "'auto' tries real boxes first and falls back to emulators."
        ),
    )

    parser.add_argument(
        "--obis-ports",
        nargs="*",
        default=None,
        help="Explicit OBIS serial ports, e.g. --obis-ports COM3 COM5",
    )

    return parser


def main(argv: list[str]) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config_json = load_json_defaults(args.config)
    
    newport_dll = value_from_args_or_config(
        args,
        config_json,
        "newport_dll",
        r"C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll",
    )

    power_channel = int(
        value_from_args_or_config(args, config_json, "power_channel", 1)
    )
    
    laser_mode = str(
        value_from_args_or_config(args, config_json, "laser_mode", "auto")
    )
    
    obis_ports = value_from_args_or_config(args, config_json, "obis_ports", None)
    
    fallback_emulator = bool(
        value_from_args_or_config(args, config_json, "fallback_emulator", False)
    )

    emulate_main_devices = True

    if args.real:
        emulate_main_devices = False
    elif args.emulate:
        emulate_main_devices = True

    dll_path = Path(newport_dll) if newport_dll else None

    config = DeviceConfig(
        emulate=emulate_main_devices,
        fallback_emulator=bool(fallback_emulator),
        newport_dll=dll_path,
        power_channel=int(power_channel),

        emulate_lasers=(laser_mode == "emulated"),
        laser_fallback_emulator=(laser_mode == "auto"),
        obis_ports=obis_ports,
    )

    app = QApplication(sys.argv)
    QApplication.setOrganizationName("YourLab")
    QApplication.setApplicationName("MagnetoPLAcquisition")
    QApplication.setApplicationVersion("0.1")
    window = MainWindow(config)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
