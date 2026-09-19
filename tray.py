"""Notification-area (system tray) icon.

The clock window is a borderless layered overlay, so it deliberately stays out
of the taskbar and Alt-Tab. That leaves nothing to show the app is running or
to click when you lose track of it -- this is that something.

Built on Shell_NotifyIcon with a message-only window of our own. Tk's event
loop pumps and dispatches every message for the thread, so a window created
here has its WndProc called normally without Tk knowing about it.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WM_APP_TRAY = 0x8000 + 1
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
NIF_SHOWTIP = 0x80
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
SM_CXSMICON, SM_CYSMICON = 49, 50
IDI_APPLICATION = 32512

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", ctypes.c_uint),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


def icon_path() -> str | None:
    """Find the .ico shipped beside the exe, else render one into APPDATA."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "FloatingClock.ico"))
    candidates.append(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "FloatingClock.ico")
    )
    from . import settings as cfg

    generated = os.path.join(os.path.dirname(cfg.settings_path()), "FloatingClock.ico")
    candidates.append(generated)
    for path in candidates:
        if os.path.exists(path):
            return path
    try:
        from .icon import write_ico

        return write_ico(generated)
    except Exception:
        return None


def _configure_prototypes() -> None:
    """Same reason as win32util: undeclared ctypes args marshal as 32-bit and
    blow up on real 64-bit handles and LPARAMs."""
    user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.LoadImageW.restype = wintypes.HICON
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = ctypes.c_uint
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE


_configure_prototypes()


class Tray:
    def __init__(self, tooltip: str = "Floating Clock") -> None:
        self.events: queue.Queue[str] = queue.Queue()
        self.hwnd = None
        self.hicon = None
        self._added = False
        self._tooltip = tooltip
        # Keep hard references: ctypes callbacks and the class name must
        # outlive the registration or the WndProc address becomes garbage.
        self._wndproc = WNDPROC(self._on_message)
        self._class = None
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._install()

    # --- setup -------------------------------------------------------------
    def _install(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        wndclass = WNDCLASS()
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = instance
        wndclass.lpszClassName = "FloatingClockTrayWindow"
        self._class = wndclass
        user32.RegisterClassW(ctypes.byref(wndclass))

        self.hwnd = user32.CreateWindowExW(
            0, "FloatingClockTrayWindow", "Floating Clock", 0,
            0, 0, 0, 0, None, None, instance, None,
        )
        if not self.hwnd:
            return

        path = icon_path()
        if path:
            self.hicon = user32.LoadImageW(
                None, path, IMAGE_ICON,
                user32.GetSystemMetrics(SM_CXSMICON),
                user32.GetSystemMetrics(SM_CYSMICON),
                LR_LOADFROMFILE,
            )
        if not self.hicon:
            user32.LoadIconW.restype = wintypes.HICON
            self.hicon = user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        self._add()

    def _data(self, flags: int) -> NOTIFYICONDATA:
        data = NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_APP_TRAY
        data.hIcon = self.hicon
        data.szTip = self._tooltip[:127]
        return data

    def _add(self) -> None:
        if not self.hwnd:
            return
        data = self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        self._added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))

    def set_tooltip(self, text: str) -> None:
        text = (text or "Floating Clock")[:127]
        if text == self._tooltip or not self._added:
            self._tooltip = text
            return
        self._tooltip = text
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._data(NIF_TIP | NIF_SHOWTIP)))

    # --- messages ----------------------------------------------------------
    def _on_message(self, hwnd, message, wparam, lparam):
        if message == WM_APP_TRAY:
            event = lparam & 0xFFFF
            if event == WM_LBUTTONUP:
                self.events.put("left")
            elif event == WM_RBUTTONUP:
                self.events.put("right")
            elif event == WM_LBUTTONDBLCLK:
                self.events.put("double")
            return 0
        if message == self._taskbar_created:
            # Explorer restarted and every tray icon was wiped; put ours back.
            self._add()
            return 0
        if message == WM_DESTROY:
            self.remove()
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def remove(self) -> None:
        if self._added and self.hwnd:
            data = NOTIFYICONDATA()
            data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            data.hWnd = self.hwnd
            data.uID = 1
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            self._added = False

    def destroy(self) -> None:
        self.remove()
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
