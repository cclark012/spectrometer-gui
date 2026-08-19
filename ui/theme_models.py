from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from io_utils.atomic import atomic_text_writer


@dataclass(frozen=True, slots=True)
class ThemeDefinition:
    key: str
    display_name: str
    dark: bool = True
    widget_style: str = "Fusion"

    window: str = "#1E1E1E"
    panel: str = "#252526"
    input_background: str = "#2D2D30"
    alternate_background: str = "#2A2D2E"

    border: str = "#3F3F46"
    border_hover: str = "#707070"

    text: str = "#D4D4D4"
    muted_text: str = "#9D9D9D"
    disabled_text: str = "#707070"

    accent: str = "#007ACC"
    accent_hover: str = "#1C97EA"
    selection: str = "#264F78"

    error: str = "#F14C4C"
    warning: str = "#CCA700"
    success: str = "#4EC9B0"

    plot_background: str = "#1E1E1E"
    plot_foreground: str = "#D4D4D4"
    plot_grid: str = "#3F3F46"

    checkbox_border: str = "#9D9D9D"
    corner_radius_px: int = 3
    control_padding_px: int = 5

    def to_json(self, path: Path) -> None:
        with atomic_text_writer(path) as file:
            file.write(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def from_json(
        cls,
        path: Path,
    ) -> "ThemeDefinition":  # noqa
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)
