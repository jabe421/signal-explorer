import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import can
import cantools
from cantools.database.can import Message, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from signal_explorer.dbc.document import DbcDocument, signal_bits, validate_database
from signal_explorer.ui.dbc_editor import DbcEditorWindow, PropertiesDialog
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.bus.backends import BACKENDS

app = QApplication.instance() or QApplication([])


def database():
    return cantools.database.load_string('''VERSION "test"
NS_ :
BS_:
BU_: Board Tool
BO_ 272 Analog: 2 Board
 SG_ Pot : 7|12@0+ (0.001221001221001221,0) [0|5] "V" Tool
BO_ 577 Buttons: 1 Board
 SG_ Button : 7|1@1+ (1,0) [0|1] "" Tool
CM_ SG_ 272 Pot "Original comment";
BA_DEF_ BO_ "GenMsgCycleTime" INT 0 65535;
BA_ "GenMsgCycleTime" BO_ 272 10;
VAL_ 577 Button 0 "Released" 1 "Pressed";
''', database_format='dbc')


class DocumentTests(unittest.TestCase):
    def test_isolation_validation_and_undo(self):
        source = database()
        doc = DbcDocument(source)
        candidate = deepcopy(doc.database)
        candidate.messages[0].signals[0].name = 'Voltage'
        doc.commit(candidate)
        self.assertEqual(source.messages[0].signals[0].name, 'Pot')
        self.assertTrue(doc.dirty)
        invalid = deepcopy(doc.database)
        invalid.messages[0].signals.append(Signal('Overlap', 7, 1))
        with self.assertRaises(cantools.database.errors.Error):
            doc.commit(invalid)
        self.assertEqual(len(doc.database.messages[0].signals), 1)
        doc.undo()
        self.assertFalse(doc.dirty)
        self.assertEqual(doc.database.messages[0].signals[0].name, 'Pot')
        doc.redo()
        self.assertEqual(doc.database.messages[0].signals[0].name, 'Voltage')

    def test_motorola_layout_and_metadata_roundtrip(self):
        doc = DbcDocument(database())
        self.assertEqual(signal_bits(doc.database.messages[0].signals[0]), list(range(7, -1, -1)) + [15,14,13,12])
        _, text = doc.save_snapshot()
        loaded = cantools.database.load_string(text, database_format='dbc')
        self.assertAlmostEqual(loaded.decode_message(272, bytes.fromhex('FF F0'))['Pot'], 5)
        self.assertEqual(loaded.messages[0].cycle_time, 10)
        self.assertEqual(loaded.messages[0].signals[0].comment, 'Original comment')
        self.assertEqual(str(loaded.decode_message(577, b'\x80')['Button']), 'Pressed')

    def test_invalid_ids_duplicates_lengths_and_factor(self):
        for mutate in (
            lambda db: setattr(db.messages[0], 'frame_id', 0x800),
            lambda db: setattr(db.messages[0], 'length', 1),
            lambda db: setattr(db.messages[0].signals[0], 'scale', 0),
            lambda db: db.messages.append(deepcopy(db.messages[0])),
        ):
            with self.subTest(mutate=mutate):
                db = database()
                with self.assertRaises((ValueError, cantools.database.errors.Error)):
                    mutate(db)
                    validate_database(db)

    def test_extended_fd_and_multiplexed_roundtrip(self):
        mux = Signal('Selector', 0, 4, is_multiplexer=True)
        signals = [mux, Signal('A', 8, 8, multiplexer_ids=[0], multiplexer_signal='Selector'),
                   Signal('B', 8, 8, multiplexer_ids=[1], multiplexer_signal='Selector')]
        db = cantools.database.Database(messages=[Message(0x12345, 'MuxFd', 12, signals,
                                                             is_extended_frame=True, is_fd=True)])
        doc = DbcDocument(db)
        _, text = doc.save_snapshot()
        loaded = cantools.database.load_string(text, database_format='dbc')
        msg = loaded.messages[0]
        self.assertTrue(msg.is_fd)
        self.assertTrue(msg.is_extended_frame)
        self.assertEqual(msg.decode(msg.encode({'Selector': 1, 'B': 23})), {'Selector': 1, 'B': 23})
        msg.is_fd = False
        msg.is_extended_frame = False
        msg.frame_id = 0x123
        msg.length = 8
        _, classic_text = DbcDocument(loaded).save_snapshot()
        classic = cantools.database.load_string(classic_text, database_format='dbc').messages[0]
        self.assertFalse(classic.is_fd)
        self.assertFalse(classic.is_extended_frame)


