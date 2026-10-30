from __future__ import annotations

from typing import Any
import time
from copy import copy
from threading import RLock

import can
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from signal_explorer.bus.backends import BackendSpec
from signal_explorer.bus.discovery import prepare_vector_library
from signal_explorer.bus.canfd import fd_configuration, validate_frame
from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.bus.demo import demo_frames, demo_reply
from signal_explorer.bus.timestamps import HardwareClock


class _Reader(QThread):
    message = Signal(object)
    failed = Signal(str)

    def __init__(self, bus: can.BusABC, capture=None) -> None:
        super().__init__()
        self._bus = bus
        self._capture = capture

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                msg = self._bus.recv(timeout=0.1)
                if msg is not None and self._capture is not None:
                    captured = self._capture(msg)
                    if captured is not None:
                        msg = captured
            except Exception as exc:
                self.failed.emit(f"Receive failed ({type(exc).__name__}): {exc}")
                return
            if msg is not None:
                self.message.emit(msg)


class BusService(QObject):
    """Owns the python-can bus, RX thread, and optional virtual demo traffic."""

    connected = Signal(str)
    disconnected = Signal()
    message = Signal(object)
    error = Signal(str)
    traffic = Signal(object)  # Thread-safe capture before display queuing/filtering.
    about_to_disconnect = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus: can.BusABC | None = None
        self._device_lease = None
        self._send_lock = RLock()
        self.session_info = {}
        self._channel = None
        self.fd_enabled = False
        self._rx_clock = None
        self._demo_bus: can.BusABC | None = None
        self._reader: _Reader | None = None
        self._demo_timer = QTimer(self)
        self._demo_timer.setInterval(100)
        self._demo_timer.timeout.connect(self._send_demo_frame)
        self._demo_counter = 0

    @property
    def is_connected(self) -> bool:
        return self._bus is not None

    def connect_bus(
        self,
        backend: BackendSpec,
        channel: str,
        bitrate: int,
        inject_demo: bool,
        device_config: dict | None = None,
        fd: bool = False,
        data_bitrate: int = 2000000,
    ) -> None:
        if self._bus is not None:
            self.disconnect_bus()

        kwargs: dict[str, Any] = {
            "interface": backend.interface,
            "channel": _typed_channel(backend.interface, channel),
        }
        kwargs.update(backend.extra_kwargs)
        if device_config:
            for key in ("channel", "serial", "channel_index", "app_name"):
                if key in device_config:
                    kwargs[key] = device_config[key]
        kwargs["receive_own_messages"] = False
        kwargs["ignore_config"] = True
        if backend.interface == "vector":
            prepare_vector_library()
        if backend.interface not in {"virtual"}:
            kwargs["bitrate"] = bitrate
        if backend.interface == "virtual":
            kwargs["receive_own_messages"] = False
        if backend.interface == "slcan":
            kwargs.setdefault("tty_baudrate", 115200)

        try:
            if fd:
                if device_config and device_config.get("supports_fd") is False:
                    raise ValueError("The selected channel does not support CAN FD.")
                kwargs.update(fd_configuration(backend.interface, bitrate, data_bitrate))
            if backend.interface in {'vector', 'neovi'}:
                self._device_lease = DeviceLease(backend.interface, kwargs.get('serial'), 'CAN')
            self._bus = can.Bus(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surface any vendor/driver failure in the UI
            self._bus = None
            if self._device_lease:
                self._device_lease.release()
                self._device_lease = None
            self.error.emit(f"Connect failed ({backend.label}): {exc}")
            return

        self.fd_enabled = fd
        self._rx_clock = HardwareClock() if backend.interface == 'neovi' else None
        self._channel = kwargs["channel"]
        self.session_info = dict(adapter=backend.label, interface=backend.interface,
                                 native_channel=str(self._channel), bitrate=bitrate, fd=fd,
                                 data_bitrate=data_bitrate if fd else None,
                                 serial=(device_config or {}).get("serial"))
        if self._rx_clock is not None:
            self.session_info['timestamp_source'] = 'Hardware intervals anchored to host time at first RX'
        self._reader = _Reader(self._bus, self._capture_received)
        self._reader.message.connect(self._on_received_message)
        self._reader.failed.connect(self._on_reader_failed)
        self._reader.start()

        if inject_demo and backend.interface == "virtual":
            try:
                self._demo_bus = can.Bus(
                    interface="virtual",
                    channel=kwargs["channel"],
                    protocol=self._bus.protocol,
                    ignore_config=True,
                )
                self._demo_counter = 0
                self._demo_timer.start()
            except Exception as exc:  # noqa: BLE001
                self.error.emit(f"Demo injector failed: {exc}")

        self.connected.emit(f"{backend.label} · {channel}")

    def _on_received_message(self, msg):
        if msg.channel is None:
            msg = copy(msg)
            msg.channel = self._channel
        self.message.emit(msg)

    def _capture_received(self, msg):
        captured = copy(msg)
        if self._rx_clock is not None:
            captured.timestamp = self._rx_clock.to_unix(msg.timestamp)
        if captured.channel is None:
            captured.channel = self._channel
        self.traffic.emit(captured)
        return captured

    def send_message(self, msg: can.Message, report_errors=True) -> bool:
        with self._send_lock:
            try:
                if self._bus is None:
                    raise RuntimeError("Connect before sending a message.")
                validate_frame(msg, self.fd_enabled)
                self._bus.send(msg, timeout=0.2)
            except Exception as exc:
                if report_errors:
                    self.error.emit(f"Send failed: {exc}")
                return False
            echo = copy(msg)
            echo.timestamp = time.time()
            echo.is_rx = False
            echo.channel = self._channel
            self.traffic.emit(echo)
        self.message.emit(echo)
        return True

    def disconnect_bus(self) -> None:
        if self._bus is not None:
            self.about_to_disconnect.emit()
        self.fd_enabled = False
        self._demo_timer.stop()
        if self._demo_bus is not None:
            try:
                self._demo_bus.shutdown()
            except Exception:
                pass
            self._demo_bus = None

        if self._reader is not None:
            self._reader.requestInterruption()
            self._reader.wait(1000)
            self._reader = None
        self._rx_clock = None

        with self._send_lock:
            if self._bus is not None:
                try:
                    self._bus.shutdown()
                except Exception:
                    pass
                self._bus = None

        if self._device_lease:
            self._device_lease.release()
            self._device_lease = None

        self.disconnected.emit()

    def _on_reader_failed(self, text: str) -> None:
        self.error.emit(text)
        self.disconnect_bus()

    def _send_demo_frame(self) -> None:
        if self._demo_bus is None:
            return
        self._demo_counter += 1
        try:
            # Consume actual virtual-bus requests, rather than manufacturing RX
            # on the tester's send path. Bound work to keep the UI responsive.
            for _ in range(100):
                request = self._demo_bus.recv(timeout=0)
                if request is None:
                    break
                reply = demo_reply(request)
                if reply is not None:
                    self._demo_bus.send(reply)
            for msg in demo_frames(self._demo_counter, self.fd_enabled):
                self._demo_bus.send(msg)
        except can.CanError as exc:
            self.error.emit(f"Demo send failed: {exc}")
            self._demo_timer.stop()


def _typed_channel(interface: str, channel: str) -> str | int:
    if interface in {"slcan", "virtual"}:
        return channel
    try:
        return int(channel)
    except ValueError:
        return channel
