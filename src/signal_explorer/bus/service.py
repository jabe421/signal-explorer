from __future__ import annotations

from typing import Any
import time
from copy import copy

import can
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from signal_explorer.bus.backends import BackendSpec
from signal_explorer.bus.discovery import prepare_vector_library


class _Reader(QThread):
    message = Signal(object)
    failed = Signal(str)

    def __init__(self, bus: can.BusABC) -> None:
        super().__init__()
        self._bus = bus

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                msg = self._bus.recv(timeout=0.1)
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

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus: can.BusABC | None = None
        self._channel = None
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
            self._bus = can.Bus(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surface any vendor/driver failure in the UI
            self._bus = None
            self.error.emit(f"Connect failed ({backend.label}): {exc}")
            return

        self._channel = kwargs["channel"]
        self._reader = _Reader(self._bus)
        self._reader.message.connect(self._on_received_message)
        self._reader.failed.connect(self._on_reader_failed)
        self._reader.start()

        if inject_demo and backend.interface == "virtual":
            try:
                self._demo_bus = can.Bus(
                    interface="virtual",
                    channel=kwargs["channel"],
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

    def send_message(self, msg: can.Message) -> bool:
        if self._bus is None:
            self.error.emit("Connect before sending a message.")
            return False
        try:
            self._bus.send(msg, timeout=0.2)
        except Exception as exc:
            self.error.emit(f"Send failed: {exc}")
            return False
        echo = copy(msg)
        echo.timestamp = time.time()
        echo.is_rx = False
        echo.channel = self._channel
        self.message.emit(echo)
        return True

    def disconnect_bus(self) -> None:
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

        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None

        self.disconnected.emit()

    def _on_reader_failed(self, text: str) -> None:
        self.error.emit(text)
        self.disconnect_bus()

    def _send_demo_frame(self) -> None:
        if self._demo_bus is None:
            return
        self._demo_counter = (self._demo_counter + 1) & 0xFF
        msg = can.Message(
            arbitration_id=0x100,
            data=[self._demo_counter, 0x01, 0x02, 0x03, 0xDE, 0xAD, 0xBE, 0xEF],
            is_extended_id=False,
        )
        try:
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
