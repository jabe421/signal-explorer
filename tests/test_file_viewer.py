import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import can
from PySide6.QtWidgets import QApplication, QMessageBox

from signal_explorer.bus.backends import BACKENDS
from signal_explorer.recording.codecs import CANCodec
from signal_explorer.recording.viewer import LogLoader
from signal_explorer.lin.codec import LINCodec
from signal_explorer.lin.model import LinFrame
from signal_explorer.ethernet.codec import EthernetCodec
from signal_explorer.ethernet.model import preset
from dataclasses import replace
from signal_explorer.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
VIRTUAL = next(b for b in BACKENDS if b.interface == 'virtual')


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return False


class FileViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow()
        self.window.show()
        self.warnings = patch.object(QMessageBox, 'warning', return_value=QMessageBox.StandardButton.Ok)
        self.warnings.start()
        self.sent = []
        self.window._service.traffic.connect(lambda msg:self.sent.append((time.monotonic(),msg)) if not msg.is_rx else None)

    def tearDown(self):
        for viewer in list(self.window._script_windows):
            viewer.controller.stop()
            self.assertTrue(wait_for(lambda:not viewer.controller.active))
            viewer.editor.document().setModified(False)
        self.window.close();app.processEvents()
        self.warnings.stop()
        self.temp.cleanup()

    def connect(self, fd=False):
        self.window._service.connect_bus(VIRTUAL, 'viewer-'+uuid.uuid4().hex, 500000, False, fd=fd)

    def logfile(self, gaps=(0,.15,.35), fd=False):
        path = Path(self.temp.name)/'test.mf4'
        now = time.time()
        with can.MF4Writer(path) as writer:
            for index,gap in enumerate(gaps):
                writer.on_message_received(can.Message(timestamp=now+gap,channel=1,is_rx=index!=1,
                    arbitration_id=0x120+index,is_extended_id=False,is_fd=fd,bitrate_switch=fd,
                    data=bytes(range(12)) if fd else bytes([index,2,3])))
        return path

    def open(self,path):
        viewer = self.window._open_logfile(path)
        self.assertIsNotNone(viewer)
        self.assertTrue(wait_for(lambda:not viewer.controller.active))
        self.assertIsNotNone(viewer.controller.loaded_path,viewer.console.toPlainText())
        return viewer

    def test_menu_and_read_only_log_mode_without_connection(self):
        titles = [action.text() for action in self.window.menuBar().actions()]
        self.assertIn('File Viewer',titles);self.assertNotIn('Scripts',titles)
        self.assertIn('Open Logfile',[a.text() for a in self.window.menuBar().actions()[0].menu().actions()])
        viewer = self.open(self.logfile())
        self.assertEqual(viewer.output_label.text(),'Replay Output')
        self.assertTrue(viewer.editor.isReadOnly())
        self.assertFalse(viewer.save_action.isEnabled());self.assertFalse(viewer.save_as_action.isEnabled())
        self.assertFalse(viewer.run.isEnabled())
        self.assertEqual(viewer.editor.blockCount(),3)
        self.assertIn('120',viewer.editor.toPlainText());self.assertIn('RX',viewer.editor.toPlainText())
        self.assertTrue(viewer.follow.isChecked());self.assertTrue(viewer.delay.isEnabled())
        self.connect();self.assertTrue(viewer.run.isEnabled())
        script = self.window._open_script(new=True)
        self.assertEqual(script.output_label.text(),'Script Output')

    def test_replay_directions_original_timing_and_display_delay(self):
        self.connect()
        viewer = self.open(self.logfile())
        for direction, ids, rows in (('rx',[0x120,0x122],[1,3]),('tx',[0x121],[2]),
                                     ('all',[0x120,0x121,0x122],[1,2,3])):
            with self.subTest(direction=direction):
                self.sent.clear()
                viewer.direction.setCurrentIndex(viewer.direction.findData(direction))
                viewer.delay.setValue(1000)
                viewer.follow.setChecked(False)
                viewer._run()
                self.assertTrue(wait_for(lambda:not viewer.controller.active),viewer.console.toPlainText())
                self.assertEqual(viewer.controller.outcome,'completed')
                self.assertEqual([m.arbitration_id for _,m in self.sent],ids)
                self.assertEqual(viewer.controller.line,rows[-1])
                self.assertEqual(viewer.editor.execution_line,rows[-1])
                self.assertEqual(viewer.editor.textCursor().blockNumber(),0)
                self.assertIn('Playback complete',viewer.console.toPlainText())
                if len(self.sent)>1:
                    self.assertAlmostEqual(self.sent[-1][0]-self.sent[0][0],.35,delta=.10)

    def test_pause_resume_keeps_remaining_gaps_and_stop_leaves_connection_open(self):
        self.connect()
        viewer = self.open(self.logfile((0,.4,.7)))
        viewer._run()
        self.assertTrue(wait_for(lambda:len(self.sent)==1))
        viewer.pause.click()
        self.assertTrue(wait_for(lambda:viewer.controller.state=='paused'))
        self.assertEqual(viewer.pause.text(),'Resume')
        wait_for(lambda:False,.3)
        self.assertEqual(len(self.sent),1)
        viewer.pause.click()
        self.assertTrue(wait_for(lambda:len(self.sent)>=2))
        self.assertGreaterEqual(self.sent[1][0]-self.sent[0][0],.65)
        viewer.stop.click()
        self.assertTrue(wait_for(lambda:not viewer.controller.active))
        self.assertEqual(viewer.controller.outcome,'stopped')
        self.assertTrue(self.window._service.is_connected)
        wait_for(lambda:False,.4)
        self.assertEqual(len(self.sent),2)

    def test_shared_playback_ownership_and_disconnect(self):
        self.connect()
        path=self.logfile((0,30))
        first=self.open(path);second=self.open(path)
        first._run()
        self.assertTrue(wait_for(lambda:len(self.sent)==1))
        self.assertFalse(second.run.isEnabled())
        self.assertTrue(self.window._recording_panel.playback_busy)
        self.assertEqual(self.window._recording_panel.replay.text(),'Stop Playback')
        self.window._service.disconnect_bus()
        self.assertTrue(wait_for(lambda:not first.controller.active))
        self.assertEqual(first.controller.outcome,'stopped')
        self.assertEqual(len(self.sent),1)

    def test_main_close_stops_pending_replay_before_disconnect(self):
        self.connect()
        viewer=self.open(self.logfile((0,30)))
        viewer._run();self.assertTrue(wait_for(lambda:len(self.sent)==1))
        self.assertFalse(self.window.close())
        self.assertTrue(wait_for(lambda:not self.window.isVisible()))
        self.assertFalse(self.window._service.is_connected)

    def test_invalid_or_fd_incompatible_logs_send_nothing(self):
        path=Path(self.temp.name)/'bad.mf4';path.write_bytes(b'not an MDF file')
        viewer=self.window._open_logfile(path)
        self.assertTrue(wait_for(lambda:not viewer.controller.active))
        self.assertFalse(viewer.run.isEnabled());self.assertIn('Open logfile failed',viewer.console.toPlainText())
        self.connect()
        viewer=self.open(self.logfile(fd=True))
        viewer._run();self.assertTrue(wait_for(lambda:not viewer.controller.active))
        self.assertEqual(viewer.controller.outcome,'failed');self.assertFalse(self.sent)

    def test_loader_cap_does_not_limit_replay(self):
        self.connect()
        with patch.object(LogLoader,'MAX_ROWS',1):
            viewer=self.open(self.logfile((0,.02,.04)))
        self.assertEqual(viewer.editor.blockCount(),1)
        self.assertIn('Display limited',viewer.console.toPlainText())
        viewer._run();self.assertTrue(wait_for(lambda:not viewer.controller.active))
        self.assertEqual(len(self.sent),3)

    def test_ethernet_detection_and_simulated_replay(self):
        path=Path(self.temp.name)/'ethernet.mf4'
        codec=EthernetCodec();writer=codec.writer(path)
        frame=replace(preset('UDP telemetry'),timestamp=time.time(),direction='rx')
        writer.write(codec.capture(frame));writer.finish({})
        service=self.window._ethernet.service
        service.connect_simulated(False,False)
        sent=[];service.traffic.connect(sent.append)
        viewer=self.open(path)
        self.assertEqual(viewer.controller.transport.protocol,'ETHERNET')
        viewer._run();self.assertTrue(wait_for(lambda:not viewer.controller.active))
        self.assertEqual(viewer.controller.outcome,'completed',viewer.console.toPlainText())
        self.assertEqual(len(sent),1);self.assertEqual(sent[0].raw,frame.raw)
        self.assertEqual(sent[0].direction,'tx')
        self.assertFalse(self.sent)

    def test_lin_detection_replays_master_headers_and_rejects_monitor_role(self):
        path=Path(self.temp.name)/'lin.mf4'
        codec=LINCodec();writer=codec.writer(path)
        writer.write(codec.capture(LinFrame(time.time(),16,direction='rx',kind='header')))
        writer.finish({})
        service=self.window._lin.service
        service.connect_simulated('monitor',19200,False)
        viewer=self.open(path)
        self.assertEqual(viewer.controller.transport.protocol,'LIN')
        self.assertFalse(viewer.run.isEnabled())
        service.disconnect_bus();service.connect_simulated('master',19200,False)
        events=[];service.traffic.connect(events.append)
        viewer._run();self.assertTrue(wait_for(lambda:not viewer.controller.active))
        self.assertEqual(viewer.controller.outcome,'completed',viewer.console.toPlainText())
        self.assertTrue(wait_for(lambda:any(frame.kind=='header' for frame in events)))
        self.assertEqual([frame.frame_id for frame in events if frame.kind=='header'],[16])
        self.assertFalse(self.sent)


if __name__ == '__main__':
    unittest.main()
