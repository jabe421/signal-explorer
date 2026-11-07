"""Bounded application logs and a support report without capturing bus payloads."""

from collections import deque
from dataclasses import asdict, is_dataclass
from importlib import metadata
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import sys
import struct
import tempfile
import threading

LOGGER = logging.getLogger("signal_explorer")
_recent = deque(maxlen=50)
_recent_lock = threading.Lock()
_log_path = None
_configured = False
_hooks_installed = False


class _RecentErrors(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            with _recent_lock:
                _recent.append(self.format(record))


def configure_logging(directory=None):
    """Idempotent startup configuration; failure to create a log is nonfatal."""
    global _log_path, _configured
    if _configured:
        return _log_path
    _configured = True
    target = Path(
        directory
        or Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir()))
        / "Signal Explorer"
        / "logs"
    )
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"
    )
    LOGGER.setLevel(logging.INFO)
    recent = _RecentErrors()
    recent.setFormatter(formatter)
    LOGGER.addHandler(recent)
    for folder in (target, Path(tempfile.gettempdir()) / "signal-explorer-logs"):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "signal-explorer.log"
            handler = RotatingFileHandler(
                path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(formatter)
            LOGGER.addHandler(handler)
            _log_path = path
            break
        except OSError:
            LOGGER.warning("Cannot create diagnostic log in %s", folder, exc_info=True)
    LOGGER.info(
        "Signal Explorer startup; Python %s; %s",
        platform.python_version(),
        platform.platform(),
    )
    return _log_path


def install_exception_hooks():
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    previous = sys.excepthook

    def report(kind, value, traceback):
        LOGGER.critical("Unhandled exception", exc_info=(kind, value, traceback))
        previous(kind, value, traceback)

    sys.excepthook = report
    previous_thread = threading.excepthook

    def report_thread(args):
        LOGGER.critical(
            "Unhandled worker exception in %s",
            args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        previous_thread(args)

    threading.excepthook = report_thread


def installed_build_info():
    if not getattr(sys, "frozen", False):
        return {"source_checkout": True}
    path = Path(sys.executable).resolve().parent / "build-info.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        return {
            key: info.get(key)
            for key in (
                "version",
                "application_version",
                "commit",
                "built_utc",
                "dirty",
            )
        }
    except (OSError, ValueError, AttributeError) as exc:
        return {"unavailable": str(exc)}


def _windows_file_version(path):
    import ctypes

    api = ctypes.WinDLL("version", use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint),
    ]
    api.GetFileVersionInfoSizeW.restype = ctypes.c_uint
    api.GetFileVersionInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    api.GetFileVersionInfoW.restype = ctypes.c_int
    api.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint),
    ]
    api.VerQueryValueW.restype = ctypes.c_int
    size = api.GetFileVersionInfoSizeW(path, None)
    if not size:
        return "unavailable"
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(path, 0, size, data):
        return "unavailable"
    pointer, length = ctypes.c_void_p(), ctypes.c_uint()
    if (
        not api.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length))
        or length.value < 52
    ):
        return "unavailable"
    values = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
    if values[0] != 0xFEEF04BD:
        return "unavailable"
    return ".".join(
        str(n)
        for n in (
            values[2] >> 16,
            values[2] & 0xFFFF,
            values[3] >> 16,
            values[3] & 0xFFFF,
        )
    )


def loaded_driver_libraries():
    """Inspect already loaded DLLs; never load a driver just to build a report."""
    if sys.platform != "win32":
        return {}
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint
    result = {}
    for name in (
        "vxlapi64.dll",
        "icsneo40.dll",
        "icsneo.dll",
        "canlib32.dll",
        "PCANBasic.dll",
        "PLinApi.dll",
        "nixnet.dll",
        "wpcap.dll",
        "Packet.dll",
    ):
        handle = kernel.GetModuleHandleW(name)
        result[name] = {"status": "not loaded (not an installation check)"}
        if handle:
            buffer = ctypes.create_unicode_buffer(32768)
            if kernel.GetModuleFileNameW(handle, buffer, len(buffer)):
                try:
                    file_version = _windows_file_version(buffer.value)
                except (OSError, AttributeError) as exc:
                    file_version = f"unavailable: {exc}"
                result[name] = dict(path=buffer.value, file_version=file_version)
    return result


def diagnostics_report(context=None):
    versions = {}
    for name in (
        "signal-explorer",
        "PySide6-Essentials",
        "python-can",
        "asammdf",
        "cantools",
        "python-ics",
        "nixnet",
        "pyserial",
        "gs-usb",
        "pyusb",
    ):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not installed"
    with _recent_lock:
        errors = list(_recent)

    def serialize(value):
        return (
            asdict(value)
            if is_dataclass(value) and not isinstance(value, type)
            else str(value)
        )

    return json.dumps(
        dict(
            python=sys.version,
            python_bits=struct.calcsize("P") * 8,
            executable=sys.executable,
            platform=platform.platform(),
            architecture=platform.machine(),
            frozen=bool(getattr(sys, "frozen", False)),
            versions=versions,
            build=installed_build_info(),
            log_file=str(_log_path) if _log_path else "logging not configured",
            loaded_driver_libraries=loaded_driver_libraries(),
            context=context or {},
            recent_errors=errors,
        ),
        indent=2,
        default=serialize,
    )
