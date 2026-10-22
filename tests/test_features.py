import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import unittest
from pathlib import Path
import uuid
import can
import cantools
from PySide6.QtWidgets import QApplication
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.trace_model import TraceModel, IdFilter
from signal_explorer.bus.service import BusService
from signal_explorer.bus.backends import BACKENDS

app = QApplication.instance() or QApplication([])

class Features(unittest.TestCase):
    def test_latest_frame_and_format_separation(self):
        model = TraceModel(history_mode=False)
        model.append_message(can.Message(arbitration_id=256, is_extended_id=False, data=[1]))
        model.append_message(can.Message(arbitration_id=256, is_extended_id=False, data=[2]))
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.data(model.index(0, 1)), '2')
        self.assertEqual(model.data(model.index(0, 7)), '02')
        model.append_message(can.Message(arbitration_id=256, is_extended_id=True, data=[]))
        self.assertEqual(model.rowCount(), 2)
        model.clear()
        self.assertEqual(model.rowCount(), 0)

    def test_filters_restore_hidden_messages_and_invalid_keeps_filter(self):
        model = TraceModel(history_mode=False)
        proxy = IdFilter()
        proxy.setSourceModel(model)
        for identifier in (256, 512, 768):
            model.append_message(can.Message(arbitration_id=identifier))
        proxy.set_expression('100, 200-2FF')
        self.assertEqual(proxy.rowCount(), 2)
        with self.assertRaises(ValueError):
            proxy.set_expression('200-100')
        self.assertEqual(proxy.rowCount(), 2)
        proxy.set_expression('')
        self.assertEqual(proxy.rowCount(), 3)

    def test_dbc_existing_frames_and_short_payload(self):
        model = TraceModel(history_mode=False)
        model.append_message(can.Message(arbitration_id=256, is_extended_id=False, data=[5, 65, 0, 0, 0, 0, 0, 0]))
        model.set_database(cantools.database.load_file(str(Path('examples/demo.dbc'))))
        self.assertEqual(model.data(model.index(0, 8)), 'DemoStatus')
        self.assertIn('Temperature=25 degC', model.data(model.index(0, 9)))
        model.append_message(can.Message(arbitration_id=256, is_extended_id=False, data=[5]))
        self.assertIn('Decode error', model.data(model.index(0, 9)))
        model.set_database(None)
        self.assertEqual(model.data(model.index(0, 8)), '')

    def test_history_switch_preserves_data_filters_and_timestamps(self):
        model = TraceModel(max_history=3)
        proxy = IdFilter()
        proxy.setSourceModel(model)
        proxy.set_expression('100')
        for identifier, value in ((256, 1), (512, 2), (256, 3), (256, 4)):
            model.append_message(can.Message(arbitration_id=identifier, is_extended_id=False,
                                             data=[value], timestamp=float(value)))
        self.assertTrue(model.history_mode)
        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(proxy.rowCount(), 2)
        self.assertEqual(model.data(model.index(0, 0)), '2.000000')
        model.set_history_mode(False)
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(model.data(model.index(0, 7)), '04')
        model.append_message(can.Message(arbitration_id=256, is_extended_id=False, data=[5], timestamp=5))
        model.set_history_mode(True)
        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(model.data(model.index(2, 0)), '5.000000')
        self.assertEqual(model.data(model.index(2, 1)), '4')
        model.clear()
        model.set_history_mode(False)
        self.assertEqual(model.rowCount(), 0)
        model.set_history_mode(True)
        self.assertEqual(model.rowCount(), 0)

    def test_checkbox_defaults_to_history_and_controls_scrolling(self):
        from unittest.mock import patch
        window = MainWindow()
        try:
            window._set_exploring(True)
            self.assertTrue(window._auto_scroll.isChecked())
            with patch.object(window._table, 'scrollToBottom') as scroll:
                window._on_message(can.Message(arbitration_id=256, data=[1]))
                self.assertEqual(scroll.call_count, 1)
                window._auto_scroll.setChecked(False)
                window._on_message(can.Message(arbitration_id=256, data=[2]))
                self.assertEqual(scroll.call_count, 1)
                self.assertEqual(window._model.rowCount(), 1)
                window._auto_scroll.setChecked(True)
                self.assertEqual(window._model.rowCount(), 2)
                self.assertEqual(scroll.call_count, 2)
        finally:
            window.close()

    def test_send_reaches_virtual_peer_and_echo_once(self):
        channel = str(uuid.uuid4())
        peer = can.Bus(interface='virtual', channel=channel)
        service = BusService()
        seen = []
        service.message.connect(seen.append)
        try:
            service.connect_bus(BACKENDS[0], channel, 500000, False)
            self.assertTrue(service.send_message(can.Message(arbitration_id=256, is_extended_id=False, data=[1, 2])))
            received = peer.recv(1)
            self.assertIsNotNone(received)
            self.assertEqual(bytes(received.data), b'\x01\x02')
            self.assertEqual(len(seen), 1)
            self.assertFalse(seen[0].is_rx)
        finally:
            service.disconnect_bus()
            peer.shutdown()

    def test_window_send_updates_same_row(self):
        window = MainWindow()
        window._auto_scroll.setChecked(False)
        try:
            window._service.connect_bus(BACKENDS[0], str(uuid.uuid4()), 500000, False)
            window._send_message()
            window._tx_data.setText('AA BB')
            window._send_message()
            self.assertEqual(window._model.rowCount(), 1)
            self.assertEqual(window._model.data(window._model.index(0, 7)), 'AA BB')
            window._clear_trace()
            self.assertEqual(window._frame_count, 0)
        finally:
            window.close()

if __name__ == '__main__':
    unittest.main()
