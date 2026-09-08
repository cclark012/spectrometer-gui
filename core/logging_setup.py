from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

LOG_FILE_NAME = "spectrometer-gui.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime


def configure_logging(
    log_directory: str | Path,
    *,
    level: int | str = logging.INFO,
    file_enabled: bool = True,
    console_level: int | str = logging.ERROR,
) -> Path | None:
    """Configure optional bounded file logging and a quiet error console."""

    directory = Path(log_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_FILE_NAME

    root = logging.getLogger()
    root.setLevel(level)
    for handler in tuple(root.handlers):
        if getattr(handler, "_spectrometer_gui_handler", False):
            return path if file_enabled else None

    formatter = _UtcFormatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s "
        "%(name)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    if file_enabled:
        file_handler = RotatingFileHandler(
            path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler._spectrometer_gui_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    console_handler._spectrometer_gui_handler = True  # type: ignore[attr-defined]
    root.addHandler(console_handler)

    logging.captureWarnings(True)
    return path if file_enabled else None


def install_exception_hook() -> None:
    """Record uncaught Python exceptions before delegating to the normal hook."""

    previous = sys.excepthook
    logger = logging.getLogger("spectrometer_gui.uncaught")

    def hook(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            previous(exception_type, exception, traceback)
            return
        logger.critical(
            "Uncaught exception",
            exc_info=(exception_type, exception, traceback),
        )
        previous(exception_type, exception, traceback)

    sys.excepthook = hook