class WindowTests(unittest.TestCase):
    def test_properties_preview_save_and_cancel_close(self):
        source = database()
        w = DbcEditorWindow(source)
        try:
            w.payload.setText('FF F0')
            self.assertIn('Pot = 5.0 V', w.decoded.toPlainText())
            dialog = PropertiesDialog(w.document.database.messages[0].signals[0])
            dialog.fields['name'].setText('Voltage')
            dialog.fields['scale'].setText('0')
            dialog.accept()
            # The containing document rejects invalid scaling transactionally.
            bad = deepcopy(w.document.database)
            bad.messages[0].signals[0] = dialog.result_item
            with self.assertRaises(ValueError):
                w.commit(bad)
            dialog.fields['scale'].setText('0.001221001221001221')
            dialog.accept()
            db = deepcopy(w.document.database)
            db.messages[0].signals[0] = dialog.result_item
            w.commit(db)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel):
                self.assertFalse(w.close())
            self.assertTrue(w.document.dirty)
            self.assertEqual(source.messages[0].signals[0].name, 'Pot')
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)/'edited.dbc'
                self.assertTrue(w.save_file(path=target))
                self.assertFalse(w.document.dirty)
                self.assertEqual(cantools.database.load_file(target).messages[0].signals[0].name, 'Voltage')
        finally:
            w.document.dirty = False
            w.close()

    def test_main_window_live_bus_isolation_and_save_reload(self):
        main = MainWindow()
        channel = 'dbc-editor-isolation'
        main._service.connect_bus(next(b for b in BACKENDS if b.interface == 'virtual'), channel, 500000, False)
        peer = can.Bus(interface='virtual', channel=channel)
        try:
            main._model.set_database(database())
            main._recording_panel.database_path = str(Path('original.dbc').resolve())
            model_database = main._model.database
            w = main._open_dbc_editor()
            self.assertIs(main._open_dbc_editor(), w)
            self.assertIsNot(w.document.database, model_database)
            candidate = deepcopy(w.document.database)
            candidate.messages[0].signals[0].name = 'Voltage'
            w.commit(candidate)
            self.assertIs(main._model.database, model_database)
            self.assertTrue(main._service.is_connected)
            peer.send(can.Message(arbitration_id=577, is_extended_id=False, data=[128]))
            deadline = time.monotonic() + 2
            while main._model.rowCount() == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
                app.processEvents()
            self.assertGreater(main._model.rowCount(), 0)
            main._dbc_editor_saved(str(Path('unrelated.dbc').resolve()), candidate)
            self.assertIs(main._model.database, model_database)
            main._dbc_editor_saved(str(Path('original.dbc').resolve()), candidate)
            self.assertEqual(main._model.database.messages[0].signals[0].name, 'Voltage')
            main._unload_dbc()
            main._dbc_editor_saved(str(Path('original.dbc').resolve()), candidate)
            self.assertIsNone(main._model.database)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel):
                self.assertFalse(main.close())
            self.assertTrue(main._service.is_connected)
            w.document.dirty = False
            w.close()
            self.assertTrue(main._service.is_connected)
        finally:
            peer.shutdown()
            for w in list(main._dbc_windows):
                w.document.dirty = False
            main.close()
