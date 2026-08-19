from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMainWindow


def clamp_main_window_to_available_screen(
    window: QMainWindow,
    *,
    margin_px: int = 8,
) -> None:
    """Clamp a normal main-window geometry to the active screen.

    Qt emits ``QWindowsWindow::setGeometry`` warnings when a restored layout or
    newly visible dock requires a geometry larger than the screen's available
    area. This helper is intentionally a no-op for maximized/full-screen windows.
    The window's child hierarchy still must permit shrinking; use scroll areas for
    vertically dense docks rather than assigning large minimum heights.
    """

    if window.isMaximized() or window.isFullScreen():
        return

    screen = window.screen()
    if screen is None:
        screen = QGuiApplication.screenAt(window.frameGeometry().center())
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return

    available = screen.availableGeometry().adjusted(
        margin_px,
        margin_px,
        -margin_px,
        -margin_px,
    )
    if available.width() <= 0 or available.height() <= 0:
        return

    current = window.geometry()
    width = min(max(1, current.width()), available.width())
    height = min(max(1, current.height()), available.height())

    x_min = available.left()
    x_max = available.right() - width + 1
    y_min = available.top()
    y_max = available.bottom() - height + 1

    x = min(max(current.x(), x_min), x_max)
    y = min(max(current.y(), y_min), y_max)

    target = QRect(x, y, width, height)
    if target != current:
        window.setGeometry(target)
