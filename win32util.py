"""Win32 plumbing: DPI awareness, layered windows, global hotkeys, monitors."""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
from ctypes import wintypes

# Importable anywhere -- the shared modules and their tests pull this in --
# but every function here is Windows-only and will fail off it.
WINDOWS = sys.platform == "win32"
user32 = ctypes.windll.user32 if WINDOWS else None
gdi32 = ctypes.windll.gdi32 if WINDOWS else None

# --- constants -------------------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


# --- structures ------------------------------------------------------------
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


def _configure_prototypes() -> None:
    """Declare argtypes for every GDI/USER call we make.

    Without these, ctypes marshals arguments as 32-bit ints and silently
    truncates -- or, on Python 3.14, raises OverflowError -- on the 64-bit
    handles CreateDIBSection hands back.
    """
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE),
        wintypes.HDC,
        ctypes.POINTER(wintypes.POINT),
        wintypes.COLORREF,
        ctypes.POINTER(BLENDFUNCTION),
        wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL


def enable_dpi_awareness() -> None:
    """Per-monitor-v2 so the card is rendered at native pixels, not upscaled."""
    try:
        user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        return
    except (AttributeError, OSError):
        pass
    try:  # Windows 8.1 fallback: PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def dpi_for_window(hwnd: int) -> int:
    try:
        dpi = user32.GetDpiForWindow(wintypes.HWND(hwnd))
        if dpi:
            return int(dpi)
    except (AttributeError, OSError):
        pass
    return 96


def virtual_screen() -> tuple[int, int, int, int]:
    """Bounding box across all monitors: (left, top, width, height)."""
    try:
        gsm = user32.GetSystemMetrics
        return (
            gsm(SM_XVIRTUALSCREEN),
            gsm(SM_YVIRTUALSCREEN),
            gsm(SM_CXVIRTUALSCREEN),
            gsm(SM_CYVIRTUALSCREEN),
        )
    except (AttributeError, OSError):
        return (0, 0, 1920, 1080)


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def work_area(hwnd: int) -> tuple[int, int, int, int]:
    """Usable bounds (taskbar excluded) of the monitor holding the window."""
    try:
        monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # NEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
            return (rect.left, rect.top, rect.right, rect.bottom)
    except (AttributeError, OSError):
        pass
    left, top, width, height = virtual_screen()
    return (left, top, left + width, top + height)


def work_area_at(x: int, y: int) -> tuple[int, int, int, int]:
    """Usable bounds of the monitor under a screen point.

    A window that has not been placed yet sits on the primary monitor, so
    asking for *its* monitor would clamp it to the wrong screen; ask for the
    monitor under the thing it is meant to sit beside instead.
    """
    try:
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        monitor = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # NEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
            return (rect.left, rect.top, rect.right, rect.bottom)
    except (AttributeError, OSError):
        pass
    left, top, width, height = virtual_screen()
    return (left, top, left + width, top + height)


SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4


def show_window(hwnd: int, visible: bool) -> None:
    """Hide the window or bring it back, without stealing focus.

    Done at the Win32 level rather than through Tk's withdraw/deiconify: Tk
    re-applies its own idea of the geometry on deiconify, which stops matching
    physical pixels on mixed-DPI setups, and may activate the window -- which
    a peek firing every half hour must never do to whatever you are typing in.
    """
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOWNOACTIVATE if visible else SW_HIDE)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """True screen rect in physical pixels: (left, top, right, bottom)."""
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return (point.x, point.y)


def move_window(hwnd: int, x: int, y: int) -> None:
    """Position in physical pixels.

    Tk's own geometry is in whatever coordinate space Tk believes in, which
    stops matching physical pixels on mixed-DPI multi-monitor setups (and when
    a bundled manifest pins the process to system-DPI awareness). SetWindowPos
    is unambiguous, so all positioning goes through here.
    """
    user32.SetWindowPos(
        wintypes.HWND(hwnd), None, int(x), int(y), 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
    )


def clamp_to_screen(hwnd: int, inset: int = 0) -> tuple[int, int]:
    """Nudge the window fully inside its monitor's work area. Returns (x, y)."""
    left, top, right, bottom = window_rect(hwnd)
    width, height = right - left, bottom - top
    wl, wt, wr, wb = work_area(hwnd)
    x = min(max(left, wl - inset), max(wl - inset, wr - width + inset))
    y = min(max(top, wt - inset), max(wt - inset, wb - height + inset))
    if (x, y) != (left, top):
        move_window(hwnd, x, y)
    return (x, y)


DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19   # early 1809 builds
DWMWA_BORDER_COLOR = 34                  # Windows 11 only
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def _colorref(colour: str) -> int:
    r, g, b = (int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return (b << 16) | (g << 8) | r


def _dwm_set(hwnd: int, attribute: int, value) -> bool:
    try:
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), attribute, ctypes.byref(value), ctypes.sizeof(value)
        ) == 0
    except (AttributeError, OSError):
        return False


