from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QApplication

from core.configuration import (
    ConfigurationError,
    build_device_config,
    load_json_defaults,
)
from core.logging_setup import configure_logging, install_exception_hook
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
    mode.add_argument(
        "--real",
        action="store_true",
        help="Use the selected real spectrometer backend and Newport 2936-R",
    )

    parser.add_argument(
        "--spectrometer-backend",
        choices=["auto", "qepro", "andor"],
        default=None,
        help="Real spectrometer backend (default: auto-detect QEPro, then Andor).",
    )
    parser.add_argument(
        "--spectrometer-mode",
        choices=["real", "emulated", "disconnected"],
        default=None,
        help="Initial spectrometer connection mode; overrides the JSON default.",
    )
    parser.add_argument(
        "--qepro-serial-number",
        default=None,
        help=(
            "Open the matching SeaBreeze spectrometer serial instead of the "
            "first available device."
        ),
    )
    parser.add_argument(
        "--power-meter-mode",
        choices=["real", "emulated", "disconnected"],
        default=None,
        help="Initial power-meter connection mode; overrides the JSON default.",
    )
    parser.add_argument(
        "--andor-solis-dir",
        default=None,
        help="Directory containing Andor SDK2 and ATSpectrograph DLLs.",
    )
    parser.add_argument(
        "--andor-camera-dll",
        default=None,
        help=(
            "Exact Andor SDK2 camera DLL path or filename "
            "(default: atmcd64d_legacy.dll)."
        ),
    )
    parser.add_argument("--andor-camera-index", type=int, default=None)
    parser.add_argument("--andor-spectrograph-index", type=int, default=None)

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
        choices=["real", "emulated", "auto", "disconnected"],
        default=None,
        help=(
            "OBIS laser mode. "
            "'real' tries real OBIS boxes only; "
            "'emulated' uses fake COM3/COM5 boxes; "
            "'auto' tries real boxes first and falls back to emulators; "
            "'disconnected' leaves laser control offline."
        ),
    )

    parser.add_argument(
        "--obis-ports",
        nargs="*",
        default=None,
        help="Explicit OBIS serial ports, e.g. --obis-ports COM3 COM5",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Minimum severity written to the rotating application log.",
    )
    parser.add_argument(
        "--file-logging",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable the rotating application log (use --no-file-logging to disable).",
    )
    parser.add_argument(
        "--newport-process-isolation",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Run the Newport .NET driver in a restartable child process "
            "(recommended for hot reconnect)."
        ),
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

    app_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    log_directory = Path(app_data) / "logs" if app_data else Path.cwd() / "logs"
    log_path = configure_logging(
        log_directory,
        level=args.log_level,
        file_enabled=bool(args.file_logging),
    )
    install_exception_hook()
    logger = logging.getLogger("spectrometer_gui")
    logger.info(
        "Application starting: Python=%s, spectrometer=%s/%s, power=%s, "
        "lasers=%s, log=%s",
        sys.version.split()[0],
        config.spectrometer_mode,
        config.spectrometer_backend,
        config.power_meter_mode,
        config.laser_mode,
        log_path,
    )

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

    window = MainWindow(config, theme_manager=manager, log_path=log_path)
    window.show()

    exit_code = app.exec()
    logger.info("Application event loop exited with code %s", exit_code)

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
