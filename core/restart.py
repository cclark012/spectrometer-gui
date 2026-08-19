from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QProcess

RESTART_EXIT_CODE = 1001


@dataclass(frozen=True, slots=True)
class LaunchCommand:
    program: str
    arguments: list[str]
    working_directory: str


def current_launch_command() -> LaunchCommand:
    """
    Reconstruct the command used to launch the current application.

    Handles both:
      - python gui.py ...
      - a future frozen executable
    """

    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()

        return LaunchCommand(
            program=str(executable),
            arguments=list(sys.argv[1:]),
            working_directory=str(executable.parent),
        )

    script = Path(sys.argv[0]).resolve()

    return LaunchCommand(
        program=sys.executable,
        arguments=[str(script), *sys.argv[1:]],
        working_directory=str(script.parent),
    )


def launch_replacement_process() -> tuple[bool, int]:
    command = current_launch_command()

    result = QProcess.startDetached(
        command.program,
        command.arguments,
        command.working_directory,
    )

    # Current PySide6 returns (success, pid) for the static overload.
    if isinstance(result, tuple):
        success, pid = result
        return bool(success), int(pid)

    # Defensive fallback for bindings that return only bool.
    return bool(result), -1
