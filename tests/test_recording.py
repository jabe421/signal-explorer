import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import can
from asammdf import MDF
from asammdf.blocks.v4_constants import FLAG_CG_BUS_EVENT
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.recording.playback import MF4Playback
from signal_explorer.recording.codecs import BusEvent, CANCodec
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.bus.backends import BACKENDS

app=QApplication.instance() or QApplication([])

def wait_for(predicate, timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        time.sleep(.01)
        app.processEvents()
        if predicate(): return True
    return False

class MDFLogging(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, frames, database=None):
        recorder=MF4Recorder(temp_directory=self.root)
        recorder.start({'native_channel':'COM3','bitrate':500000},database)
        for msg in frames: recorder.capture(msg)
        recorder.stop()
        recorder._thread.join(15)
        self.assertFalse(recorder.busy)
        self.assertEqual(recorder.problem,'')
        self.assertIsNotNone(recorder.pending_path)
        return recorder

    def frames(self, gaps=(0,.08,.20), fd=False):
        now=time.time()
        return [can.Message(timestamp=now+gap, arbitration_id=0x100+i,
                            is_extended_id=False,data=bytes(range(32)) if fd else bytes([i]),
                            is_fd=fd,bitrate_switch=fd,is_rx=i!=1,channel='COM3')
                for i,gap in enumerate(gaps)]

    def test_asam_structure_roundtrip_direction_timing_fd_and_attachments(self):
        frames=self.frames(fd=True)
        frames.append(can.Message(timestamp=frames[-1].timestamp+.1,arbitration_id=0x12345,
                                  is_extended_id=True,is_remote_frame=True,dlc=8,is_rx=False))
        frames.append(can.Message(timestamp=frames[-1].timestamp+.1,is_error_frame=True, data=[1]))
        recorder=self.record(frames,'examples/demo.dbc')
        path=recorder.pending_path
        self.assertEqual(path.read_bytes()[:8],b'MDF     ')
        with can.MF4Reader(path) as reader: actual=list(reader)
        self.assertEqual(len(actual),len(frames))
        for original,saved in zip(frames,actual):
            self.assertEqual(saved.arbitration_id,original.arbitration_id)
            self.assertEqual(saved.is_rx,original.is_rx)
            self.assertEqual(saved.is_extended_id,original.is_extended_id)
            self.assertEqual(saved.is_fd,original.is_fd)
            self.assertEqual(saved.bitrate_switch,original.bitrate_switch)
            self.assertEqual(saved.is_remote_frame,original.is_remote_frame)
            self.assertEqual(saved.is_error_frame,original.is_error_frame)
            self.assertEqual(saved.data,original.data)
            self.assertEqual(saved.channel,1)
            self.assertAlmostEqual(saved.timestamp,original.timestamp,places=5)
        with MDF(path) as mdf:
            self.assertEqual(mdf.version,'4.10')
            bus_groups=[group for group in mdf.groups if any(c.name in ('CAN_DataFrame','CAN_ErrorFrame','CAN_RemoteFrame') for c in group.channels)]
            self.assertEqual(len(bus_groups),3)
            self.assertTrue(all(group.channel_group.flags & FLAG_CG_BUS_EVENT for group in bus_groups))
            self.assertEqual(len(mdf.attachments),2)
            samples=mdf.get('CAN_DataFrame').samples
            self.assertIn('CAN_DataFrame.Dir',samples.dtype.names)
            self.assertEqual(int(samples['CAN_DataFrame.DLC'][0]),13) # 32-byte encoded DLC
            metadata=json.loads(mdf.extract_attachment(1)[0])
            self.assertEqual(metadata['native_channel'],'COM3')
            self.assertEqual(metadata['protocol'],'CAN')

    def test_save_failure_preserves_original_and_success_is_mf4(self):
        recorder=self.record(self.frames())
        original=recorder.pending_path
        destination=self.root/'capture.mf4'
        destination.write_bytes(b'old content')
        with patch('signal_explorer.recording.recorder.shutil.copyfileobj',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): recorder.save_as(destination)
        self.assertEqual(destination.read_bytes(),b'old content')
        self.assertEqual(recorder.pending_path,original)
        self.assertTrue(original.exists())
        saved=recorder.save_as(self.root/'new-capture')
        self.assertEqual(saved.suffix,'.mf4')
        self.assertIsNone(recorder.pending_path)
        self.assertFalse(original.exists())
        with can.MF4Reader(saved) as reader: self.assertEqual(len(list(reader)),3)

    def test_bad_timestamps_are_counted_in_session_metadata(self):
        frames=self.frames()
        frames[1].timestamp=frames[0].timestamp-10
        frames[2].timestamp=None
        recorder=self.record(frames)
        with can.MF4Reader(recorder.pending_path) as reader: actual=list(reader)
        self.assertTrue(all(a.timestamp<=b.timestamp for a,b in zip(actual,actual[1:])))
        with MDF(recorder.pending_path) as mdf:
            metadata=json.loads(mdf.extract_attachment(0)[0])
            self.assertEqual(metadata['timestamp_corrections'],2)

    def test_queue_overflow_is_explicit_and_finalizes_partial(self):
        entered=threading.Event(); release=threading.Event()
        class SlowCodec(CANCodec):
            def writer(inner,path,database=None):
                entered.set(); release.wait(3)
                return super().writer(path,database)
        recorder=MF4Recorder(queue_size=1,temp_directory=self.root,codec=SlowCodec())
        recorder.start()
        self.assertTrue(entered.wait(2))
        try:
            for msg in self.frames(): recorder.capture(msg)
            self.assertFalse(recorder.recording)
            self.assertIn('overflow',recorder.problem)
        finally:
            release.set(); recorder.stop(); recorder._thread.join(10)
        self.assertIsNotNone(recorder.pending_path)
        self.assertEqual(recorder.count,1)
        with MDF(recorder.pending_path) as mdf:
            self.assertIn('overflow',json.loads(mdf.extract_attachment(0)[0])['incomplete_reason'])

    def test_ui_paused_trace_logs_rx_tx_then_save_dialog_cancel_and_retry(self):
        window=MainWindow()
        channel=str(uuid.uuid4())
        peer=can.Bus(interface='virtual',channel=channel)
        panel=window._recording_panel
        panel.recorder.temp_directory=self.root
        try:
            self.assertFalse(panel.log.isEnabled())
            window._service.connect_bus(BACKENDS[0],channel,500000,False)
            panel.log.click()
            self.assertEqual(panel.log.text(),'Stop Logging')
            window._explore.click()
            window._filter.setText('555');window._apply_filter()
            peer.send(can.Message(arbitration_id=0x100,data=[1]))
            window._send.click()
            self.assertTrue(wait_for(lambda:panel.recorder.count>=2))
            self.assertEqual(window._model.rowCount(),0)
            with patch('signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName',return_value=('', '')):
                panel.log.click()
                self.assertTrue(wait_for(lambda:panel.recorder.pending_path is not None and not panel.recorder.busy))
                app.processEvents()
            self.assertEqual(panel.log.text(),'Start Logging')
            self.assertFalse(panel.log.isEnabled())
            self.assertTrue(panel.save.isEnabled())
            output=self.root/'ui.mf4'
            with patch('signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName',return_value=(str(output), '')):
                panel.save.click()
            self.assertTrue(output.exists())
            with can.MF4Reader(output) as reader: frames=list(reader)
            self.assertEqual({msg.is_rx for msg in frames},{True,False})
            self.assertTrue(panel.log.isEnabled())
        finally:
            if panel.recorder.busy:
                panel.recorder.stop();panel.recorder._thread.join(10)
            if panel.recorder.pending_path: panel.recorder.save_as(self.root/'cleanup.mf4')
            window.close();peer.shutdown()

    def test_ui_disconnect_finalizes_and_prompts(self):
        window=MainWindow();panel=window._recording_panel
        panel.recorder.temp_directory=self.root
        try:
            window._service.connect_bus(BACKENDS[0],str(uuid.uuid4()),500000,False)
            panel.log.click();window._send.click()
            with patch('signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName',return_value=(str(self.root/'disconnect.mf4'), '')):
                window._service.disconnect_bus()
                self.assertTrue(wait_for(lambda:(self.root/'disconnect.mf4').exists()))
            self.assertFalse(panel.recorder.busy)
            self.assertFalse(panel.log.isEnabled())
        finally:
            if panel.recorder.busy:
                panel.recorder.stop();panel.recorder._thread.join(10)
            if panel.recorder.pending_path:panel.recorder.save_as(self.root/'cleanup.mf4')
            window.close()

    def test_playback_preserves_relative_gaps_and_remaps_to_connected_virtual_bus(self):
        recorder=self.record(self.frames(fd=True))
        from signal_explorer.bus.service import BusService
        service=BusService();channel=str(uuid.uuid4())
        service.connect_bus(BACKENDS[0],channel,500000,False,fd=True)
        peer=can.Bus(interface='virtual',channel=channel)
        errors=[];sent_at=[]
        def send(msg):
            sent_at.append(time.monotonic())
            return service.send_message(msg,report_errors=False)
        replay=MF4Playback(recorder.pending_path,send,True)
        replay.failed.connect(errors.append)
        try:
            replay.start()
            self.assertTrue(wait_for(lambda:not replay.isRunning()))
            self.assertEqual(errors,[])
            frames=[peer.recv(.2) for _ in range(3)]
            self.assertTrue(all(frame is not None for frame in frames))
            self.assertTrue(all(frame.channel==channel and frame.is_fd and frame.bitrate_switch for frame in frames))
            self.assertAlmostEqual(sent_at[1]-sent_at[0],.08,delta=.05)
            self.assertAlmostEqual(sent_at[2]-sent_at[0],.20,delta=.05)
        finally:
            replay.stop();replay.wait(3000);service.disconnect_bus();peer.shutdown()

    def test_playback_preflight_fd_mismatch_and_empty_selection_send_nothing(self):
        recorder=self.record(self.frames(fd=True))
        for channel,enabled in ((1,False),(5,True)):
            sent=[];errors=[]
            replay=MF4Playback(recorder.pending_path,lambda msg:sent.append(msg) or True,enabled,bus_channel=channel)
            replay.failed.connect(errors.append)
            replay.start()
            self.assertTrue(wait_for(lambda:not replay.isRunning()))
            app.processEvents()
            self.assertTrue(errors)
            self.assertEqual(sent,[])

    def test_playback_filters_direction_and_stops_long_gaps_promptly(self):
        recorder=self.record(self.frames(gaps=(0,5,10)))
        sent=[]
        replay=MF4Playback(recorder.pending_path,lambda msg:sent.append(msg) or True,False,direction='rx')
        replay.start()
        self.assertTrue(wait_for(lambda:len(sent)==1))
        start=time.monotonic();replay.stop()
        self.assertTrue(replay.wait(1000))
        self.assertLess(time.monotonic()-start,.5)
        self.assertEqual(len(sent),1)
        sent=[]
        replay=MF4Playback(recorder.pending_path,lambda msg:sent.append(msg) or True,False,direction='tx')
        replay.start();self.assertTrue(wait_for(lambda:not replay.isRunning()))
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0].arbitration_id,0x101)
        self.assertFalse(sent[0].is_rx)

    def test_logging_does_not_wait_for_gui_event_processing(self):
        from signal_explorer.bus.service import BusService
        service=BusService();channel=str(uuid.uuid4())
        peer=can.Bus(interface='virtual',channel=channel)
        recorder=MF4Recorder(temp_directory=self.root)
        service.traffic.connect(recorder.capture,Qt.ConnectionType.DirectConnection)
        try:
            service.connect_bus(BACKENDS[0],channel,500000,False)
            recorder.start()
            for i in range(30):peer.send(can.Message(arbitration_id=i,data=[i]))
            # Deliberately do not process Qt events: raw capture must still run.
            deadline=time.monotonic()+3
            while recorder.count<30 and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(recorder.count,30)
        finally:
            recorder.stop();recorder._thread.join(10)
            service.disconnect_bus();peer.shutdown()

    def test_ui_playback_stops_repeated_send_and_disconnect_stops_replay(self):
        recorder=self.record(self.frames(gaps=(0,5,10)))
        window=MainWindow();panel=window._recording_panel
        try:
            window._service.connect_bus(BACKENDS[0],str(uuid.uuid4()),500000,False)
            window._tx_interval.setText('100');window._send.click()
            self.assertTrue(window._tx_timer.isActive())
            with patch('signal_explorer.ui.recording_panel.QFileDialog.getOpenFileName',return_value=(str(recorder.pending_path),'')):
                panel.replay.click()
            self.assertFalse(window._tx_timer.isActive())
            self.assertFalse(window._send.isEnabled())
            self.assertEqual(panel.replay.text(),'Stop Playback')
            self.assertTrue(wait_for(lambda:panel.playback.sent==1))
            window._service.disconnect_bus()
            self.assertTrue(wait_for(lambda:not panel.playback_busy))
            self.assertEqual(panel.replay.text(),'Playback Log as TX')
            self.assertFalse(window._send.isEnabled())
        finally:
            if panel.playback_busy:
                panel.playback.stop();panel.playback.wait(3000);app.processEvents()
            window.close()

    def test_close_active_recording_saves_before_closing(self):
        window=MainWindow();panel=window._recording_panel
        panel.recorder.temp_directory=self.root
        window._service.connect_bus(BACKENDS[0],str(uuid.uuid4()),500000,False)
        panel.log.click();window._send.click()
        output=self.root/'close.mf4'
        try:
            with patch('signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName',return_value=(str(output),'')):
                window.close()
                self.assertTrue(wait_for(lambda:output.exists() and not window._service.is_connected))
            self.assertIsNone(panel.recorder.pending_path)
        finally:
            if panel.recorder.busy:
                panel.recorder.stop();panel.recorder._thread.join(10)
            if panel.recorder.pending_path:panel.recorder.save_as(self.root/'cleanup.mf4')
            window.close()

    def test_file_opening_delay_does_not_compress_replay_gaps(self):
        class SlowReader:
            protocol='EXAMPLE'
            def read(self,path):
                time.sleep(.15)
                yield BusEvent(1.0,1,'rx','EXAMPLE',b'a')
                yield BusEvent(1.08,1,'rx','EXAMPLE',b'b')
            def replayable(self,event):return True
            def prepare_tx(self,event,options):return event.payload
        times=[]
        replay=MF4Playback('not-used',lambda msg:times.append(time.monotonic()) or True,codec=SlowReader())
        replay.start();self.assertTrue(wait_for(lambda:not replay.isRunning()))
        self.assertEqual(len(times),2)
        self.assertAlmostEqual(times[1]-times[0],.08,delta=.04)

    def test_replay_scheduler_accepts_non_can_codec(self):
        # Contract test only: no claim of LIN/Ethernet MDF encoding support yet.
        class ExampleCodec:
            protocol='EXAMPLE'
            def read(self,path):
                yield BusEvent(1.0,1,'rx','EXAMPLE',b'abc')
                yield BusEvent(1.02,1,'tx','EXAMPLE',b'def')
            def replayable(self,event): return True
            def prepare_tx(self,event,options):return event.payload
        sent=[]
        replay=MF4Playback('not-used',lambda payload:sent.append(payload) or True,False,codec=ExampleCodec())
        replay.start();self.assertTrue(wait_for(lambda:not replay.isRunning()))
        self.assertEqual(sent,[b'abc',b'def'])

if __name__=='__main__':unittest.main()
