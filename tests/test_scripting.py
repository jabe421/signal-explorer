import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import can
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QMessageBox
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.demo import demo_reply
from signal_explorer.bus.service import BusService
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.scripting.controller import ScriptController
from signal_explorer.scripting.transport import CanScriptTransport
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.script_window import ScriptWindow
from signal_explorer.ui.python_editor import PythonEditor

app=QApplication.instance() or QApplication([])
VIRTUAL=next(b for b in BACKENDS if b.interface=='virtual')
DEMO=Path('examples/scripts/can_demo_test.py').resolve()


def wait_for(predicate,timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return True
        time.sleep(.005)
    return False


class ScriptingTests(unittest.TestCase):
    def setUp(self):
        self.service=BusService();self.channel='scripts-'+uuid.uuid4().hex
        self.transport=CanScriptTransport(self.service)
        self.controller=ScriptController(self.transport)
        self.output=[];self.lines=[];self.events=[]
        self.controller.output.connect(self.output.append)
        self.controller.line_changed.connect(self.lines.append)
        self.service.traffic.connect(self.events.append)

    def tearDown(self):
        self.controller.stop()
        self.assertTrue(wait_for(lambda:not self.controller.active))
        self.service.disconnect_bus();app.processEvents()

    def connect(self,demo=False,fd=False):
        self.service.connect_bus(VIRTUAL,self.channel,500000,demo,fd=fd)
        self.assertTrue(self.service.is_connected)

    def run_source(self,source,filename='<script test>',**kwargs):
        self.controller.start(source,filename,**kwargs)
        self.assertTrue(wait_for(lambda:not self.controller.active),''.join(self.output))
        return self.controller.outcome

    def test_requires_connected_bus_and_one_active_script(self):
        with self.assertRaisesRegex(RuntimeError,'Connect'):self.controller.start('pass')
        self.connect();self.controller.start('se.sleep(10000)')
        with self.assertRaisesRegex(RuntimeError,'Another script'):self.controller.start('pass')
        self.controller.stop();self.assertTrue(wait_for(lambda:not self.controller.active))
        self.assertEqual(self.controller.outcome,'stopped')

    def test_demo_classic_and_fd_use_shared_service_and_keep_bus_connected(self):
        self.assertEqual(DEMO.read_text(), Path('src/signal_explorer/scripting/demo_can.py').read_text())
        for fd in (False,True):
            with self.subTest(fd=fd):
                self.events.clear()
                self.connect(True,fd)
                self.controller.start(DEMO.read_text(),str(DEMO))
                self.assertTrue(wait_for(lambda:sum(m.arbitration_id==0x221 for m in self.events)>=3), ''.join(self.output))
                self.assertTrue(self.controller.active)
                requests=[m for m in self.events if m.arbitration_id==0x321]
                replies=[m for m in self.events if m.arbitration_id==0x221]
                for index,reply in enumerate(replies):
                    self.assertTrue(reply.is_rx)
                    self.assertFalse(requests[index].is_rx)
                    self.assertEqual(bytes(reply.data),bytes((value+index)&255 for value in range(8)))
                    self.assertEqual(reply.data,requests[index].data)
                self.controller.pause();self.assertTrue(wait_for(lambda:self.controller.state=='paused'))
                sent=len([m for m in self.events if m.arbitration_id==0x321])
                received=len(self.events)
                self.assertTrue(wait_for(lambda:len(self.events)>received+4))
                self.assertEqual(sum(m.arbitration_id==0x321 for m in self.events),sent)
                self.controller.resume()
                self.assertTrue(wait_for(lambda:sum(m.arbitration_id==0x321 for m in self.events)>sent))
                self.controller.stop();self.assertTrue(wait_for(lambda:not self.controller.active))
                self.assertEqual(self.controller.outcome,'stopped')
                sent=sum(m.arbitration_id==0x321 for m in self.events)
                wait_for(lambda:False,.25)
                self.assertEqual(sum(m.arbitration_id==0x321 for m in self.events),sent)
                self.assertTrue(self.service.is_connected)
                self.assertTrue(any(m.arbitration_id==0x200 for m in self.events))
                if fd:self.assertTrue(any(m.arbitration_id==0x300 and m.is_fd for m in self.events))
                self.service.disconnect_bus()

    def test_demo_echo_requires_injection_and_preserves_fd_payload(self):
        self.connect()
        self.assertEqual(self.run_source('se.can.send(0x321,bytes(8))\nf=se.can.recv(.25,arbitration_id=0x221)\nse.check(f is None,"No peer without injection")'),'passed')
        self.service.disconnect_bus();self.connect(True,True)
        self.assertEqual(self.run_source('payload=bytes([255,0,128,1,254,2,253,3])\nse.can.send(0x321,payload,fd=True,brs=True)\nf=se.can.recv(2,arbitration_id=0x221)\nse.check(f is not None and f.data==payload and f.is_fd and f.bitrate_switch,"Peer echoes FD flags and data")'),'passed')

    def test_demo_peer_ignores_other_ids_and_invalid_requests(self):
        for change in (dict(arbitration_id=0x221), dict(is_extended_id=True),
                       dict(is_remote_frame=True), dict(is_error_frame=True),
                       dict(data=bytes(7)), dict(dlc=7)):
            with self.subTest(change=change):
                fields=dict(arbitration_id=0x321,is_extended_id=False,data=bytes(8))
                fields.update(change)
                self.assertIsNone(demo_reply(can.Message(**fields)))

    def test_demo_script_byte_increment_wraps(self):
        self.connect(True)
        source=DEMO.read_text().replace('payload = bytes(range(8))','payload = bytes([254, 255, 0, 1, 2, 3, 4, 5])')
        self.controller.start(source,str(DEMO))
        self.assertTrue(wait_for(lambda:sum(m.arbitration_id==0x221 for m in self.events)>=3))
        replies=[bytes(m.data) for m in self.events if m.arbitration_id==0x221]
        self.assertEqual(replies[:3],[bytes([254,255,0,1,2,3,4,5]),bytes([255,0,1,2,3,4,5,6]),bytes([0,1,2,3,4,5,6,7])])

    def test_pause_freezes_script_tx_but_monitor_receives_then_resume(self):
        self.connect(True)
        self.controller.start('for i in range(50):\n    se.can.send(0x555,[i])\n    se.sleep(40)\n')
        self.assertTrue(wait_for(lambda:sum(m.arbitration_id==0x555 for m in self.events)>=2))
        self.controller.pause();self.assertTrue(wait_for(lambda:self.controller.state=='paused'))
        sent=sum(m.arbitration_id==0x555 for m in self.events);received=len(self.events)
        self.assertTrue(wait_for(lambda:len(self.events)>received+4))
        self.assertEqual(sum(m.arbitration_id==0x555 for m in self.events),sent)
        self.assertTrue(self.lines);self.assertTrue(self.controller.active)
        self.controller.resume()
        self.assertTrue(wait_for(lambda:sum(m.arbitration_id==0x555 for m in self.events)>sent))

    def test_pending_receive_timeout_excludes_pause(self):
        self.connect()
        self.controller.start('frame=se.can.recv(timeout=0.25,arbitration_id=0x567)\nse.check(frame is None,"Timeout occurred")\n')
        self.assertTrue(wait_for(lambda:self.controller._pending is not None))
        self.controller.pause();self.assertTrue(wait_for(lambda:self.controller.state=='paused'))
        wait_for(lambda:False,.4)
        self.assertTrue(self.controller.active)
        self.controller.resume();self.assertTrue(wait_for(lambda:not self.controller.active))
        self.assertEqual(self.controller.outcome,'passed')

    def test_nonblocking_receive_searches_existing_mailbox_and_input_does_not_steal_control(self):
        self.connect()
        self.controller.start('se.log("queue ready")\nse.sleep(200)\nf=se.can.recv(0,arbitration_id=0x567)\nse.check(f is not None,"Find queued frame")')
        self.assertTrue(wait_for(lambda:'queue ready' in ''.join(self.output)))
        for i in range(300):self.service.traffic.emit(can.Message(arbitration_id=0x200,data=[i%256],is_extended_id=False))
        self.service.traffic.emit(can.Message(arbitration_id=0x567,data=[1],is_extended_id=False))
        self.assertTrue(wait_for(lambda:not self.controller.active));self.assertEqual(self.controller.outcome,'passed')
        self.assertEqual(self.run_source('input("Enter a value")'),'failed')
        self.assertIn('EOFError',''.join(self.output))

    def test_stop_pending_receive_and_blocking_native_call(self):
        self.connect()
        for source in ('se.can.recv(timeout=None)\nse.can.send(0x555,[1])',
                       'import time\nse.log("native sleep")\ntime.sleep(30)\nse.can.send(0x555,[1])'):
            self.output.clear();self.controller.start(source)
            self.assertTrue(wait_for(lambda:self.controller._pending is not None or 'native sleep' in ''.join(self.output)))
            self.controller.stop();self.assertTrue(wait_for(lambda:not self.controller.active,3))
            self.assertEqual(self.controller.outcome,'stopped')
            self.assertFalse(any(m.arbitration_id==0x555 for m in self.events))
            self.assertTrue(self.service.is_connected)

    def test_stop_infinite_python_loop_and_immediate_restart(self):
        self.connect()
        self.controller.start('while True:\n    pass\n')
        self.assertTrue(wait_for(lambda:self.controller.state=='running'))
        self.controller.stop();self.assertTrue(wait_for(lambda:not self.controller.active,3))
        self.assertEqual(self.run_source('se.check(True,"New process")'),'passed')

    def test_exceptions_syntax_errors_exit_and_trace_line(self):
        self.connect()
        self.assertEqual(self.run_source('value=1\nse.check(False,"bad measurement")\n'),'failed')
        self.assertEqual(self.controller.line,2);self.assertIn('bad measurement',''.join(self.output))
        self.assertEqual(self.run_source('value =\n'),'failed');self.assertEqual(self.controller.line,1)
        self.assertEqual(self.run_source('raise SystemExit(0)'),'completed')
        self.assertEqual(self.run_source('raise SystemExit(1)'),'failed')
        self.assertEqual(self.run_source('try:\n    se.check(False,"Collect a failure")\nexcept AssertionError:\n    pass\nse.check(True,"Continue testing")'),'failed')
        self.assertEqual(self.controller.line,2)

    def test_multiple_editors_one_owner_and_editor_close_keeps_monitor_open(self):
        window=MainWindow();window.show();window._service.connect_bus(VIRTUAL,self.channel,500000,False)
        first=window._open_script(new=True);second=window._open_script(new=True)
        try:
            first.editor.setPlainText('se.sleep(20000)');first.editor.document().setModified(False);first._run()
            self.assertTrue(wait_for(lambda:window._script_controller.state=='running'))
            self.assertFalse(second.run.isEnabled());self.assertFalse(second.editor.isReadOnly())
            self.assertFalse(first.close())
            self.assertTrue(wait_for(lambda:not window._script_controller.active))
            self.assertTrue(window.isVisible());self.assertTrue(window._service.is_connected)
            self.assertTrue(second.run.isEnabled())
        finally:
            window._script_controller.stop();wait_for(lambda:not window._script_controller.active)
            for editor in list(window._script_windows):editor.editor.document().setModified(False)
            window.close();app.processEvents()

    def test_fd_validation_and_no_fake_hardware_ack(self):
        self.connect()
        self.assertEqual(self.run_source('se.can.send(0x123,bytes(12),fd=True)'),'failed')
        self.assertFalse(any(not m.is_rx for m in self.events))
        self.assertEqual(self.run_source('se.can.send(0x123,b"hello")\nf=se.can.recv(.1,arbitration_id=0x123)\nse.check(f is None,"No artificial RX")'),'passed')
        self.service.disconnect_bus();self.connect(fd=True)
        self.assertEqual(self.run_source('se.can.send(0x123,bytes(12),fd=True,brs=True)'),'completed')
        self.assertTrue(any(m.is_fd and m.bitrate_switch and not m.is_rx for m in self.events))

    def test_disconnect_stops_script_and_no_old_worker_controls_new_session(self):
        self.connect();self.controller.start('se.sleep(20000)\nse.can.send(0x555,[1])')
        self.assertTrue(wait_for(lambda:self.controller.state=='running'))
        self.service.disconnect_bus();self.assertTrue(wait_for(lambda:not self.controller.active))
        self.assertEqual(self.controller.outcome,'stopped')
        self.connect();self.assertEqual(self.run_source('se.log("Fresh session")'),'completed')
        self.assertFalse(any(m.arbitration_id==0x555 for m in self.events))

    def test_receive_overflow_fails_explicitly_and_simulation_guard_prevents_tx(self):
        self.connect()
        with patch.object(self.controller,'MAX_RECEIVE',2):self.controller.start('se.sleep(20000)')
        self.assertTrue(wait_for(lambda:self.controller.state=='running'))
        for i in range(5):self.service.traffic.emit(can.Message(arbitration_id=0x200,data=[i],is_extended_id=False))
        self.assertTrue(wait_for(lambda:not self.controller.active))
        self.assertEqual(self.controller.outcome,'failed');self.assertIn('overflow',''.join(self.output))
        self.assertTrue(self.service.is_connected)
        with patch.object(self.transport,'session',return_value=dict(protocol='CAN',simulated=False)):
            self.assertEqual(self.run_source(DEMO.read_text(),str(DEMO)),'failed')
        self.assertFalse(any(not m.is_rx for m in self.events))

    def test_saved_script_local_import_and_output_are_isolated(self):
        self.connect()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'helper.py').write_text('VALUE = 42\n')
            source='import helper\nprint("answer",helper.VALUE)\nse.check(helper.VALUE==42,"Local import")\n'
            self.assertEqual(self.run_source(source,str(path/'test.py')),'passed')
            self.assertIn('answer 42',''.join(self.output))
        self.assertEqual(self.run_source('se.check("helper" not in globals(),"Fresh namespace")'),'passed')

    def test_script_receive_and_tx_record_while_main_monitor_paused(self):
        window=MainWindow();window._service.connect_bus(VIRTUAL,self.channel,500000,True)
        window._exploring=False
        c=window._script_controller;recording=window._recording_panel
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'script.mf4'
            try:
                recording.log.click()
                c.start('f=se.can.recv(2,arbitration_id=0x200)\nse.check(f is not None,"RX despite paused monitor")\nse.can.send(0x456,[1,2])')
                self.assertTrue(wait_for(lambda:not c.active));self.assertEqual(c.outcome,'passed')
                self.assertEqual(window._model.rowCount(),0)
                with patch('signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName',return_value=(str(path),'')):
                    recording.log.click();self.assertTrue(wait_for(lambda:path.exists()))
                with can.MF4Reader(path) as reader:frames=list(reader)
                self.assertTrue(any(m.arbitration_id==0x200 and m.is_rx for m in frames))
                self.assertTrue(any(m.arbitration_id==0x456 and not m.is_rx for m in frames))
            finally:
                c.stop();wait_for(lambda:not c.active);window.close()

    def test_editor_save_unicode_find_replace_colors_and_unsaved_cancel(self):
        editor=ScriptWindow(self.controller)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.py';path.write_text('# RPM café\nfor i in range(3):\n    print("hello", i)\n',encoding='utf-8')
            try:
                self.assertTrue(editor.open_script(path));app.processEvents()
                formats=editor.editor.document().findBlockByNumber(1).layout().formats()
                self.assertTrue(any(f.format.foreground().color().name()=='#7c3aab' for f in formats))
                editor.find_text.setText('hello');editor.replace_text.setText('voltage');editor._replace_all()
                self.assertTrue(editor.editor.document().isModified());self.assertTrue(editor.save_script())
                self.assertIn('voltage',path.read_text(encoding='utf-8'));self.assertIn('café',path.read_text(encoding='utf-8'))
                editor.editor.insertPlainText('# changed\n')
                with patch('signal_explorer.ui.script_window.QMessageBox.question',return_value=QMessageBox.StandardButton.Cancel):
                    self.assertFalse(editor.close());self.assertFalse(editor._closed)
            finally:editor.editor.document().setModified(False);editor.close()

    def test_editor_execution_highlight_readonly_pause_stop_controls(self):
        self.connect();editor=ScriptWindow(self.controller)
        try:
            editor.editor.setPlainText('for i in range(30):\n    se.log(i)\n    se.sleep(100)\n')
            editor.editor.document().setModified(False);editor._run()
            self.assertTrue(wait_for(lambda:editor.editor.execution_line>0))
            self.assertTrue(editor.editor.isReadOnly());self.assertFalse(editor.run.isEnabled())
            editor.pause.click();self.assertTrue(wait_for(lambda:self.controller.state=='paused'))
            self.assertEqual(editor.pause.text(),'Resume');self.assertTrue(editor.editor.extraSelections())
            editor.stop.click();self.assertTrue(wait_for(lambda:not self.controller.active))
            self.assertFalse(editor.editor.isReadOnly());self.assertTrue(editor.run.isEnabled())
            self.assertIn('STOPPED',editor.console.toPlainText())
        finally:
            self.controller.stop();wait_for(lambda:not self.controller.active)
            editor.editor.document().setModified(False);editor.close()

    def test_main_window_close_waits_for_script_before_disconnecting(self):
        window=MainWindow();window.show();window._service.connect_bus(VIRTUAL,self.channel,500000,False)
        editor=window._open_script(new=True)
        editor.editor.setPlainText('se.sleep(20000)');editor.editor.document().setModified(False);editor._run()
        self.assertTrue(wait_for(lambda:window._script_controller.state=='running'))
        self.assertFalse(window.close());self.assertTrue(window._service.is_connected)
        self.assertTrue(wait_for(lambda:not window.isVisible() and not window._script_controller.active))
        self.assertFalse(window._service.is_connected);app.processEvents()


if __name__=='__main__':unittest.main()
