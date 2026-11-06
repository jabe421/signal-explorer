"""Multi-message CAN scheduling through the application's existing bus service."""
from collections import deque
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
import math
from threading import Condition, Thread
import time
from uuid import uuid4

import can
import cantools
from cantools.database.can.formats.dbc import DbcSpecifics
from PySide6.QtCore import QObject, Signal
from signal_explorer.bus.canfd import validate_frame
from signal_explorer.dbc.document import signal_bits


@dataclass(frozen=True)
class Waveform:
    kind: str = 'Off'
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    period_ms: int = 1000

    def value(self, elapsed, tick):
        if self.kind not in ('Off', 'Ramp', 'Pulse', 'Sine'):
            raise ValueError('Unknown waveform.')
        if not all(math.isfinite(v) for v in (self.minimum, self.maximum, self.step)):
            raise ValueError('Waveform values must be finite.')
        if self.minimum > self.maximum or self.step <= 0 or not 1 <= self.period_ms <= 86400000:
            raise ValueError('Waveform requires min <= max, a positive step and a 1-86400000 ms period.')
        span = self.maximum - self.minimum
        if not span: return self.minimum
        phase = (elapsed * 1000 / self.period_ms) % 1
        if self.kind == 'Ramp':
            steps = math.floor(span / self.step) + 1
            return self.minimum + (tick % steps) * self.step
        if self.kind == 'Pulse': return self.minimum if phase < .5 else self.maximum
        return self.minimum + span * (1 - math.cos(2 * math.pi * phase)) / 2


@dataclass(frozen=True)
class TxEntry:
    uid: str = field(default_factory=lambda: uuid4().hex)
    name: str = 'Message'
    identifier: int = 0x100
    channel: str | int | None = None
    extended: bool = False
    fd: bool = False
    brs: bool = False
    data: bytes = bytes(8)
    enabled: bool = True
    cyclic: bool = False
    period_ms: int = 100
    definition: object = None
    waves: dict = field(default_factory=dict)

    def frame(self, channel=None, elapsed=0, tick=0):
        if type(self.identifier) is not int or type(self.period_ms) is not int:
            raise ValueError('CAN ID and cycle time must be integers.')
        if self.definition and (len(self.data) != self.definition.length or self.identifier != self.definition.frame_id
                                or self.extended != self.definition.is_extended_frame or self.fd != self.definition.is_fd):
            raise ValueError('DBC definition does not match the message layout.')
        payload = self.data
        if self.waves:
            if self.definition is None: raise ValueError('Waveforms require a DBC message.')
            values = self.definition.decode(payload, decode_choices=False)
            for name, wave in self.waves.items():
                signal = self.definition.get_signal_by_name(name)
                if signal.is_multiplexer or name not in values:
                    raise ValueError('Waveforms require an active, non-multiplexer signal.')
                if wave.kind != 'Off': values[name] = wave.value(elapsed, tick)
            payload = encode_signals(self.definition, self.data, values)
        msg = can.Message(arbitration_id=self.identifier, channel=channel, data=payload,
                          is_extended_id=self.extended, is_fd=self.fd, bitrate_switch=self.brs, check=True)
        validate_frame(msg, True)
        if not 1 <= self.period_ms <= 2147483647:
            raise ValueError('Cycle time must be 1-2147483647 milliseconds.')
        return msg


def default_signal_values(definition):
    values = {}
    for signal in definition.signals:
        value = signal.initial if signal.initial is not None else 0
        if signal.minimum is not None: value = max(value, signal.minimum)
        if signal.maximum is not None: value = min(value, signal.maximum)
        if signal.is_multiplexer and signal.initial is None:
            branches = [i for s in definition.signals if s.multiplexer_signal == signal.name for i in (s.multiplexer_ids or [])]
            if branches: value = signal.conversion.raw_to_scaled(min(branches), decode_choices=False)
        values[signal.name] = value
    return values


