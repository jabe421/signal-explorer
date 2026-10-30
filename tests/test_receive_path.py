import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import threading
import time
import unittest
from unittest.mock import patch
from dataclasses import replace
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import _Reader

app = QApplication.instance() or QApplication([])

class FakeSerial:
    def __init__(self):
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.commands = []
    @property
    def in_waiting(self):
        with self.lock:
            return len(self.buffer)
    def read(self, size):
        with self.lock:
            result = bytes(self.buffer[:size])
            del self.buffer[:size]
            return result
    def write(self, data):
        self.commands.append(data)
        return len(data)
    def flush(self):
        pass
    def close(self):
        pass
    def inject(self, data):
        with self.lock:
            self.buffer.extend(data)

class ReceivePath(unittest.TestCase):
    def test_slcan_serial_frames_reach_both_views_and_filter_clear(self):
        window = MainWindow()
        fake = FakeSerial()
        backend = replace(next(b for b in BACKENDS if b.key == 'slcan'),
                          extra_kwargs={'sleep_after_open': 0, 'tty_baudrate': 115200})
        try:
            with patch('serial.serial_for_url', return_value=fake):
                window._service.connect_bus(backend, 'COM3', 500000, False)
            self.assertTrue(window._service.is_connected)
            fake.inject(b't10020102\rt1002AABB\rt2001CC\r')
            deadline = time.monotonic()+2
            while window._rx_count < 3 and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertEqual(window._rx_count, 3)
            self.assertEqual(window._tx_count, 0)
            self.assertEqual(window._proxy.rowCount(), 3)
            self.assertEqual(window._model.data(window._model.index(1, 7)), 'AA BB')
            window._auto_scroll.setChecked(False)
            self.assertEqual(window._proxy.rowCount(), 2)
            self.assertEqual(window._model.data(window._model.index(0, 7)), 'AA BB')
            window._filter.setText('555')
            window._apply_filter()
            self.assertEqual(window._proxy.rowCount(), 0)
            self.assertIn('RX 3', window._count.text())
            self.assertIn('ID filter active', window._count.text())
            window._clear_filter()
            self.assertEqual(window._proxy.rowCount(), 2)
            self.assertNotIn('ID filter active', window._count.text())
            window._clear_trace()
            self.assertEqual(window._rx_count, 0)
        finally:
            window.close()

    def test_unexpected_receive_failure_is_reported(self):
        class BadBus:
            def recv(self, timeout):
                raise ValueError('invalid serial frame')
        reader = _Reader(BadBus())
        errors = []
        reader.failed.connect(errors.append)
        reader.run()
        self.assertEqual(len(errors), 1)
        self.assertIn('Receive failed (ValueError)', errors[0])

if __name__ == '__main__':
    unittest.main()
