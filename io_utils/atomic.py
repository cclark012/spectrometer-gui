from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


@contextmanager
def atomic_text_writer(
    path: str | Path,
    *,
    newline: str | None = "",
    encoding: str = "utf-8",
) -> Iterator[TextIO]:
    """Write a text file through a same-directory temporary file.

    The final path is replaced only after the writer closes successfully. This
    prevents an interrupted spectrum/calibration export from leaving a partially
    written file under the intended result name.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    temporary = Path(temporary_name)

    try:
        with os.fdopen(
            descriptor,
            "w",
            newline=newline,
            encoding=encoding,
        ) as file:
            yield file
        os.replace(temporary, target)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
