# spectroscopy_gui.py

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core.settings import DeviceConfig
from panels.main_window import MainWindow


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Magneto-PL acquisition GUI")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--emulate", action="store_true", help="Use spectrometer and power-meter emulators")
    mode.add_argument("--real", action="store_true", help="Use real QE-PRO and Newport 2936-R")

    parser.add_argument(
        "--fallback-emulator",
        action="store_true",
        help="Fall back to emulators if real-device connection fails",
    )

    parser.add_argument(
        "--newport-dll",
        default=r"C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll",
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
        default="auto",
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

    emulate_main_devices = True

    if args.real:
        emulate_main_devices = False
    elif args.emulate:
        emulate_main_devices = True

    dll_path = Path(args.newport_dll) if args.newport_dll else None

    laser_mode = str(args.laser_mode)

    config = DeviceConfig(
        emulate=emulate_main_devices,
        fallback_emulator=bool(args.fallback_emulator),
        newport_dll=dll_path,
        power_channel=int(args.power_channel),

        emulate_lasers=(laser_mode == "emulated"),
        laser_fallback_emulator=(laser_mode == "auto"),
        obis_ports=args.obis_ports,
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
