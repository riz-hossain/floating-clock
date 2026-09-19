"""SRV and MX lookups through Windows' own resolver.

Python's standard library can resolve host names but not record types, and
pulling in a DNS library for two queries is more than this needs. DnsQuery_W
in dnsapi.dll answers both, honouring the machine's DNS settings and cache.
"""

from __future__ import annotations

import ctypes
import logging
import sys

if sys.platform == "win32":
    import ctypes.wintypes as wt
else:   # the structures below are only ever built on Windows
    class wt:  # noqa: N801
        LPWSTR = LPCWSTR = ctypes.c_wchar_p
        WORD = ctypes.c_uint16
        DWORD = ctypes.c_uint32

log = logging.getLogger(__name__)

DNS_TYPE_MX = 15
DNS_TYPE_SRV = 33
DNS_QUERY_STANDARD = 0
DNS_FREE_RECORD_LIST = 1


class _SrvData(ctypes.Structure):
    _fields_ = [
        ("pNameTarget", wt.LPWSTR), ("wPriority", wt.WORD),
        ("wWeight", wt.WORD), ("wPort", wt.WORD), ("Pad", wt.WORD),
    ]


class _MxData(ctypes.Structure):
    _fields_ = [("pNameExchange", wt.LPWSTR), ("wPreference", wt.WORD), ("Pad", wt.WORD)]


class _Data(ctypes.Union):
    _fields_ = [("SRV", _SrvData), ("MX", _MxData), ("_pad", ctypes.c_byte * 64)]


class _Record(ctypes.Structure):
    pass


_Record._fields_ = [
    ("pNext", ctypes.POINTER(_Record)), ("pName", wt.LPWSTR), ("wType", wt.WORD),
    ("wDataLength", wt.WORD), ("Flags", wt.DWORD), ("dwTtl", wt.DWORD),
    ("dwReserved", wt.DWORD), ("Data", _Data),
]


def _query(name: str, record_type: int) -> list:
    """Raw records of one type for `name`, or [] when there are none."""
    try:
        dnsapi = ctypes.windll.dnsapi
    except (AttributeError, OSError):
        return []
    dnsapi.DnsQuery_W.argtypes = [
        wt.LPCWSTR, wt.WORD, wt.DWORD, ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(_Record)), ctypes.c_void_p,
    ]
    dnsapi.DnsQuery_W.restype = ctypes.c_long
    dnsapi.DnsRecordListFree.argtypes = [ctypes.POINTER(_Record), ctypes.c_int]
    dnsapi.DnsRecordListFree.restype = None

    head = ctypes.POINTER(_Record)()
    status = dnsapi.DnsQuery_W(name, record_type, DNS_QUERY_STANDARD, None, ctypes.byref(head), None)
    if status != 0 or not head:
        return []
    found = []
    try:
        node = head
        while node:
            record = node.contents
            if record.wType == record_type:
                if record_type == DNS_TYPE_SRV:
                    data = record.Data.SRV
                    found.append((data.wPriority, -data.wWeight, data.pNameTarget or "", data.wPort))
                elif record_type == DNS_TYPE_MX:
                    data = record.Data.MX
                    found.append((data.wPreference, data.pNameExchange or ""))
            node = record.pNext
    finally:
        dnsapi.DnsRecordListFree(head, DNS_FREE_RECORD_LIST)
    return found


def _resolver():
    """dnspython, for the platforms without dnsapi.dll. None if absent."""
    if sys.platform == "win32":
        return None
    try:
        import dns.resolver
        return dns.resolver
    except ImportError:
        return None


def mx(domain: str) -> list[str]:
    """Mail exchangers for a domain, most preferred first, lower-cased."""
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        return []
    resolver = _resolver()
    if resolver is not None:
        try:
            answers = resolver.resolve(domain, "MX")
            return [str(r.exchange).lower().rstrip(".")
                    for r in sorted(answers, key=lambda r: r.preference)]
        except Exception:
            return []
    try:
        records = _query(domain, DNS_TYPE_MX)
    except Exception:
        log.debug("MX lookup failed for %s", domain, exc_info=True)
        return []
    return [host.lower().rstrip(".") for _pref, host in sorted(records) if host]


def srv(service: str, domain: str) -> list[tuple[str, int]]:
    """(host, port) for a service such as '_caldavs._tcp', best first."""
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        return []
    resolver = _resolver()
    if resolver is not None:
        try:
            answers = resolver.resolve("%s.%s" % (service, domain), "SRV")
            return [(str(r.target).lower().rstrip("."), int(r.port))
                    for r in sorted(answers, key=lambda r: (r.priority, -r.weight))
                    if str(r.target) != "."]
        except Exception:
            return []
    try:
        records = _query("%s.%s" % (service, domain), DNS_TYPE_SRV)
    except Exception:
        log.debug("SRV lookup failed for %s.%s", service, domain, exc_info=True)
        return []
    return [(host.lower().rstrip("."), int(port))
            for _prio, _weight, host, port in sorted(records) if host and host != "."]