def encode_signals(definition, base, values):
    values = definition.gather_signals(values)
    encoded = definition.encode(values, strict=True)
    mask = bytearray(len(base))
    for signal in definition.signals:
        if signal.name in values:
            for bit in signal_bits(signal): mask[bit // 8] |= 1 << (bit % 8)
    return bytes((old & ~bits) | (new & bits) for old, new, bits in zip(base, encoded, mask))


def dbc_entry(definition):
    definition = deepcopy(definition)
    values = default_signal_values(definition)
    data = definition.encode(definition.gather_signals(values), strict=True)
    return TxEntry(name=definition.name, identifier=definition.frame_id, extended=definition.is_extended_frame,
                   fd=definition.is_fd, data=data, period_ms=definition.cycle_time or 100, definition=definition)


def definition_text(definition):
    definitions = OrderedDict()
    for obj in [definition, *definition.signals]:
        specifics = getattr(obj, 'dbc', None)
        for attribute in (specifics.attributes.values() if specifics else []):
            definitions[attribute.name] = deepcopy(attribute.definition)
    return cantools.database.Database(messages=[deepcopy(definition)],
        dbc_specifics=DbcSpecifics(attribute_definitions=definitions)).as_dbc_string()


def encode_entries(entries):
    rows = []
    for entry in entries:
        entry.frame()
        row = {key: getattr(entry, key) for key in ('name','identifier','channel','extended','fd','brs','enabled','cyclic','period_ms')}
        row['data'] = entry.data.hex()
        row['waves'] = {name: vars(wave) for name, wave in entry.waves.items()}
        row['dbc'] = definition_text(entry.definition) if entry.definition else None
        rows.append(row)
    return json.dumps(dict(format='SignalExplorer CAN transmit list', version=1, messages=rows), indent=2)


def decode_entries(text):
    document = json.loads(text)
    if document.get('format') != 'SignalExplorer CAN transmit list' or document.get('version') != 1:
        raise ValueError('Not a supported Signal Explorer transmit list.')
    rows = document['messages']
    if not isinstance(rows, list) or len(rows) > 256: raise ValueError('A transmit list supports at most 256 messages.')
    entries = []
    for source in rows:
        row = dict(source)
        allowed = {'name','identifier','channel','extended','fd','brs','enabled','cyclic','period_ms','data','waves','dbc'}
        if set(row) - allowed: raise ValueError('Unknown fields in a transmit row.')
        for key in ('extended','fd','brs','enabled','cyclic'):
            if not isinstance(row[key], bool): raise ValueError('Transmit flags must be booleans.')
        if type(row['identifier']) is not int or type(row['period_ms']) is not int:
            raise ValueError('CAN ID and cycle time must be integers.')
        if not isinstance(row['name'], str) or (row['channel'] is not None and type(row['channel']) not in (str,int)):
            raise ValueError('Invalid message name or channel.')
        dbc = row.pop('dbc', None)
        waves = row.pop('waves', {})
        row['data'] = bytes.fromhex(row['data'])
        row['definition'] = cantools.database.load_string(dbc, database_format='dbc').messages[0] if dbc else None
        row['waves'] = {name: Waveform(**settings) for name, settings in waves.items()}
        entry = TxEntry(**row)
        if entry.definition and (entry.identifier != entry.definition.frame_id or entry.extended != entry.definition.is_extended_frame
                                 or entry.fd != entry.definition.is_fd or len(entry.data) != entry.definition.length):
            raise ValueError('DBC definition does not match its transmit row.')
        entry.frame()
        entries.append(entry)
    return entries


class TransmitEngine(QObject):
    changed = Signal()
    progress = Signal()
    failed = Signal(str)

    def __init__(self, service, playback_busy=lambda: False, parent=None):
        super().__init__(parent)
        self.service, self.playback_busy = service, playback_busy
        self._condition = Condition()
        self._entries, self._counts, self._errors, self._versions = {}, {}, {}, {}
        self._instances = {}
        self._last_data = {}
        self._active, self._pending, self._inflight = {}, deque(), None
        self._closed = False
        self._last_notice = 0
        service.about_to_disconnect.connect(self.stop_all)
        self._worker = Thread(target=self._run, name='SignalExplorer-MultiTX', daemon=False)
        self._worker.start()

    def entries(self):
        with self._condition: return deepcopy(list(self._entries.values()))

    def get(self, uid):
        with self._condition: return deepcopy(self._entries[uid])

    def state(self, uid):
        with self._condition:
            return (self._counts.get(uid, 0), uid in self._active,
                    uid == self._inflight or any(job[0] == uid for job in self._pending), self._errors.get(uid, ''))

    def last_data(self, uid):
        with self._condition: return self._last_data.get(uid, self._entries[uid].data)

    def add(self, entry):
        entry.frame()
        with self._condition:
            if len(self._entries) >= 256: raise ValueError('At most 256 messages are supported.')
            if entry.uid in self._entries: raise ValueError('Duplicate transmit row.')
            self._entries[entry.uid] = deepcopy(entry)
            self._instances[entry.uid] = object()
            self._counts[entry.uid] = self._versions[entry.uid] = 0
        self.changed.emit()
        return entry.uid

    def update(self, entry):
        entry.frame()
        with self._condition:
            if entry.uid not in self._entries: raise ValueError('Transmit row no longer exists.')
            old = self._entries[entry.uid]
            runtime = self._active.get(entry.uid)
            queued = [job for job in self._pending if job[0] == entry.uid]
            stopping = not entry.enabled or (old.cyclic and not entry.cyclic)
            # Preflight the entire new configuration before changing a running
            # schedule. An unchanged Default TX retains its pinned destination.
            target = None
            if not stopping and (runtime or queued or self._inflight == entry.uid):
                pinned = runtime[4] if runtime else queued[0][2].channel if queued else None
                effective = replace(entry, channel=pinned) if entry.channel == old.channel and pinned is not None else entry
                target = self._prepare(effective).channel
            if stopping:self._versions[entry.uid] += 1
            version = self._versions[entry.uid]
            self._entries[entry.uid] = deepcopy(entry)
            if stopping:
                self._active.pop(entry.uid, None)
                self._pending = deque(job for job in self._pending if job[0] != entry.uid)
            else:
                if runtime:
                    runtime[3], runtime[4] = version, target
                    if entry.period_ms != old.period_ms:
                        runtime[0] = time.monotonic() + entry.period_ms / 1000
                    if entry.waves and not old.waves:
                        runtime[1], runtime[2] = time.monotonic(), 0
                self._pending = deque((uid, version, entry.frame(target)) if uid == entry.uid else (uid, rev, msg)
                                      for uid, rev, msg in self._pending)
            self._errors.pop(entry.uid, None)
            self._last_data.pop(entry.uid, None)
            self._condition.notify_all()
        self.changed.emit()

    def remove(self, uids):
        self.stop(uids)
        with self._condition:
            for uid in uids:
                self._entries.pop(uid, None); self._counts.pop(uid, None); self._errors.pop(uid, None)
                self._last_data.pop(uid, None)
                self._instances.pop(uid, None)
        self.changed.emit()

    def replace_entries(self, entries):
        entries = deepcopy(list(entries))
        if len(entries) > 256 or len({e.uid for e in entries}) != len(entries):
            raise ValueError('A transmit list requires at most 256 distinct rows.')
        for entry in entries: entry.frame()
        self.stop_all()
        with self._condition:
            self._entries = {e.uid: e for e in entries}
            self._instances = {e.uid: object() for e in entries}
            self._counts = {e.uid: 0 for e in entries}
            self._versions = {e.uid: self._versions.get(e.uid, 0) + 1 for e in entries}
            self._errors.clear(); self._last_data.clear()
        self.changed.emit()

    def _prepare(self, entry):
        if not self.service.can_send: raise ValueError('CAN transmission unavailable: disconnect listen-only mode before sending.')
        if not self.service.is_connected: raise ValueError('Connect a CAN adapter before sending.')
        if self.playback_busy(): raise ValueError('Stop logfile playback before sending these messages.')
        target = self.service.tx_channel if entry.channel is None else entry.channel
        channel = next((c['native_channel'] for c in self.service.channels
                        if str(target) in (str(c['native_channel']), c['label'])), None)
        if channel is None: raise ValueError(f'{entry.name}: selected TX channel is not enabled.')
        msg = entry.frame(channel)
        validate_frame(msg, self.service.fd_enabled)
        # Reject waveform endpoints up front instead of failing partway through a cycle.
        for name, wave in entry.waves.items():
            wave.value(0, 0)
            if wave.kind == 'Off': continue
            values = entry.definition.decode(entry.data, decode_choices=False)
            for value in (wave.minimum, wave.maximum):
                values[name] = value
                entry.definition.encode(values, strict=True)
        return msg

    def start(self, uids, cyclic=False):
        with self._condition:
            entries = [self._entries[uid] for uid in uids if self._entries[uid].enabled]
            prepared = [(entry, self._prepare(entry)) for entry in entries]
            if not prepared: raise ValueError('No enabled messages selected.')
            now = time.monotonic()
            for entry, msg in prepared:
                if self.state(entry.uid)[1] or self.state(entry.uid)[2]: raise ValueError('One selected message is already running or pending.')
            for entry, msg in prepared:
                self._errors.pop(entry.uid, None)
                version = self._versions[entry.uid]
                if cyclic: self._active[entry.uid] = [now, now, 0, version, msg.channel]
                else: self._pending.append((entry.uid, version, msg))
            self._condition.notify_all()
        self.progress.emit()

    def stop(self, uids):
        with self._condition:
            for uid in uids:
                self._active.pop(uid, None)
                self._versions[uid] = self._versions.get(uid, 0) + 1
            self._pending = deque(job for job in self._pending if job[0] not in uids)
            self._condition.notify_all()
        self.progress.emit()

    def stop_all(self):
        with self._condition: uids = list(self._entries)
        self.stop(uids)

    def shutdown(self):
        self.stop_all()
        with self._condition:
            self._closed = True; self._condition.notify_all()
        self._worker.join(2)

    def _run(self):
        while True:
            with self._condition:
                if self._closed: return
                now = time.monotonic()
                due = min(self._active, key=lambda uid: self._active[uid][0], default=None)
                if self._pending:
                    uid, version, msg = self._pending.popleft()
                    row, runtime = self._entries.get(uid), None
                elif due is not None and self._active[due][0] <= now:
                    uid, row = due, self._entries[due]
                    deadline, started, tick, version, target = self._active[uid]
                    period = row.period_ms / 1000
                    self._active[uid][0] = deadline + (math.floor((now-deadline)/period)+1)*period
                    self._active[uid][2] += 1
                    msg, runtime = None, (target, now-started, tick)
                else:
                    self._condition.wait(None if due is None else max(0, self._active[due][0]-now))
                    continue
                if row is None or version != self._versions.get(uid): continue
                self._inflight = uid
                instance = self._instances.get(uid)
            error = ''
            try:
                if runtime is not None: msg = row.frame(*runtime)
                with self._condition:
                    cancelled = version != self._versions.get(uid) or self._instances.get(uid) is not instance
                    if cancelled:
                        self._inflight = None
                        self._condition.notify_all()
                if cancelled:
                    self.progress.emit()
                    continue
                if not self.service.send_message(msg, report_errors=False):
                    raise RuntimeError('Adapter rejected TX; check connection, channel, FD settings and bus status.')
            except Exception as exc:
                error = f'{row.name}: {exc}'
            with self._condition:
                self._inflight = None
                if self._instances.get(uid) is instance:
                    if error and version == self._versions.get(uid) and self._entries.get(uid) is row:
                        self._errors[uid] = error; self._active.pop(uid, None)
                    elif not error:
                        self._counts[uid] += 1
                        self._last_data[uid] = bytes(msg.data)
                notice = runtime is None or error or time.monotonic()-self._last_notice >= .05
                if notice: self._last_notice = time.monotonic()
                self._condition.notify_all()
            if error: self.failed.emit(error)
            if notice: self.progress.emit()
