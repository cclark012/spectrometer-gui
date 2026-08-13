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
from core.restart import RESTART_EXIT_CODE, launch_replacement_process
from panels.main_window import MainWindow
from ui.theme import (
    LEGACY_THEME_SETTINGS_KEY,
    THEME_SETTINGS_KEY,
    VISUAL_STUDIO_DARK,
    ThemeManager,
)


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

    manager = ThemeManager(app)
    settings = QSettings()

    theme_name = settings.value(
        THEME_SETTINGS_KEY,
        "",
        type=str,
    )

    # Migrate the earlier key if it exists.
    if not theme_name:
        theme_name = settings.value(
            LEGACY_THEME_SETTINGS_KEY,
            VISUAL_STUDIO_DARK,
            type=str,
        )

        settings.setValue(
            THEME_SETTINGS_KEY,
            theme_name,
        )
        settings.sync()

    applied_theme = manager.apply(
        app,
        theme_name,
    )

    # Normalize an invalid or obsolete stored value.
    if applied_theme != theme_name:
        settings.setValue(
            THEME_SETTINGS_KEY,
            applied_theme,
        )
        settings.sync()

    app.setQuitOnLastWindowClosed(False)

    theme_manager = ThemeManager(app)
    theme_manager.apply(app, theme_name)
    window = MainWindow(config, theme_manager=theme_manager)
    window.show()

    exit_code = app.exec()

    if exit_code == RESTART_EXIT_CODE:
        success, pid = launch_replacement_process()

        if not success:
            print(
                "Failed to restart the application.",
                file=sys.stderr,
            )
            return 1

        print(f"Restarted application as PID {pid}.")
        return 0

    return int(exit_code)


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