def set_titlebar(hwnd: int, dark: bool = True, colour: str | None = None,
                 text: str | None = None) -> None:
    """Match a dialog's title bar to its palette.

    Dark/light mode works from Windows 10 1809; the caption and text colours
    are Windows 11 and are simply ignored where unsupported, which leaves the
    plain dark or light bar -- still the right family of colour.
    """
    flag = ctypes.c_int(1 if dark else 0)
    if not _dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, flag):
        _dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, flag)
    if colour:
        _dwm_set(hwnd, DWMWA_CAPTION_COLOR, wintypes.DWORD(_colorref(colour)))
        _dwm_set(hwnd, DWMWA_BORDER_COLOR, wintypes.DWORD(_colorref(colour)))
    if text:
        _dwm_set(hwnd, DWMWA_TEXT_COLOR, wintypes.DWORD(_colorref(text)))


def set_dark_titlebar(hwnd: int) -> None:
    """Windows 10 1809+ dark title bar, so dialogs match the clock."""
    set_titlebar(hwnd, dark=True)


def _long_funcs():
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_long.restype = ctypes.c_ssize_t
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    set_long.restype = ctypes.c_ssize_t
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    return get_long, set_long


def update_ex_style(hwnd: int, add: int = 0, remove: int = 0) -> None:
    get_long, set_long = _long_funcs()
    style = get_long(wintypes.HWND(hwnd), GWL_EXSTYLE)
    style = (style | add) & ~remove
    set_long(wintypes.HWND(hwnd), GWL_EXSTYLE, style)


def has_ex_style(hwnd: int, flag: int) -> bool:
    get_long, _ = _long_funcs()
    return bool(get_long(wintypes.HWND(hwnd), GWL_EXSTYLE) & flag)


def push_layered_bitmap(hwnd: int, image, opacity: float = 1.0, position=None) -> None:
    """Paint an RGBA image as the window's content, with per-pixel alpha.

    `image` must already be premultiplied (see render.to_premultiplied_bgra).
    `opacity` rides on top of the per-pixel alpha as a constant factor, so
    changing it never requires re-rendering the card.

    Passing `position` moves and resizes the window in the same call, which
    matters while animating: doing them as two calls tears visibly.
    """
    width, height = image.size
    data = image.tobytes("raw", "BGRA")

    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB

    bits = ctypes.c_void_p()
    hbitmap = gdi32.CreateDIBSection(
        hdc_mem, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    old = None
    try:
        if not hbitmap:
            return
        ctypes.memmove(bits, data, len(data))
        old = gdi32.SelectObject(hdc_mem, hbitmap)

        size = wintypes.SIZE(width, height)
        src = wintypes.POINT(0, 0)
        dst = wintypes.POINT(int(position[0]), int(position[1])) if position else None
        blend = BLENDFUNCTION(
            AC_SRC_OVER, 0, max(0, min(255, int(round(opacity * 255)))), AC_SRC_ALPHA
        )
        user32.UpdateLayeredWindow(
            wintypes.HWND(hwnd),
            hdc_screen,
            ctypes.byref(dst) if dst else None,  # else keep the current position
            ctypes.byref(size),
            hdc_mem,
            ctypes.byref(src),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )
    finally:
        if old:
            gdi32.SelectObject(hdc_mem, old)
        if hbitmap:
            gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


VK_NAMES = {
    0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4", 0x74: "F5", 0x75: "F6",
    0x76: "F7", 0x77: "F8", 0x78: "F9", 0x79: "F10", 0x7A: "F11", 0x7B: "F12",
}


def hotkey_label(mods: int, vk: int) -> str:
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_ALT:
        parts.append("Alt")
    parts.append(VK_NAMES.get(vk, chr(vk) if 32 < vk < 127 else "?"))
    return "+".join(parts)


class HotkeyListener(threading.Thread):
    """Global hotkeys on a private thread with its own message pump.

    Hotkeys are delivered to the thread that registered them, and Tk owns the
    main thread's queue, so registration lives here and presses come back over
    a queue that the Tk loop polls.

    Each action supplies a list of candidate combinations and the first one
    Windows accepts wins: a global hotkey is machine-wide, so any combination
    can already be taken by another running app (ERROR_HOTKEY_ALREADY_REGISTERED).
    Callers must check `registered` -- a feature whose only escape is a hotkey
    has to stay switched off when nothing could be registered.
    """

    def __init__(self, bindings: dict[int, list[tuple[int, int]]]) -> None:
        """`bindings` maps an id to candidate (modifiers, virtual-key) pairs,
        best first."""
        super().__init__(daemon=True, name="floating-clock-hotkeys")
        self.bindings = bindings
        self.events: queue.Queue[int] = queue.Queue()
        self.ready = threading.Event()
        self.registered: dict[int, tuple[int, int]] = {}

    def label(self, hotkey_id: int) -> str:
        combo = self.registered.get(hotkey_id)
        return hotkey_label(*combo) if combo else ""

    def run(self) -> None:
        try:
            user32.RegisterHotKey.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_uint,
                ctypes.c_uint,
            ]
            user32.RegisterHotKey.restype = wintypes.BOOL
            for hotkey_id, candidates in self.bindings.items():
                for mods, vk in candidates:
                    if user32.RegisterHotKey(None, hotkey_id, mods, vk):
                        self.registered[hotkey_id] = (mods, vk)
                        break
        except (AttributeError, OSError):
            self.ready.set()
            return
        self.ready.set()
        if not self.registered:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.events.put(int(msg.wParam))


def single_instance(name: str) -> bool:
    """True if this is the first instance; False if one is already running.

    The mutex handle is deliberately leaked so it lives for the process.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW(None, True, name)
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except (AttributeError, OSError):
        return True


if WINDOWS:
    _configure_prototypes()
