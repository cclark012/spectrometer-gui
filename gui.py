from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from core.configuration import (
    ConfigurationError,
    build_device_config,
    load_json_defaults,
)
from panels.main_window import MainWindow
from ui.theme import VISUAL_STUDIO_DARK, ThemeManager


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Magneto-PL acquisition GUI")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--emulate",
        action="store_true",
        help="Use spectrometer and power-meter emulators",
    )
    mode.add_argument("--real", action="store_true", help="Use real QE-PRO and Newport 2936-R")

    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent / "config" / "lab_defaults.json"),
        help="Path to a JSON file containing lab defaults.",
    )

    parser.add_argument(
        "--fallback-emulator",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Fall back to emulators if real-device connection fails.",
    )

    parser.add_argument(
        "--newport-dll",
        default=None,
        help="Path to Newport PowerMeterCommands.dll",
    )

    parser.add_argument(
        "--power-channel",
        type=int,
        default=None,
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        config_json = load_json_defaults(args.config)
        config = build_device_config(args, config_json)
    except ConfigurationError as exc:
        parser.error(str(exc))

    app = QApplication([sys.argv[0], *argv])
    
    QApplication.setOrganizationName("YourLab")
    QApplication.setApplicationName("MagnetoPLAcquisition")
    QApplication.setApplicationVersion("0.1")

    manager = ThemeManager()
    theme_name = QSettings().value(
        "ui/theme",
        VISUAL_STUDIO_DARK,
        type=str,
    )
    manager.apply(app, theme_name)

    window = MainWindow(config)
    window.show()

    return app.exec()


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
