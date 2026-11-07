from __future__ import annotations

from typing import Any
import math
import logging
from copy import copy
from threading import RLock

import can
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from signal_explorer.bus.backends import BackendSpec
from signal_explorer.bus.canfd import fd_configuration, validate_frame
from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.bus.demo import demo_frames, demo_reply
from signal_explorer.bus.timestamps import HardwareClock, SessionClock
from signal_explorer.bus.multichannel import native_channel, open_channels
from signal_explorer.bus.display_queue import DisplayQueue
from signal_explorer.bus.frames import snapshot_message
from signal_explorer.bus.listen_only import open_bus
from signal_explorer.bus.timing import TimingSettings
from signal_explorer.bus.tx_dispatch import TxDispatcher, TxFailure, send_failure
from signal_explorer.bus.health import BusHealth
from signal_explorer.bus.health_native import query_health
from signal_explorer.bus.strategies import BackendStrategy, interfaces_supporting, strategy_for

_DRIVER_TASKS = set()  # Keep outstanding native calls alive until Qt receives completion.
logger = logging.getLogger(__name__)


class _DriverTask(QThread):
    def __init__(self, owner, operation):
        super().__init__()
        self.owner, self.operation = owner, operation
        self.result = self.problem = None
        _DRIVER_TASKS.add(self)

    def run(self):
        try: self.result = self.operation()
        except Exception as exc:
            logger.exception('Native CAN operation failed')
            self.problem = exc

    def release(self):
        self.owner = self.operation = None
        _DRIVER_TASKS.discard(self)
        self.deleteLater()

