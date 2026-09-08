from __future__ import annotations

import logging
from pathlib import Path

from core.logging_setup import BACKUP_COUNT, MAX_LOG_BYTES, configure_logging


def test_rotating_application_log_is_created(tmp_path: Path) -> None:
    root = logging.getLogger()
    original_handlers = tuple(root.handlers)
    original_level = root.level
    path = configure_logging(tmp_path, level="INFO")
    try:
        logging.getLogger("spectrometer_gui.test").info("instrument event")
        added_handlers = [
            handler for handler in root.handlers if handler not in original_handlers
        ]
        for handler in added_handlers:
            handler.flush()

        assert path.exists()
        assert "instrument event" in path.read_text(encoding="utf-8")
        file_handler = next(
            handler
            for handler in added_handlers
            if getattr(handler, "baseFilename", "")
        )
        assert file_handler.maxBytes == MAX_LOG_BYTES
        assert file_handler.backupCount == BACKUP_COUNT
    finally:
        for handler in tuple(root.handlers):
            if handler not in original_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(original_level)


def test_file_logging_can_be_disabled(tmp_path: Path) -> None:
    root = logging.getLogger()
    original_handlers = tuple(root.handlers)
    original_level = root.level
    try:
        path = configure_logging(tmp_path, file_enabled=False)
        assert path is None
        assert not (tmp_path / "spectrometer-gui.log").exists()
        added_handlers = [
            handler for handler in root.handlers if handler not in original_handlers
        ]
        assert len(added_handlers) == 1
        assert not getattr(added_handlers[0], "baseFilename", "")
    finally:
        for handler in tuple(root.handlers):
            if handler not in original_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(original_level)
