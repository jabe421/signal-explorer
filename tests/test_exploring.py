import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import unittest
import uuid
import time
import can
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.trace_model import TraceModel
from signal_explorer.bus.backends import BACKENDS

app = QApplication.instance() or QApplication([])

class Exploring(unittest.TestCase):
    def test_exact_columns_and_bus_distinguishes_same_id(self):
        model = TraceModel(history_mode=False)
        headers = [model.headerData(c, Qt.Orientation.Horizontal) for c in range(model.columnCount())]
        self.assertEqual(headers, ['Timestamp', 'Counts', 'Bus Number', 'Message ID (hex)',
                                  'Ext', 'Direction', 'DLC', 'Data', 'DBC Message', 'Signals'])
        for channel in ('COM3', 'PCAN_USBBUS1'):
            model.append_message(can.Message(timestamp=123.5, arbitration_id=256,
                                             is_extended_id=False, channel=channel, data=[170]))
        self.assertEqual(model.rowCount(), 2)
        values = [model.data(model.index(0,c)) for c in range(8)]
        self.assertEqual(values, ['123.500000', '1', 'COM3', '100', '', 'RX', '1', 'AA'])

    def test_pause_resume_keeps_connection_drains_rx_and_allows_send(self):
        window = MainWindow()
        channel = str(uuid.uuid4())
        peer = can.Bus(interface='virtual', channel=channel)
        def wait_for(predicate):
            deadline = time.monotonic()+2
            while not predicate() and time.monotonic()<deadline:
                time.sleep(0.01)
                app.processEvents()
            self.assertTrue(predicate())
        try:
            self.assertEqual(window._explore.text(), 'Start Exploring')
            self.assertFalse(window._explore.isEnabled())
            window._service.connect_bus(BACKENDS[0], channel, 500000, False)
            self.assertEqual(window._explore.text(), 'Pause Exploring')
            peer.send(can.Message(arbitration_id=256, data=[1]))
            wait_for(lambda: window._rx_count == 1)
            window._explore.click()
            self.assertEqual(window._explore.text(), 'Start Exploring')
            received = []
            window._service.message.connect(received.append)
            peer.send(can.Message(arbitration_id=256, data=[2]))
            wait_for(lambda: len(received) == 1)
            self.assertEqual(window._rx_count, 1)
            self.assertEqual(window._model.rowCount(), 1)
            self.assertTrue(window._service.is_connected)
            window._send.click()
            self.assertIsNotNone(peer.recv(0.5))
            self.assertEqual(window._model.rowCount(), 1)
            window._explore.click()
            peer.send(can.Message(arbitration_id=256, data=[3]))
            wait_for(lambda: window._rx_count == 2)
            self.assertEqual(window._model.rowCount(), 2)
            self.assertEqual(window._model.data(window._model.index(1,7)), '03')
            window._service.disconnect_bus()
            self.assertEqual(window._explore.text(), 'Start Exploring')
            self.assertFalse(window._explore.isEnabled())
        finally:
            window.close()
            peer.shutdown()

if __name__ == '__main__':
    unittest.main()
