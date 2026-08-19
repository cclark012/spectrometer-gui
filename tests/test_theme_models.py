from __future__ import annotations

from ui.theme_models import ThemeDefinition


def test_theme_definition_json_round_trip(tmp_path) -> None:
    path = tmp_path / "theme.json"
    theme = ThemeDefinition(
        key="lab_theme",
        display_name="Lab Theme",
        accent="#123456",
        corner_radius_px=5,
    )

    theme.to_json(path)

    assert ThemeDefinition.from_json(path) == theme
    assert not list(tmp_path.glob("*.tmp"))
