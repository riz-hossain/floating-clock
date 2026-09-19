"""Passwords in Windows Credential Manager, never in settings.json.

An app password for a calendar is a real credential: it reads the whole
calendar. The settings file is plain JSON that gets copied around, so the
secret goes into the Credential Manager under this user's account instead,
where it is encrypted and only readable by them. Each calendar gets one
entry, named after the calendar's id.
"""

from __future__ import annotations

import ctypes
import json
import logging
import sys

if sys.platform == "win32":
    import ctypes.wintypes as wt

log = logging.getLogger(__name__)

PREFIX = "FloatingClock/"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class _Credential(ctypes.Structure if sys.platform == "win32" else object):
    _fields_ = [] if sys.platform != "win32" else [
        ("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wt.LPWSTR), ("UserName", wt.LPWSTR),
    ]


def _advapi():
    lib = ctypes.windll.advapi32
    lib.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wt.DWORD]
    lib.CredWriteW.restype = wt.BOOL
    lib.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                              ctypes.POINTER(ctypes.POINTER(_Credential))]
    lib.CredReadW.restype = wt.BOOL
    lib.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
    lib.CredDeleteW.restype = wt.BOOL
    lib.CredFree.argtypes = [ctypes.c_void_p]
    lib.CredFree.restype = None
    return lib


def _target(key: str) -> str:
    return PREFIX + key


def _keyring():
    """Off Windows: the platform keychain through the keyring package
    (macOS Keychain, the Linux secret service). None when unavailable."""
    if sys.platform == "win32":
        return None
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def store(key: str, username: str, secret: str) -> bool:
    """Save (username, secret) under `key`. Returns False if Windows refused."""
    ring = _keyring()
    if ring is not None:
        try:
            ring.set_password(PREFIX.rstrip("/"), key, json.dumps([username, secret]))
            return True
        except Exception:
            log.warning("The keychain refused to store %s", key, exc_info=True)
            return False
    try:
        lib = _advapi()
        blob = secret.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(blob, len(blob))
        cred = _Credential()
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = _target(key)
        cred.Comment = "Floating Clock calendar sign-in"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = username
        if not lib.CredWriteW(ctypes.byref(cred), 0):
            log.warning("Credential Manager refused to store %s (error %d)",
                        key, ctypes.get_last_error())
            return False
        return True
    except Exception:
        log.warning("Could not store the credential for %s", key, exc_info=True)
        return False


def read(key: str) -> tuple[str, str] | None:
    """(username, secret) for `key`, or None when nothing is stored."""
    ring = _keyring()
    if ring is not None:
        try:
            raw = ring.get_password(PREFIX.rstrip("/"), key)
            if not raw:
                return None
            username, secret = json.loads(raw)
            return str(username), str(secret)
        except Exception:
            log.warning("Could not read %s from the keychain", key, exc_info=True)
            return None
    try:
        lib = _advapi()
        found = ctypes.POINTER(_Credential)()
        if not lib.CredReadW(_target(key), CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
            return None
        try:
            cred = found.contents
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return (cred.UserName or "", raw.decode("utf-16-le", "replace"))
        finally:
            lib.CredFree(found)
    except Exception:
        log.warning("Could not read the credential for %s", key, exc_info=True)
        return None


def delete(key: str) -> None:
    ring = _keyring()
    if ring is not None:
        try:
            ring.delete_password(PREFIX.rstrip("/"), key)
        except Exception:
            log.debug("Could not delete %s from the keychain", key, exc_info=True)
        return
    try:
        _advapi().CredDeleteW(_target(key), CRED_TYPE_GENERIC, 0)
    except Exception:
        log.debug("Could not delete the credential for %s", key, exc_info=True)