TX_REPORT_INTERFACES = interfaces_supporting('tx_reports')


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
                logger.exception('CAN receive failed')
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
    connection_state_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus: can.BusABC | None = None
        self._device_lease = None
        self._send_lock = RLock()
        self._native_gates = {}
        self._dispatcher = None
        self._connection_task = None
        self._closing = self._cancel_connect = False
        self.session_info = {}
        self._channel = None
        self.channels = []
        self.tx_channel = None
        self.fd_enabled = False
        self._rx_clock = None
        self._clock = SessionClock()
        self._interface = None
        self._strategy = BackendStrategy()
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
        self.health = BusHealth()
        self._health_pending = {}
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self._poll_health)

    @property
    def is_connected(self) -> bool:
        return self._bus is not None

    @property
    def can_send(self):
        return self.is_connected and not self.listen_only and not self.busy

    @property
    def busy(self):
        return self._connection_task is not None or self._closing

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
        if self.busy:
            raise RuntimeError('Wait for the current CAN connection operation to finish.')
        if self._bus is not None:
            self.disconnect_bus()

        try:
            result = self._open_connection(backend, channel, bitrate, inject_demo, device_config,
                                          fd, data_bitrate, driver_tx_reports, listen_only, timing_settings)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self._adopt_connection(result)

    def connect_async(self, *args, **kwargs):
        if self.busy or self.is_connected:
            return
        self._cancel_connect = False
        self._connection_task = _DriverTask(self, lambda: self._open_connection(*args, **kwargs))
        self._connection_task.finished.connect(self._finish_connect)
        self.connection_state_changed.emit('connecting')
        self._connection_task.start()

    def cancel_connect(self):
        self._cancel_connect = True
        self.connection_state_changed.emit('cancelling')

    def _finish_connect(self):
        task, self._connection_task = self._connection_task, None
        result, problem = task.result, task.problem
        task.release()
        if result is not None and self._cancel_connect:
            # Opening a native device cannot always be interrupted. Close a
            # late result in a worker before permitting any new connection.
            self._closing = True
            self._connection_task = _DriverTask(self, lambda: self._dispose_open_result(result))
            self._connection_task.finished.connect(self._finish_cancelled_open)
            self._connection_task.start()
            return
        if result is not None:
            self._adopt_connection(result)
            self.connection_state_changed.emit('connected')
        else:
            self.connection_state_changed.emit('disconnected')
            if not self._cancel_connect: self.error.emit(str(problem))

    @staticmethod
    def _dispose_open_result(result):
        try: result['bus'].shutdown()
        finally:
            if result['lease']: result['lease'].release()

    def _finish_cancelled_open(self):
        task, self._connection_task = self._connection_task, None
        problem = task.problem
        task.release()
        self._closing = False
        self.connection_state_changed.emit('disconnected')
        if problem: self.error.emit(f'CAN cleanup failed: {problem}')

    @staticmethod
    def _open_connection(backend, channel, bitrate, inject_demo, device_config=None,
                         fd=False, data_bitrate=2000000, driver_tx_reports=False,
                         listen_only=False, timing_settings=None):
        bus = lease = None
        strategy = strategy_for(backend.interface)
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
        if driver_tx_reports and not strategy.tx_reports:
            raise ValueError('Driver TX reports are not supported by this backend. Disable that option.')
        kwargs["receive_own_messages"] = driver_tx_reports
        kwargs["ignore_config"] = True
        strategy.prepare_connection(kwargs, bitrate, driver_tx_reports, device_config or {})

        try:
            if listen_only and not strategy.listen_only:
                raise ValueError('Listen-only is unavailable for this backend; no active-mode fallback is allowed.')
            request = timing_settings or TimingSettings(bitrate=bitrate, data_bitrate=data_bitrate)
            if request.bitrate != bitrate or (fd and request.data_bitrate != data_bitrate):
                raise ValueError('Timing rates must match the connection rates.')
            custom_timing = request.build(backend.interface, fd)
            primary_channel = native_channel(backend.interface, kwargs['channel'])
            strategy.validate_connection(device_config or {}, configs, fd)
            if fd:
                kwargs.update(fd_configuration(backend.interface, bitrate, data_bitrate))
            if custom_timing is not None:
                kwargs['timing'] = custom_timing
            if strategy.needs_lease:
                lease = DeviceLease(backend.interface, kwargs.get('serial'), 'CAN')
            factory = lambda **args: open_bus(monitor_only=listen_only, **args)
            bus = (open_channels(factory, backend.interface, configs, kwargs)
                         if len(configs) > 1 else factory(**kwargs))
        except Exception as exc:  # noqa: BLE001 — surface any vendor/driver failure in the UI
            logger.exception('CAN connect failed: %s channel %s', backend.interface, channel)
            bus = None
            if lease:
                lease.release()
                lease = None
            hint = (' Driver TX reports require compatible drivers. Disable that option to use local TX estimates.'
                    if driver_tx_reports else '') + strategy.connection_hint
            raise RuntimeError(f"Connect failed ({backend.label}): {exc}{hint}") from exc

        return dict(backend=backend, channel=channel, bitrate=bitrate, inject_demo=inject_demo,
                    device_config=device_config, fd=fd, data_bitrate=data_bitrate,
                    driver_tx_reports=driver_tx_reports, listen_only=listen_only,
                    kwargs=kwargs, configs=configs, request=request, custom_timing=custom_timing,
                    primary_channel=primary_channel, bus=bus, lease=lease)

    def _adopt_connection(self, result):
        backend, channel, bitrate, inject_demo, device_config, fd, data_bitrate, driver_tx_reports, listen_only = (
            result[key] for key in ('backend','channel','bitrate','inject_demo','device_config',
                                    'fd','data_bitrate','driver_tx_reports','listen_only'))
        kwargs, configs, request, custom_timing, primary_channel = (
            result[key] for key in ('kwargs','configs','request','custom_timing','primary_channel'))
        self._bus, self._device_lease = result['bus'], result['lease']
        self.fd_enabled = fd
        self._display.reset()
        self._clock = SessionClock()
        self._interface = backend.interface
        self._strategy = strategy_for(backend.interface)
        self.session_started_at = self._clock.now()
        self.driver_tx_reports = driver_tx_reports
        self.listen_only = listen_only
        self._rx_clock = HardwareClock(self._clock.now) if self._strategy.timestamp_source == 'device_relative' else None
        self._channel = primary_channel
        self.channels = [dict(native_channel=native_channel(backend.interface, cfg['channel']),
                              label=cfg.get('channel_label', str(cfg['channel'])),
                              bus_channel=self._strategy.bus_number(cfg, index))
                         for index, cfg in enumerate(configs)]
        self.tx_channel = self._channel
        keys = list(self._bus.buses) if hasattr(self._bus, 'buses') else ['shared']
        self._native_gates = {str(key): RLock() for key in keys}
        self._dispatcher = TxDispatcher(keys)
        self.session_info = dict(adapter=backend.label, interface=backend.interface,
                                 native_channel=str(self._channel), bitrate=bitrate, fd=fd,
                                 data_bitrate=data_bitrate if fd else None,
                                 serial=(device_config or {}).get("serial"))
        self.session_info['channels'] = self.channels
        self.session_info['session_started_at'] = self.session_started_at
        logger.info('CAN connected: interface=%s channels=%s bitrate=%s FD=%s listen_only=%s',
                    backend.interface, self.channels, bitrate, fd, listen_only)
        self.session_info.update(listen_only=listen_only, timing_request=vars(request),
                                 calculated_timing=str(custom_timing) if custom_timing is not None else 'Driver/default timing')
        self.session_info.update(
            driver_tx_reports=driver_tx_reports,
            host_clock='Unix epoch anchored once to monotonic time for this connection',
            tx_semantics=('Driver TX reports only; no synthetic TX echo; not intended-ECU delivery proof'
                          if driver_tx_reports else 'Local send accepted; host estimate, not a bus acknowledgement'),
            rx_timestamp_source=('Shared device clock anchored at first RX/TX event' if self._rx_clock else
                                 'Host receipt estimate' if self._strategy.timestamp_source == 'host_receipt' else
                                 'Driver epoch timestamp, calibrated by python-can'))
        self.session_info.update(self._strategy.session_details(device_config or {}, kwargs))
        if self._rx_clock is not None:
            self.session_info['timestamp_source'] = 'Hardware RX/TX intervals anchored to monotonic host epoch at first event'
        self.health.start(self.channels, self.session_info)
        self._strategy.setup_health(self._bus, self.health)
        self._health_timer.start()
        self._reader = _Reader(self._bus, self._capture_received)
        self._reader.failed.connect(self._on_reader_failed)
        self._reader.start()

        if inject_demo and self._strategy.simulated:
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
        elif self._strategy.timestamp_source == 'host_receipt':
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
        self.health.observe(captured)
        self.traffic.emit(captured)
        self._display.put(captured, epoch)
        return captured

    def submit_operation(self, channel, operation):
        with self._send_lock:
            if not self.can_send or self._dispatcher is None:
                from concurrent.futures import Future
                future = Future(); future.set_exception(TxFailure('CAN transmission unavailable or session is closing.'))
                return future
            key = str(channel) if hasattr(self._bus, 'buses') else 'shared'
            return self._dispatcher.submit(key, operation)

    def submit_message(self, msg):
        outgoing = snapshot_message(msg)
        if outgoing.channel is None:
            outgoing.channel = self.tx_channel
        def operation():
            result = self.send_message(outgoing, report_errors=False, return_timestamp=True, raise_on_error=True)
            if result is False: raise TxFailure('Adapter rejected TX.')
            return result
        return self.submit_operation(outgoing.channel, operation)

    def send_message(self, msg: can.Message, report_errors=True, *, return_timestamp=False,
                     raise_on_error=False) -> bool | float:
        """Synchronous background API. UI callers must use submit_message()."""
        try:
            with self._send_lock:
                if self._bus is None or self.busy:
                    raise RuntimeError('Connect before sending; the CAN session may be closing.')
                if self.listen_only:
                    raise RuntimeError('Listen-only connection: transmission is disabled, including scripts and replay.')
                validate_frame(msg, self.fd_enabled)
                target = self.tx_channel if msg.channel is None else msg.channel
                selected = next((c for c in self.channels if str(target) in (str(c['native_channel']), c['label'])), None)
                if selected is None:
                    raise ValueError('TX channel is not enabled. Select one enabled CAN channel.')
                outgoing = snapshot_message(msg)
                outgoing.channel = selected['native_channel']
                bus = self._bus
                key = str(outgoing.channel) if hasattr(bus, 'buses') else 'shared'
                gate = self._native_gates[key]
            # Only callers using this native handle serialize. Never hold the
            # connection-state lock while a vendor function can block.
            with gate:
                if self._closing or self._bus is not bus:
                    raise RuntimeError('CAN session closed before transmission.')
                bus.send(outgoing, timeout=0.0)
                accepted_at = self._clock.now()
                if self.driver_tx_reports:
                    return accepted_at if return_timestamp else True
                echo = copy(outgoing)
                echo.timestamp = accepted_at
                echo.timestamp_source = 'monotonic host TX acceptance estimate'
                echo.raw_timestamp = None
                echo.tx_confirmation = 'local acceptance only'
                echo.is_rx = False
                epoch = self._display.epoch
                self.traffic.emit(echo)
                self.health.observe(echo)
                self._display.put(echo, epoch, legacy=False)
                self.message.emit(echo)
                return echo.timestamp if return_timestamp else True
        except Exception as exc:
            self.health.send_failed(msg.channel if msg.channel is not None else self.tx_channel, str(exc))
            failure = send_failure(exc, self._interface)
            if raise_on_error: raise failure from exc
            if report_errors: self.error.emit(f'Send failed: {failure}')
            return False

    def _begin_disconnect(self):
        with self._send_lock:
            self._closing = True
        self.about_to_disconnect.emit()
        self._demo_timer.stop()
        self._display_timer.stop()
        self._health_timer.stop()
        for future, _ in self._health_pending.values(): future.cancel()
        self._health_pending.clear()
        if self._dispatcher: self._dispatcher.stop()
        if self._reader: self._reader.requestInterruption()

    def _shutdown_connection(self):
        from contextlib import ExitStack
        if self._dispatcher: self._dispatcher.join()
        if self._reader: self._reader.wait()
        with ExitStack() as stack:
            for gate in self._native_gates.values(): stack.enter_context(gate)
            try:
                if self._demo_bus: self._demo_bus.shutdown()
                if self._bus: self._bus.shutdown()
            finally:
                if self._device_lease: self._device_lease.release()

    def _clear_connection(self):
        if self._interface is not None:
            logger.info('CAN disconnected: %s', self._interface)
        self.health.stop()
        self._flush_display(self._display.capacity)
        self._bus = self._demo_bus = self._reader = self._dispatcher = self._device_lease = None
        self._rx_clock = None
        self._native_gates = {}
        self.channels = []
        self.tx_channel = None
        self.fd_enabled = self._closing = False
        self.disconnected.emit()
        self.connection_state_changed.emit('disconnected')

    def disconnect_bus(self) -> None:
        """Synchronous cleanup for background/headless callers and tests."""
        if self._connection_task is not None:
            self.cancel_connect()
            return
        if self._bus is not None:
            self._begin_disconnect()
            try: self._shutdown_connection()
            except Exception as exc:
                logger.exception('CAN cleanup failed')
                self.error.emit(f'CAN cleanup failed: {exc}')
        self._clear_connection()

    def disconnect_async(self):
        if self._connection_task is not None:
            self.cancel_connect(); return
        if self._closing or self._bus is None: return
        self._begin_disconnect()
        self._connection_task = _DriverTask(self, self._shutdown_connection)
        self._connection_task.finished.connect(self._finish_disconnect)
        self.connection_state_changed.emit('disconnecting')
        self._connection_task.start()

    def _finish_disconnect(self):
        task, self._connection_task = self._connection_task, None
        problem = task.problem
        task.release()
        self._clear_connection()
        if problem: self.error.emit(f'CAN cleanup failed: {problem}')

    def _on_reader_failed(self, text: str) -> None:
        with self.health.lock: self.health.fault = text
        self.error.emit(text)
        self.disconnect_async()

    def health_snapshot(self):
        result = self.health.snapshot()
        result['display_drops'] = self.ui_dropped
        return result

    def _poll_health(self):
        if self.busy or self._bus is None or self._dispatcher is None:
            return
        generation = self.health.generation
        handles = self._bus.buses if hasattr(self._bus, 'buses') else {'shared': self._bus}
        for key, bus in handles.items():
            channel = key if key!='shared' else self._channel
            pending = self._health_pending.get(key)
            if pending is not None:
                future, captured_generation = pending
                if not future.done(): continue  # At most one pending query per handle.
                self._health_pending.pop(key)
                if not future.cancelled():
                    try:
                        result = future.result()
                        if result is not None:
                            values, observed_at = result
                            if values is not None:
                                self.health.native_update(channel, values, captured_generation, observed_at=observed_at)
                    except Exception as exc:
                        self.health.native_update(channel, dict(state=None, tec=None, rec=None, driver_load=None,
                            overruns=None, overrun_flag=None, statistics_overruns=None,
                            source=f'{self._interface} diagnostics unavailable',
                            note=f'Diagnostic query failed: {exc}'), captured_generation)
            if self._interface not in {'vector', 'pcan', 'kvaser', 'nixnet'}:
                continue
            gate = self._native_gates[str(key)]
            def query(target=bus, guard=gate, session_bus=self._bus, interface=self._interface):
                with guard:
                    if self._closing or self._bus is not session_bus: return None
                    return query_health(target, interface), self.health.clock()
            self._health_pending[key] = (self._dispatcher.submit(key, query), generation)

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
    return strategy_for(interface).connection_channel(channel)
