"""Qt-free DBC document logic shared by editor views and tests."""
from copy import deepcopy
from pathlib import Path
import math
import re

import cantools
from cantools.database.can import Node


def identifier(text):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', text):
        raise ValueError('Names must start with a letter or underscore and contain only letters, digits and underscores.')
    return text


def number(text, optional=False):
    if optional and not text.strip():
        return None
    result = float(text)
    if not math.isfinite(result):
        raise ValueError('Numeric fields must be finite.')
    return result


def signal_bits(signal):
    """Return DBC bit numbers in wire order, including Motorola byte crossings."""
    bit = signal.start
    bits = []
    for _ in range(signal.length):
        bits.append(bit)
        bit = bit + 1 if signal.byte_order == 'little_endian' else (bit + 15 if bit % 8 == 0 else bit - 1)
    return bits


def validate_database(database):
    names, ids = set(), set()
    for message in database.messages:
        identifier(message.name)
        key = (message.frame_id, message.is_extended_frame)
        if message.name in names or key in ids:
            raise ValueError('Message names and CAN IDs within the same frame format must be unique.')
        names.add(message.name)
        ids.add(key)
        if not 0 <= message.frame_id <= (0x1FFFFFFF if message.is_extended_frame else 0x7FF):
            raise ValueError('CAN ID is outside the selected frame format.')
        if not 0 <= message.length <= (64 if message.is_fd else 8):
            raise ValueError('Classic CAN allows 0..8 bytes; CAN FD allows 0..64 bytes.')
        if message.is_fd and message.length not in (*range(9), 12, 16, 20, 24, 32, 48, 64):
            raise ValueError('CAN FD length must be 0..8, 12, 16, 20, 24, 32, 48 or 64 bytes.')
        signal_names = set()
        for signal in message.signals:
            identifier(signal.name)
            if signal.name in signal_names:
                raise ValueError('Signal names must be unique within a message.')
            signal_names.add(signal.name)
            if signal.length < 1 or signal.start < 0:
                raise ValueError('Signal start must be nonnegative and length must be positive.')
            if not math.isfinite(signal.scale) or signal.scale == 0 or not math.isfinite(signal.offset):
                raise ValueError('Signal factor must be finite and nonzero; offset must be finite.')
            if signal.minimum is not None and signal.maximum is not None and signal.minimum > signal.maximum:
                raise ValueError('Minimum must not exceed maximum.')
            if signal.is_float and signal.length not in (32, 64):
                raise ValueError('Floating-point signals require 32 or 64 bits.')
            if not signal.is_float:
                low = -(1 << (signal.length-1)) if signal.is_signed else 0
                high = (1 << (signal.length-int(signal.is_signed))) - 1
                if any(not low <= value <= high for value in (signal.choices or {})):
                    raise ValueError('Value-label keys must fit the signal raw bit range.')
        message.refresh(strict=True)
    database.refresh()


class DbcDocument:
    def __init__(self, database=None, path=None):
        self.database = deepcopy(database) if database is not None else cantools.database.Database()
        self.path = Path(path).resolve() if path else None
        self.dirty = False
        self._undo = []
        self._redo = []
        self._saved_text = self.database.as_dbc_string()

    def commit(self, candidate):
        validate_database(candidate)
        self._undo.append(deepcopy(self.database))
        self._redo.clear()
        self.database = candidate
        self.dirty = self.database.as_dbc_string() != self._saved_text

    def undo(self):
        if self._undo:
            self._redo.append(self.database)
            self.database = self._undo.pop()
            self.dirty = self.database.as_dbc_string() != self._saved_text

    def redo(self):
        if self._redo:
            self._undo.append(self.database)
            self.database = self._redo.pop()
            self.dirty = self.database.as_dbc_string() != self._saved_text

    def mark_saved(self):
        self._saved_text = self.database.as_dbc_string()
        self.dirty = False

    def save_snapshot(self):
        database = deepcopy(self.database)
        validate_database(database)
        nodes = {node.name for node in database.nodes}
        for message in database.messages:
            for name in message.senders + [n for signal in message.signals for n in signal.receivers]:
                if name not in nodes:
                    database.nodes.append(Node(name))
                    nodes.add(name)
        if any(message.is_fd for message in database.messages):
            # DBC represents FD through attributes, not payload length alone.
            # Parse a tiny schema rather than depending on cantools' private classes.
            schema = cantools.database.load_string('''VERSION ""
NS_ :
BS_:
BU_:
BA_DEF_ "BusType" STRING ;
BA_DEF_ BO_ "VFrameFormat" ENUM "StandardCAN","ExtendedCAN","StandardCAN_FD","ExtendedCAN_FD","J1939PG";
BA_DEF_DEF_ "VFrameFormat" "StandardCAN";
BA_ "BusType" "CAN FD";
''', database_format='dbc')
            if database.dbc is None:
                database.dbc = deepcopy(schema.dbc)
            else:
                for name, definition in schema.dbc.attribute_definitions.items():
                    if name not in database.dbc.attribute_definitions:
                        database.dbc.attribute_definitions[name] = deepcopy(definition)
                database.dbc.attributes['BusType'] = deepcopy(schema.dbc.attributes['BusType'])
        for message in database.messages:
            if message.dbc is not None:
                # Regenerate from the edited frame flags, including a change back
                # to the schema's default format (which has no explicit attribute).
                message.dbc.attributes.pop('VFrameFormat', None)
        text = database.as_dbc_string()
        text.encode('cp1252')
        loaded = cantools.database.load_string(text, database_format='dbc', strict=True)
        for original in database.messages:
            restored = next(m for m in loaded.messages if m.frame_id == original.frame_id
                            and m.is_extended_frame == original.is_extended_frame)
            if restored.is_fd != original.is_fd:
                raise ValueError('Existing VFrameFormat attributes cannot represent the selected CAN FD format.')
        return database, text
