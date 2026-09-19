"""A frameless, translucent, always-on-top window that shows a Pillow image.

The Qt counterpart of a Win32 layered window: the card, the meetings panel,
the hover detail and the peek frames are all drawn by the shared Pillow
code, and this widget simply puts the pixels on screen with their alpha.
"""

from __future__ import annotations

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets


def to_qimage(image: Image.Image, premultiplied: bool = False) -> QtGui.QImage:
    """A QImage over the pixels of an RGBA Pillow image (a copy is made)."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    data = image.tobytes("raw", "RGBA")
    fmt = (QtGui.QImage.Format.Format_RGBA8888_Premultiplied if premultiplied
           else QtGui.QImage.Format.Format_RGBA8888)
    qimage = QtGui.QImage(data, image.width, image.height, image.width * 4, fmt)
    return qimage.copy()   # detach from the bytes buffer before it is collected


class BitmapWindow(QtWidgets.QWidget):
    """Shows one image; the owner sets a new one whenever something changes."""

    def __init__(self, tool: bool = True, click_through: bool = False,
                 parent: QtWidgets.QWidget | None = None) -> None:
        flags = (QtCore.Qt.WindowType.FramelessWindowHint
                 | QtCore.Qt.WindowType.WindowStaysOnTopHint
                 | QtCore.Qt.WindowType.NoDropShadowWindowHint)
        if tool:
            flags |= QtCore.Qt.WindowType.Tool
        if click_through:
            flags |= QtCore.Qt.WindowType.WindowTransparentForInput
        super().__init__(parent, flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self._pixmap: QtGui.QPixmap | None = None
        self._opacity = 1.0

    # --- content -----------------------------------------------------------
    def set_image(self, image: Image.Image, premultiplied: bool = False) -> None:
        qimage = to_qimage(image, premultiplied)
        self._pixmap = QtGui.QPixmap.fromImage(qimage)
        if self.size() != qimage.size():
            self.resize(qimage.size())
        self.update()

    def set_opacity(self, opacity: float) -> None:
        opacity = max(0.0, min(1.0, float(opacity)))
        if abs(opacity - self._opacity) > 0.001:
            self._opacity = opacity
            self.setWindowOpacity(opacity)

    def paintEvent(self, _event) -> None:   # noqa: N802 (Qt naming)
        if self._pixmap is None:
            return
        painter = QtGui.QPainter(self)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.end()

    # --- geometry helpers ----------------------------------------------------
    def rect_on_screen(self) -> tuple[int, int, int, int]:
        geometry = self.frameGeometry()
        return geometry.left(), geometry.top(), geometry.right() + 1, geometry.bottom() + 1

    def move_to(self, x: int, y: int) -> None:
        self.move(int(x), int(y))


def work_area(widget: QtWidgets.QWidget | None = None) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the usable area of the screen the widget
    is on, or the primary screen."""
    screen = None
    if widget is not None:
        screen = widget.screen()
    screen = screen or QtGui.QGuiApplication.primaryScreen()
    rect = screen.availableGeometry()
    return rect.left(), rect.top(), rect.right() + 1, rect.bottom() + 1


def virtual_screen() -> tuple[int, int, int, int]:
    rect = QtGui.QGuiApplication.primaryScreen().virtualGeometry()
    return rect.left(), rect.top(), rect.right() + 1, rect.bottom() + 1


def cursor_pos() -> tuple[int, int]:
    point = QtGui.QCursor.pos()
    return point.x(), point.y()
