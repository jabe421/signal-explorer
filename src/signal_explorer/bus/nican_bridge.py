"""Classic CAN bus backed by the separate x86 NI-CAN process."""

from collections import deque
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Lock, Thread

import can

from signal_explorer.bus.discovery import ChannelOption


def helper_command():
    if sys.platform != "win32":
        raise can.CanInitializationError("NI-CAN requires Windows.")
    if getattr(sys, "frozen", False):
        helper = Path(sys.executable).parent / "nican" / "SignalExplorer-NICAN.exe"
        if not helper.is_file():
            raise can.CanInitializationError("The 32-bit NI-CAN helper is missing. Reinstall Signal Explorer.")
        return [str(helper)]
    worker = Path(__file__).with_name("nican_worker.py")
    python = os.environ.get("SIGNAL_EXPLORER_NICAN_PYTHON")
    return [python, str(worker)] if python else ["py", "-3.12-32", str(worker)]


class Bridge:
    def __init__(self):
        command = helper_command()
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise can.CanInitializationError(f"Could not start the 32-bit NI-CAN helper: {exc}") from exc
        self._lock = Lock()
        self._responses = Queue()
        self._reader = Thread(target=self._read, name="ni-can-helper-output", daemon=True)
        self._reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self._responses.put(json.loads(line))
        except (OSError, ValueError) as exc:
            self._responses.put({"error": f"Invalid NI-CAN helper response: {exc}"})
        finally:
            self._responses.put(None)

    def request(self, operation, *, deadline=5, **fields):
        with self._lock:
            return self._request(operation, deadline, **fields)

    def _request(self, operation, deadline, **fields):
        try:
            self.process.stdin.write(json.dumps(dict(operation=operation, **fields)) + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            # Startup errors are reported on stdout before the worker exits.
            if not isinstance(exc, BrokenPipeError):
                raise can.CanOperationError("Could not write to the NI-CAN helper.") from exc
        try:
            response = self._responses.get(timeout=deadline)
        except Empty as exc:
            if self.process.poll() is None:
                self.process.kill()
            raise can.CanOperationError(f"NI-CAN helper timed out during {operation}.") from exc
        if response is None:
            raise can.CanOperationError("The NI-CAN helper exited. Check the x86 runtime and NI-CAN driver.")
        if "error" in response:
            raise can.CanOperationError(response["error"])
        return response["result"]

    def close(self):
        with self._lock:
            if self.process.stdin.closed:
                return
            if self.process.poll() is None:
                try:
                    self._request("close", 1)
                except can.CanError:
                    pass
            self.process.stdin.close()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
            self._reader.join(timeout=1)
            self.process.stdout.close()


def discover_nican_channels():
    bridge = Bridge()
    try:
        channels = bridge.request("scan")
    finally:
        bridge.close()
    options = [
        ChannelOption(f"NI {info['device_model']} | serial {info['device_serial']} | {info['channel']}", info)
        for info in channels
    ]
    return options, (f"{len(options)} NI-CAN USB channels detected." if options else
                     "No high-speed NI-CAN USB interfaces detected. Check NI MAX.")


class NiCanBus(can.BusABC):
    def __init__(self, channel, bitrate, serial=None, **kwargs):
        self.channel = channel
        self.channel_info = f"NI-CAN: {channel}"
        self._can_protocol = can.CanProtocol.CAN_20
        self._frames = deque()
        self._is_shutdown = True
        self._bridge = Bridge()
        try:
            self._bridge.request("open", channel=channel, bitrate=bitrate, serial=serial)
            super().__init__(channel=channel, **kwargs)
        except Exception:
            self._bridge.close()
            raise

    def _recv_internal(self, timeout):
        if not self._frames:
            self._frames.extend(self._bridge.request(
                "receive", deadline=1, timeout=0.01 if timeout is None else timeout,
            ))
        if not self._frames:
            return None, False
        return can.Message(channel=self.channel, **self._frames.popleft()), False

    def send(self, message, timeout=None):
        if message.is_fd or message.is_error_frame or message.dlc > 8:
            raise can.CanOperationError("NI-CAN sends Classic CAN data and remote frames only.")
        self._bridge.request(
            "send", deadline=1,
            frame=dict(arbitration_id=message.arbitration_id, is_extended_id=message.is_extended_id,
                       is_remote_frame=message.is_remote_frame, dlc=message.dlc, data=list(message.data)),
        )

    def shutdown(self):
        if not self._is_shutdown:
            super().shutdown()
            self._bridge.close()
