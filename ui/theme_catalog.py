from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from ui.theme_models import ThemeDefinition

SYSTEM_THEME = "system"
VISUAL_STUDIO_DARK = "visual_studio_dark"
VISUAL_STUDIO_LIGHT = "visual_studio_light"
NORD_DARK = "nord_dark"
HIGH_CONTRAST_DARK = "high_contrast_dark"


BUILTIN_THEMES: dict[str, ThemeDefinition] = {
    VISUAL_STUDIO_DARK: ThemeDefinition(
        key=VISUAL_STUDIO_DARK,
        display_name="Visual Studio Dark",
    ),
    VISUAL_STUDIO_LIGHT: ThemeDefinition(
        key=VISUAL_STUDIO_LIGHT,
        display_name="Visual Studio Light",
        dark=False,
        window="#F3F3F3",
        panel="#FFFFFF",
        input_background="#FFFFFF",
        alternate_background="#F8F8F8",
        border="#C8C8C8",
        border_hover="#808080",
        text="#1E1E1E",
        muted_text="#616161",
        disabled_text="#9A9A9A",
        accent="#007ACC",
        accent_hover="#0067A3",
        selection="#ADD6FF",
        error="#C42B1C",
        warning="#8A6D00",
        success="#0E7A3E",
        plot_background="#FFFFFF",
        plot_foreground="#1E1E1E",
        plot_grid="#D8D8D8",
        checkbox_border="#707070",
    ),
    NORD_DARK: ThemeDefinition(
        key=NORD_DARK,
        display_name="Nord Dark",
        window="#2E3440",
        panel="#3B4252",
        input_background="#434C5E",
        alternate_background="#3B4252",
        border="#4C566A",
        border_hover="#D8DEE9",
        text="#ECEFF4",
        muted_text="#D8DEE9",
        disabled_text="#7D8799",
        accent="#88C0D0",
        accent_hover="#8FBCBB",
        selection="#5E81AC",
        error="#BF616A",
        warning="#EBCB8B",
        success="#A3BE8C",
        plot_background="#2E3440",
        plot_foreground="#ECEFF4",
        plot_grid="#4C566A",
        checkbox_border="#D8DEE9",
    ),
    HIGH_CONTRAST_DARK: ThemeDefinition(
        key=HIGH_CONTRAST_DARK,
        display_name="High Contrast Dark",
        window="#000000",
        panel="#0B0B0B",
        input_background="#161616",
        alternate_background="#101010",
        border="#CFCFCF",
        border_hover="#FFFFFF",
        text="#FFFFFF",
        muted_text="#D0D0D0",
        disabled_text="#8A8A8A",
        accent="#00AEEF",
        accent_hover="#4CCBFF",
        selection="#005A8D",
        error="#FF5A5A",
        warning="#FFD400",
        success="#63E57A",
        plot_background="#000000",
        plot_foreground="#FFFFFF",
        plot_grid="#666666",
        checkbox_border="#FFFFFF",
    ),
}


def custom_theme_directory() -> Path:
    root = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation
        )
    )
    directory = root / "themes"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def sanitize_theme_key(text: str) -> str:
    key = re.sub(r"[^a-z0-9_\-]+", "_", str(text).strip().lower())
    key = key.strip("_-")
    return key or "custom_theme"


class ThemeCatalog:
    """Built-in and user-defined semantic themes."""

    def __init__(self, custom_directory: Path | None = None) -> None:
        self.custom_directory = (
            Path(custom_directory)
            if custom_directory is not None
            else custom_theme_directory()
        )
        self.custom_directory.mkdir(parents=True, exist_ok=True)

    def themes(self) -> dict[str, ThemeDefinition]:
        themes = dict(BUILTIN_THEMES)
        for path in sorted(self.custom_directory.glob("*.json")):
            try:
                theme = ThemeDefinition.from_json(path)
            except Exception:
                continue
            if theme.key and theme.key != SYSTEM_THEME:
                themes[theme.key] = theme
        return themes

    def items(self, *, include_system: bool = True) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        if include_system:
            items.append((SYSTEM_THEME, "System"))
        items.extend(
            (key, theme.display_name)
            for key, theme in self.themes().items()
        )
        return items

    def get(self, key: str) -> ThemeDefinition | None:
        if str(key) == SYSTEM_THEME:
            return None
        return self.themes().get(str(key))

    def save_custom(
        self,
        theme: ThemeDefinition,
        *,
        overwrite: bool = False,
    ) -> ThemeDefinition:
        key = sanitize_theme_key(theme.key or theme.display_name)
        if key in BUILTIN_THEMES or key == SYSTEM_THEME:
            key = f"custom_{key}"
        saved = replace(theme, key=key)
        path = self.custom_directory / f"{key}.json"
        if path.exists() and not overwrite:
            raise FileExistsError(f"A custom theme named {key!r} already exists.")
        saved.to_json(path)
        return saved

    def delete_custom(self, key: str) -> bool:
        path = self.custom_directory / f"{sanitize_theme_key(key)}.json"
        if not path.exists():
            return False
        path.unlink()
        return True
