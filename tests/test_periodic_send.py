import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import unittest
from unittest.mock import patch
import uuid
import can
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.bus.backends import BACKENDS

app = QApplication.instance() or QApplication([])

def process_for(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()

class PeriodicSend(unittest.TestCase):
    def setUp(self):
        self.channel = str(uuid.uuid4())
        self.peer = can.Bus(interface="virtual", channel=self.channel)
        self.window = MainWindow()
        self.window._service.connect_bus(BACKENDS[0], self.channel, 500000, False)

    def tearDown(self):
        self.window.close()
        self.peer.shutdown()

    def drain(self):
        frames = []
        while (msg := self.peer.recv(0)) is not None:
            frames.append(msg)
        return frames

    def test_blank_sends_once(self):
        self.window._send_message()
        process_for(130)
        self.assertEqual(len(self.drain()), 1)
        self.assertFalse(self.window._tx_timer.isActive())
        self.assertEqual(self.window._send.text(), "Send")

    def test_repeat_100ms_stop_and_resume_once(self):
        self.window._tx_interval.setText("100")
        self.window._send_message()
        self.assertTrue(self.window._tx_timer.isActive())
        self.assertEqual(self.window._tx_timer.interval(), 100)
        self.assertFalse(self.window._tx_data.isEnabled())
        self.assertTrue(self.window._send.isEnabled())
        self.assertEqual(self.window._send.text(), "Stop")
        process_for(360)
        frames = self.drain()
        self.assertGreaterEqual(len(frames), 3)
        self.assertTrue(all(m.arbitration_id == 0x100 and m.data == frames[0].data for m in frames))
        self.window._send.click()
        process_for(150)
        self.assertEqual(self.drain(), [])
        self.assertTrue(self.window._tx_data.isEnabled())
        self.assertEqual(self.window._send.text(), "Send")
        self.window._tx_interval.clear()
        self.window._send.click()
        self.assertEqual(len(self.drain()), 1)
        self.assertFalse(self.window._tx_timer.isActive())

    def test_invalid_interval_sends_nothing(self):
        with patch("signal_explorer.ui.main_window.QMessageBox.warning") as warning:
            for text in ("0", "-1", "1.5", "abc", "2147483648"):
                self.window._tx_interval.setText(text)
                self.window._send_message()
        self.assertEqual(warning.call_count, 5)
        self.assertEqual(self.drain(), [])
        self.assertFalse(self.window._tx_timer.isActive())

    def test_disconnect_and_close_stop_repeat(self):
        self.window._tx_interval.setText("100")
        self.window._send_message()
        self.drain()
        self.window._service.disconnect_bus()
        self.assertEqual(self.window._send.text(), "Send")
        self.assertFalse(self.window._send.isEnabled())
        process_for(150)
        self.assertEqual(self.drain(), [])
        self.assertFalse(self.window._tx_timer.isActive())
        self.window._service.connect_bus(BACKENDS[0], self.channel, 500000, False)
        self.assertFalse(self.window._tx_timer.isActive())
        self.window._send_message()
        self.window.close()
        self.assertFalse(self.window._tx_timer.isActive())

    def test_failed_first_send_and_failed_repeat_stop(self):
        self.window._tx_interval.setText("100")
        with patch.object(self.window._service, "send_message", return_value=False):
            self.window._send_message()
        self.assertFalse(self.window._tx_timer.isActive())
        self.window._send_message()
        with patch.object(self.window._service, "send_message", return_value=False):
            self.window._send_repeated_message()
        self.assertFalse(self.window._tx_timer.isActive())
        self.assertEqual(self.window._send.text(), "Send")

if __name__ == "__main__":
    unittest.main()
