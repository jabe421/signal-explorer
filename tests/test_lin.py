import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from asammdf import MDF
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from signal_explorer.lin.model import protected_id,lin_checksum,LinFrame,Response,ScheduleSlot,validate_schedule
from signal_explorer.lin.simulation import SimulatedLinBus
from signal_explorer.lin.database import LinDatabase
from signal_explorer.lin.service import LinService
from signal_explorer.lin.codec import LINCodec
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.recording.playback import MF4Playback
from signal_explorer.ui.main_window import MainWindow

app=QApplication.instance() or QApplication([])
def wait_for(predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        time.sleep(.01);app.processEvents()
        if predicate():return True
    return False

class LINTests(unittest.TestCase):
    def test_pid_and_classic_enhanced_checksum_vectors(self):
        self.assertEqual([protected_id(i) for i in (0,16,32,60,61)],[0x80,0x50,0x20,0x3c,0x7d])
        for identifier in range(64):self.assertEqual(protected_id(identifier)&63,identifier)
        self.assertEqual(lin_checksum(16,b'\x01\x02','classic'),0xfc)
        self.assertEqual(lin_checksum(16,b'\x01\x02','enhanced'),0xac)
        self.assertEqual(lin_checksum(60,bytes(range(1,9)),'enhanced'),0xdb)
        with self.assertRaises(ValueError):protected_id(64)
        with self.assertRaises(ValueError):lin_checksum(1,bytes(9))

    def test_master_slave_monitor_exchange_and_single_master(self):
        bus=SimulatedLinBus();master_events=[];slave_events=[];monitor_events=[]
        master=bus.attach('M','master',master_events.append)
        slave=bus.attach('S','slave',slave_events.append)
        monitor=bus.attach('O','monitor',monitor_events.append)
        with self.assertRaises(ValueError):bus.attach('M2','master',lambda e:None)
        slave.set_response(32,Response(bytes([1,2,3,4])))
        self.assertEqual(slave_events,[]) # Configuration is not an unsolicited TX.
        master.header(32)
        self.assertEqual([e.kind for e in master_events],['header','frame'])
        self.assertEqual([e.direction for e in master_events],['tx','rx'])
        self.assertEqual([e.direction for e in slave_events],['rx','tx'])
        self.assertTrue(all(e.direction=='rx' for e in monitor_events))
        self.assertEqual(master_events[-1].data,bytes([1,2,3,4]))
        with self.assertRaises(ValueError):slave.header(32)
        with self.assertRaises(ValueError):monitor.set_response(32,Response(bytes(4)))
        with self.assertRaises(ValueError):slave.set_response(60,Response(bytes(2)))
        with self.assertRaises(ValueError):master.header(62)

    def test_faults_no_response_collision_sleep_and_wake(self):
        bus=SimulatedLinBus();events=[]
        master=bus.attach('M','master',events.append)
        slave=bus.attach('S','slave',lambda e:None);slave.set_response(32,Response(bytes(4)))
        for fault,kind in [('checksum','checksum_error'),('no_response','no_response'),('parity','parity_error'),('collision','collision')]:
            bus.next_fault=fault;master.header(32);self.assertEqual(events[-1].kind,kind)
        master.header(33);self.assertEqual(events[-1].kind,'no_response')
        other=bus.attach('S2','slave',lambda e:None);other.set_response(32,Response(bytes(4)))
        master.header(32);self.assertEqual(events[-1].kind,'collision')
        master.sleep();self.assertEqual(events[-1].kind,'sleep')
        with self.assertRaises(ValueError):master.header(32)
        slave.wake();self.assertEqual(events[-1].kind,'wake')
        other.close();master.header(32);self.assertEqual(events[-1].kind,'frame')

    def test_ldf_decode_schedule_and_slot_validation(self):
        db=LinDatabase('examples/demo.ldf')
        self.assertEqual(db.baudrate,19200)
        self.assertEqual(db.schedule('DemoSchedule'),[ScheduleSlot(16,50),ScheduleSlot(32,50)])
        data=db.initial_data(32)
        name,signals=db.decode(LinFrame(time.time(),32,data))
        self.assertEqual(name,'EngineStatus');self.assertIn('EngineRPM=',signals);self.assertIn('rpm',signals)
        self.assertIn('Decode error',db.decode(LinFrame(time.time(),32,b'1'))[1])
        validate_schedule(db.schedule('DemoSchedule'),{16:2,32:4},19200)
        for slots in ([],[ScheduleSlot(32,.1)],[ScheduleSlot(32,float('nan'))],[ScheduleSlot(62,50)]):
            with self.assertRaises(ValueError):validate_schedule(slots,{16:2,32:4},19200)

    def test_service_schedule_can_stop_independently(self):
        service=LinService();events=[];service.message.connect(events.append)
        try:
            service.connect_simulated('master',19200,True,{16:Response(b'\x01\x01')})
            self.assertEqual(events,[])
            service.start_schedule([ScheduleSlot(16,20),ScheduleSlot(32,20)])
            self.assertTrue(wait_for(lambda:len(events)>=6))
            service.stop_schedule();count=len(events)
            wait_for(lambda:False,.1)
            self.assertEqual(len(events),count)
            self.assertTrue(service.is_connected)
        finally:service.disconnect_bus()

    def test_ui_monitor_slave_and_master_modes_pause_filter_and_tabs(self):
        window=MainWindow();panel=window._lin
        try:
            window._tabs.setCurrentIndex(1)
            self.assertEqual(window._adapter_stack.currentIndex(),1)
            panel.connect_button.click()
            self.assertTrue(wait_for(lambda:panel.rx>=4))
            self.assertFalse(panel.header.isEnabled());self.assertFalse(panel.recording.replay.isEnabled())
            self.assertIn('EngineStatus',[panel.model.data(panel.model.index(r,12)) for r in range(panel.model.rowCount())])
            panel.filter.setText('20');panel._filter_changed()
            self.assertTrue(all(panel.proxy.data(panel.proxy.index(r,3))=='20' for r in range(panel.proxy.rowCount())))
            panel._clear_filter();panel.explore.click();count=panel.model.rowCount()
            wait_for(lambda:False,.15);self.assertEqual(panel.model.rowCount(),count)
            panel.connect_button.click();panel.role.setCurrentIndex(2);panel.connect_button.click()
            self.assertTrue(wait_for(lambda:panel.tx>=1))
            panel.connect_button.click();panel.role.setCurrentIndex(1);panel.connect_button.click()
            panel._clear();self.assertFalse(panel.service.schedule_running)
            panel.schedule_button.click();self.assertTrue(wait_for(lambda:panel.tx>=3))
            panel.explore.click();before=panel.model.rowCount()
            wait_for(lambda:False,.15);self.assertEqual(panel.model.rowCount(),before)
            self.assertTrue(panel.service.schedule_running)
            panel.schedule_button.click();self.assertFalse(panel.service.schedule_running)
        finally:window.close()

    def test_lin_mdf_roundtrip_header_frame_fault_and_ldf_attachment(self):
        with tempfile.TemporaryDirectory() as root:
            service=LinService();recorder=MF4Recorder(codec=LINCodec(),temp_directory=root)
            service.traffic.connect(recorder.capture,Qt.ConnectionType.DirectConnection)
            try:
                service.connect_simulated('master',19200,True,{16:Response(bytes([1,1]))})
                recorder.start(service.session_info,'examples/demo.ldf')
                service.send_header(16);service.send_header(32)
                service.bus.next_fault='checksum';service.send_header(32)
                recorder.stop();recorder._thread.join(10)
                self.assertFalse(recorder.problem)
                events=list(LINCodec().read(recorder.pending_path))
                self.assertEqual([e.payload.kind for e in events],['header','frame','header','frame','header','checksum_error'])
                self.assertEqual({e.direction for e in events},{'rx','tx'})
                self.assertEqual(events[1].payload.data,bytes([1,1]))
                with MDF(recorder.pending_path) as mdf:
                    self.assertEqual(mdf.version,'4.10');self.assertEqual(len(mdf.attachments),2)
                    self.assertIn('LIN_Frame.ID',mdf.channels_db)
                    self.assertIn('SignalExplorer_LINEvent.EventKind',mdf.channels_db)
            finally:service.disconnect_bus()

    def test_role_aware_replay_master_headers_slave_buffers(self):
        with tempfile.TemporaryDirectory() as root:
            service=LinService();recorder=MF4Recorder(codec=LINCodec(),temp_directory=root)
            service.traffic.connect(recorder.capture,Qt.ConnectionType.DirectConnection)
            try:
                service.connect_simulated('master',19200,True,{16:Response(b'\x01\x01')})
                recorder.start();service.send_header(16);service.send_header(32)
                recorder.stop();recorder._thread.join(10)
                events=[];service.message.connect(events.append)
                player=MF4Playback(recorder.pending_path,service.send_message,codec=LINCodec('master'),tx_options={'lengths':service.lengths})
                errors=[];player.failed.connect(errors.append);player.start()
                self.assertTrue(wait_for(lambda:not player.isRunning()));self.assertEqual(errors,[])
                # The worker can finish between processEvents() and the poll;
                # its final queued message still needs a GUI event-loop turn.
                self.assertTrue(wait_for(lambda:sum(e.kind=='header' for e in events)==2))
                self.assertEqual(player.sent,2);self.assertEqual(sum(e.kind=='header' for e in events),2)
                service.disconnect_bus();service.connect_simulated('slave',19200,False,{32:Response(bytes(4))})
                events.clear()
                player=MF4Playback(recorder.pending_path,service.send_message,codec=LINCodec('slave',[32]),tx_options={'lengths':service.lengths})
                player.failed.connect(errors.append);player.start();self.assertTrue(wait_for(lambda:not player.isRunning()))
                self.assertEqual(errors,[]);self.assertEqual(player.sent,1)
                self.assertEqual(events,[]) # Buffer update alone never transmits.
                peer=service.bus.attach('Tester','master',lambda e:None);peer.header(32)
                self.assertEqual(events[-1].direction,'tx');self.assertNotEqual(events[-1].data,bytes(4))
                peer.close()
            finally:service.disconnect_bus()

    def test_ui_lin_log_save_while_trace_paused(self):
        with tempfile.TemporaryDirectory() as root:
            window=MainWindow();panel=window._lin;recorder=panel.recording.recorder
            recorder.temp_directory=root
            try:
                panel.role.setCurrentIndex(1);panel.connect_button.click()
                panel.recording.log.click();panel.explore.click()
                panel.service.send_header(16);panel.service.send_header(32)
                self.assertEqual(panel.model.rowCount(),0)
                output=Path(root)/'lin-session.mf4'
                with patch('signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName',return_value=(str(output),'')):
                    panel.recording.log.click();self.assertTrue(wait_for(output.exists))
                records=list(LINCodec().read(output))
                self.assertEqual(len(records),4)
                self.assertEqual({r.direction for r in records},{'rx','tx'})
            finally:
                if recorder.busy:recorder.stop();recorder._thread.join(10)
                if recorder.pending_path:recorder.save_as(Path(root)/'cleanup.mf4')
                window.close()

    def test_ui_slave_playback_updates_buffers_without_unsolicited_tx(self):
        with tempfile.TemporaryDirectory() as root:
            recorder=MF4Recorder(codec=LINCodec(),temp_directory=root)
            now=time.time()
            recorder.start()
            for seconds,data in ((0,b'\x01\x02\x03\x04'),(5,b'\x05\x06\x07\x08')):
                recorder.capture(LinFrame(now+seconds,32,data,checksum=lin_checksum(32,data)))
            recorder.stop();recorder._thread.join(10)
            window=MainWindow();panel=window._lin
            try:
                panel.role.setCurrentIndex(2);panel.demo_peer.setChecked(False);panel.connect_button.click()
                original=panel.service.endpoint.responses[32]
                self.assertEqual(panel.recording.replay.text(),'Replay Response Buffers')
                with patch('signal_explorer.ui.recording_panel.QFileDialog.getOpenFileName',return_value=(str(recorder.pending_path),'')):
                    panel.recording.replay.click()
                self.assertTrue(wait_for(lambda:panel.recording.playback.sent>=1))
                self.assertEqual(panel.tx,0)
                self.assertEqual(panel.service.endpoint.responses[32].data,b'\x01\x02\x03\x04')
                panel.recording.replay.click();self.assertTrue(wait_for(lambda:not panel.recording.playback_busy))
                self.assertEqual(panel.service.endpoint.responses[32],original)
                self.assertIn('buffer updates',panel.recording.playback_status.text())
            finally:
                if panel.recording.playback_busy:
                    panel.recording.playback.stop();panel.recording.playback.wait(2000);app.processEvents()
                window.close()

if __name__=='__main__':unittest.main()
