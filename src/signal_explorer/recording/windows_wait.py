"""Private, interruptible high-resolution Windows wait; no global timer changes."""

import ctypes
from threading import Lock
import time


class WindowsReplayWait:
    def __init__(self):
        self._lock = Lock()
        self.timer = self.event = None
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateWaitableTimerExW": (
                [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32],
                ctypes.c_void_p,
            ),
            "CreateEventW": (
                [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p],
                ctypes.c_void_p,
            ),
            "SetWaitableTimerEx": (
                [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_int64),
                    ctypes.c_int32,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                ],
                ctypes.c_int,
            ),
            "WaitForMultipleObjects": (
                [
                    ctypes.c_uint32,
                    ctypes.POINTER(ctypes.c_void_p),
                    ctypes.c_int,
                    ctypes.c_uint32,
                ],
                ctypes.c_uint32,
            ),
            "SetEvent": ([ctypes.c_void_p], ctypes.c_int),
            "CancelWaitableTimer": ([ctypes.c_void_p], ctypes.c_int),
            "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        try:
            self.timer = self.api.CreateWaitableTimerExW(None, None, 0x2, 0x1F0003)
            if not self.timer:
                raise ctypes.WinError(ctypes.get_last_error())
            self.event = self.api.CreateEventW(None, False, False, None)
            if not self.event:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def wait_until(self, deadline):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        due = ctypes.c_int64(-max(1, int(remaining * 10_000_000)))
        if not self.api.SetWaitableTimerEx(
            self.timer, ctypes.byref(due), 0, None, None, None, 0
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        # State-change event first: pause/stop takes precedence if both signal.
        handles = (ctypes.c_void_p * 2)(self.event, self.timer)
        result = self.api.WaitForMultipleObjects(2, handles, False, 0xFFFFFFFF)
        if result not in (0, 1):
            raise OSError(f"Windows replay wait failed ({result:#x}).")

    def wake(self):
        with self._lock:
            if self.event and not self.api.SetEvent(self.event):
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        with self._lock:
            if self.timer:
                self.api.CancelWaitableTimer(self.timer)
                self.api.CloseHandle(self.timer)
                self.timer = None
            if self.event:
                self.api.CloseHandle(self.event)
                self.event = None
