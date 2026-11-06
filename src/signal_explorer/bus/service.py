from __future__ import annotations

from typing import Any
import math
from copy import copy
from threading import RLock

import can
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from signal_explorer.bus.backends import BackendSpec
from signal_explorer.bus.discovery import prepare_vector_library
from signal_explorer.bus.canfd import fd_configuration, validate_frame
from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.bus.demo import demo_frames, demo_reply
from signal_explorer.bus.timestamps import HardwareClock, SessionClock
from signal_explorer.bus.multichannel import native_channel, open_channels
from signal_explorer.bus.display_queue import DisplayQueue
from signal_explorer.bus.frames import snapshot_message
from signal_explorer.bus.listen_only import open_bus
from signal_explorer.bus.timing import TimingSettings, SILENT_INTERFACES

TX_REPORT_INTERFACES = frozenset({'vector', 'neovi', 'kvaser', 'pcan', 'nixnet'})


class _Reader(QThread):
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
                    self._capture(msg)
            except Exception as exc:
                self.failed.emit(f"Receive failed ({type(exc).__name__}): {exc}")
                return


class BusService(QObject):
    """Owns the python-can bus, RX thread, and optional virtual demo traffic."""

    connected = Signal(str)
    disconnected = Signal()
    message = Signal(object)
    messages = Signal(object)  # UI-thread batches; frames are owned, read-only snapshots.
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
        self.channels = []
        self.tx_channel = None
        self.fd_enabled = False
        self._rx_clock = None
        self._clock = SessionClock()
        self._interface = None
        self.driver_tx_reports = False
        self.listen_only = False
        self._demo_bus: can.BusABC | None = None
        self._reader: _Reader | None = None
        self._demo_timer = QTimer(self)
        self._demo_timer.setInterval(100)
        self._demo_timer.timeout.connect(self._send_demo_frame)
        self._demo_counter = 0
        self._display = DisplayQueue()
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(40)
        self._display_timer.timeout.connect(self._flush_display)

    @property
    def is_connected(self) -> bool:
        return self._bus is not None

    @property
    def can_send(self):
        return self.is_connected and not self.listen_only

    @property
    def can_replay(self):
        return self.can_send

    def connect_bus(
        self,
        backend: BackendSpec,
        channel: str,
        bitrate: int,
        inject_demo: bool,
        device_config: dict | None = None,
        fd: bool = False,
        data_bitrate: int = 2000000,
        driver_tx_reports: bool = False,
        listen_only: bool = False,
        timing_settings: TimingSettings | None = None,
    ) -> None:
        if self._bus is not None:
            self.disconnect_bus()

        kwargs: dict[str, Any] = {
            "interface": backend.interface,
            "channel": _typed_channel(backend.interface, channel),
        }
        kwargs.update(backend.extra_kwargs)
        configs = (device_config or {}).get('channels') or [dict(device_config or {}, channel=(device_config or {}).get('channel', channel))]
        if device_config:
            for key in ("channel", "serial", "channel_index", "app_name"):
                if key in device_config:
                    kwargs[key] = device_config[key]
        if driver_tx_reports and backend.interface not in TX_REPORT_INTERFACES:
            self.error.emit('Driver TX reports are not supported by this backend. Disable that option.')
            return
        kwargs["receive_own_messages"] = driver_tx_reports
        if driver_tx_reports and backend.interface == 'kvaser':
            kwargs['single_handle'] = False
        if backend.interface == "nixnet":
            kwargs["can_termination"] = bool((device_config or {}).get("can_termination", False))
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
            if listen_only and backend.interface not in SILENT_INTERFACES:
                raise ValueError('Listen-only is unavailable for this backend; no active-mode fallback is allowed.')
            request = timing_settings or TimingSettings(bitrate=bitrate, data_bitrate=data_bitrate)
            if request.bitrate != bitrate or (fd and request.data_bitrate != data_bitrate):
                raise ValueError('Timing rates must match the connection rates.')
            custom_timing = request.build(backend.interface, fd)
            primary_channel = native_channel(backend.interface, kwargs['channel'])
            for cfg in configs:
                if cfg.get('can_termination') and cfg.get('can_term_available') is False:
                    raise ValueError('An enabled NI channel has no internal termination.')
                if fd and cfg.get('supports_fd') is False:
                    raise ValueError('An enabled channel does not support CAN FD. Disable that channel or use Classic CAN.')
            if (backend.interface == "nixnet" and kwargs["can_termination"]
                    and (device_config or {}).get("can_term_available") is False):
                raise ValueError("The selected NI channel has no internal termination. Use external termination.")
            if fd:
                if device_config and device_config.get("supports_fd") is False:
                    raise ValueError("The selected channel does not support CAN FD.")
                kwargs.update(fd_configuration(backend.interface, bitrate, data_bitrate))
            if custom_timing is not None:
                kwargs['timing'] = custom_timing
            if backend.interface in {'vector', 'neovi'}:
                self._device_lease = DeviceLease(backend.interface, kwargs.get('serial'), 'CAN')
            factory = lambda **args: open_bus(monitor_only=listen_only, **args)
            self._bus = (open_channels(factory, backend.interface, configs, kwargs)
                         if len(configs) > 1 else factory(**kwargs))
        except Exception as exc:  # noqa: BLE001 — surface any vendor/driver failure in the UI
            self._bus = None
            if self._device_lease:
                self._device_lease.release()
                self._device_lease = None
            hint = (' Driver TX reports require compatible drivers. Disable that option to use local TX estimates.'
                    if driver_tx_reports else '') + (' Check the nixnet Python extra, the 64-bit NI-XNET driver, '
                    'and the exact CAN interface name in NI MAX; close other bus sessions.'
                    if backend.interface == 'nixnet' else '')
            self.error.emit(f"Connect failed ({backend.label}): {exc}{hint}")
            return

        self.fd_enabled = fd
        self._display.reset()
        self._clock = SessionClock()
        self._interface = backend.interface
        self.driver_tx_reports = driver_tx_reports
        self.listen_only = listen_only
        self._rx_clock = HardwareClock(self._clock.now) if backend.interface == 'neovi' else None
        self._channel = primary_channel
        self.channels = [dict(native_channel=native_channel(backend.interface, cfg['channel']),
                              label=cfg.get('channel_label', str(cfg['channel'])),
                              bus_channel=(int(cfg['channel']) + 1 if backend.interface == 'vector' else
                                           int(cfg['channel_label'][3:]) if cfg.get('channel_label', '').startswith('CAN')
                                           and cfg['channel_label'][3:].isdigit() else index + 1))
                         for index, cfg in enumerate(configs)]
        self.tx_channel = self._channel
        self.session_info = dict(adapter=backend.label, interface=backend.interface,
                                 native_channel=str(self._channel), bitrate=bitrate, fd=fd,
                                 data_bitrate=data_bitrate if fd else None,
                                 serial=(device_config or {}).get("serial"))
        self.session_info['channels'] = self.channels
        self.session_info.update(listen_only=listen_only, timing_request=vars(request),
                                 calculated_timing=str(custom_timing) if custom_timing is not None else 'Driver/default timing')
        self.session_info.update(
            driver_tx_reports=driver_tx_reports,
            host_clock='Unix epoch anchored once to monotonic time for this connection',
            tx_semantics=('Driver TX reports only; no synthetic TX echo; not intended-ECU delivery proof'
                          if driver_tx_reports else 'Local send accepted; host estimate, not a bus acknowledgement'),
            rx_timestamp_source=('Shared device clock anchored at first RX/TX event' if self._rx_clock else
                                 'Host receipt estimate' if backend.interface in {'virtual', 'slcan'} else
                                 'Driver epoch timestamp, calibrated by python-can'))
        if backend.interface == "nixnet":
            self.session_info["can_termination"] = kwargs["can_termination"]
            self.session_info["serial"] = (device_config or {}).get("device_serial")
            for key in ("device_model", "physical_port"):
                self.session_info[key] = (device_config or {}).get(key)
        if self._rx_clock is not None:
            self.session_info['timestamp_source'] = 'Hardware RX/TX intervals anchored to monotonic host epoch at first event'
        self._reader = _Reader(self._bus, self._capture_received)
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

        self.connected.emit(f"{backend.label} · " + ', '.join(c['label'] for c in self.channels)
                            + (' · LISTEN-ONLY (TX blocked)' if listen_only else ' · Normal')
                            + (' · TX: driver reports' if driver_tx_reports else ' · TX: local estimates'))
        self._display_timer.start()

    def select_tx_channel(self, channel):
        selected = next((c for c in self.channels if str(channel) in (str(c['native_channel']), c['label'])), None)
        if selected is None:
            raise ValueError('TX channel is not enabled.')
        self.tx_channel = selected['native_channel']

    @property
    def ui_dropped(self):
        return self._display.dropped

    def advance_display_epoch(self):
        self._display.advance_epoch()

    def _flush_display(self, limit=2000):
        epoch, pending = self._display.drain(limit)
        frames = [frame for captured_epoch, frame, _ in pending if captured_epoch == epoch]
        if frames:
            self.messages.emit(frames)
        # Compatibility for callers observing individual display frames. The
        # main monitor subscribes only to messages; RX never queues one Qt event
        # per frame across the reader/UI thread boundary.
        if self.receivers('2message(PyObject)'):
            for _, frame, legacy in pending:
                if legacy:
                    self.message.emit(frame)

    def _capture_received(self, msg):
        # In fallback mode the local echo is the sole TX observation. Some
        # drivers expose other clients' local echoes despite their setting.
        if not msg.is_rx and not self.driver_tx_reports:
            return None
        epoch = self._display.epoch
        captured = snapshot_message(msg)
        captured.raw_timestamp = msg.timestamp
        if self._rx_clock is not None:
            captured.timestamp = self._rx_clock.to_unix(msg.timestamp)
            captured.timestamp_source = 'device clock anchored to monotonic host epoch'
        elif self._interface in {'virtual', 'slcan'}:
            captured.timestamp = self._clock.now()
            captured.timestamp_source = 'monotonic host receive estimate'
        elif not isinstance(msg.timestamp, (int, float)) or not math.isfinite(msg.timestamp) or msg.timestamp <= 0:
            captured.timestamp = self._clock.now()
            captured.timestamp_source = 'host receive estimate (invalid driver timestamp)'
        else:
            captured.timestamp_source = 'driver epoch timestamp'
        if not captured.is_rx:
            captured.tx_confirmation = 'driver report (not intended-ECU delivery proof)'
        if captured.channel is None or len(self.channels) == 1:
            captured.channel = self._channel
        self.traffic.emit(captured)
        self._display.put(captured, epoch)
        return captured

    def send_message(self, msg: can.Message, report_errors=True, *, return_timestamp=False) -> bool | float:
        with self._send_lock:
            try:
                if self._bus is None:
                    raise RuntimeError("Connect before sending a message.")
                if self.listen_only:
                    raise RuntimeError('Listen-only connection: transmission is disabled, including scripts and replay.')
                validate_frame(msg, self.fd_enabled)
                target = self.tx_channel if msg.channel is None else msg.channel
                selected = next((c for c in self.channels if str(target) in (str(c['native_channel']), c['label'])), None)
                if selected is None:
                    raise ValueError('TX channel is not enabled. Select one enabled CAN channel.')
                outgoing = snapshot_message(msg)
                outgoing.channel = selected['native_channel']
                self._bus.send(outgoing, timeout=0.2)
            except Exception as exc:
                if report_errors:
                    self.error.emit(f"Send failed: {exc}")
                return False
            accepted_at = self._clock.now()
            if self.driver_tx_reports:
                # recv() publishes the driver's later timestamp exactly once.
                # Never synthesize a confirmation when that report is absent.
                return accepted_at if return_timestamp else True
            echo = copy(outgoing)
            echo.timestamp = accepted_at
            echo.timestamp_source = 'monotonic host TX acceptance estimate'
            echo.raw_timestamp = None
            echo.tx_confirmation = 'local acceptance only'
            echo.is_rx = False
            echo.channel = outgoing.channel
            epoch = self._display.epoch
            self.traffic.emit(echo)
            self._display.put(echo, epoch, legacy=False)
        self.message.emit(echo)
        return echo.timestamp if return_timestamp else True

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
        self._display_timer.stop()
        self._flush_display(self._display.capacity)
        self._rx_clock = None
        self.channels = []
        self.tx_channel = None

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
    if interface in {"slcan", "virtual", "nixnet"}:
        return channel
    try:
        return int(channel)
    except ValueError:
        return channel
