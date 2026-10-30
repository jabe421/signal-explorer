import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import time
import tempfile
import json
import unittest
from unittest.mock import patch
import uuid

import can
from PySide6.QtWidgets import QApplication

from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import BusService
from signal_explorer.bus.timestamps import HardwareClock
from signal_explorer.recording.codecs import BusEvent
from signal_explorer.recording.playback import MF4Playback
from signal_explorer.recording.recorder import MF4Recorder
from asammdf import MDF

app = QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return True
        time.sleep(.005)
    return False


class TimestampTests(unittest.TestCase):
    def test_device_epoch_is_anchored_once_and_hardware_intervals_are_retained(self):
        clock=HardwareClock()
        with patch('signal_explorer.bus.timestamps.time.time',return_value=1800000000.0):
            first=clock.to_unix(1167609638.9249353)
        with patch('signal_explorer.bus.timestamps.time.time',return_value=1800000005.0):
            second=clock.to_unix(1167609638.9449353)
        self.assertEqual(first,1800000000.0)
        self.assertAlmostEqual(second-first,.02,places=5)
        # A device timer reset or a new connection starts a fresh anchor.
        with patch('signal_explorer.bus.timestamps.time.time',return_value=1800000010.0):
            self.assertEqual(clock.to_unix(0),1800000010.0)
            self.assertEqual(HardwareClock().to_unix(100),1800000010.0)

    def test_invalid_hardware_timestamps_are_reported(self):
        for raw in (float('nan'),float('inf'),-1):
            with self.subTest(raw=raw),self.assertRaisesRegex(ValueError,'hardware timestamp'):
                HardwareClock().to_unix(raw)

    def test_intrepid_can_monitor_and_logger_share_host_clock_with_tx(self):
        channel='timestamp-'+uuid.uuid4().hex
        bus=can.Bus(interface='virtual',channel=channel,ignore_config=True)
        peer=can.Bus(interface='virtual',channel=channel,preserve_timestamps=True,ignore_config=True)
        service=BusService();captured=[];displayed=[]
        service.traffic.connect(captured.append);service.message.connect(displayed.append)
        try:
            with patch('signal_explorer.bus.service.can.Bus',return_value=bus),patch('signal_explorer.bus.service.DeviceLease'):
                service.connect_bus(next(b for b in BACKENDS if b.interface=='neovi'),'HSCAN',500000,False)
            with patch('signal_explorer.bus.timestamps.time.time',return_value=1800000000.0):
                peer.send(can.Message(timestamp=1167609638.9249353,arbitration_id=0x100,data=[1],is_extended_id=False))
                self.assertTrue(wait_for(lambda:len(displayed)==1))
            with patch('signal_explorer.bus.timestamps.time.time',return_value=1800000000.01):
                self.assertTrue(service.send_message(can.Message(arbitration_id=0x222,data=[2],is_extended_id=False)))
                peer.send(can.Message(timestamp=1167609638.9449353,arbitration_id=0x100,data=[3],is_extended_id=False))
                self.assertTrue(wait_for(lambda:len(displayed)==3))
            self.assertEqual([m.is_rx for m in captured],[True,False,True])
            for actual,expected in zip(captured,(1800000000,1800000000.01,1800000000.02)):
                self.assertAlmostEqual(actual.timestamp,expected,places=5)
            self.assertEqual([m.timestamp for m in displayed],[m.timestamp for m in captured])
            self.assertIn('anchored',service.session_info['timestamp_source'])
            with tempfile.TemporaryDirectory() as folder:
                recorder=MF4Recorder(temp_directory=folder)
                recorder.start(service.session_info)
                for frame in captured:recorder.capture(frame)
                recorder.stop();recorder._thread.join(5)
                self.assertFalse(recorder.busy);self.assertEqual(recorder.problem,'')
                with can.MF4Reader(recorder.pending_path) as reader:
                    saved=list(reader)
                self.assertEqual([frame.timestamp for frame in saved],[frame.timestamp for frame in captured])
                with MDF(recorder.pending_path) as mdf:
                    metadata=json.loads(mdf.extract_attachment(0)[0])
                self.assertEqual(metadata['timestamp_corrections'],0)
        finally:
            service.disconnect_bus();peer.shutdown()

    def test_timestamp_jump_is_rejected_before_any_transmission(self):
        class BadClockCodec:
            protocol='EXAMPLE'
            def read(self,path):
                for stamp in (1167609638.9,1167609638.92,1792814086.09):
                    yield BusEvent(stamp,1,'rx',self.protocol,b'data')
            def replayable(self,event):return True
            def prepare_tx(self,event,options):return event.payload
        sent=[];errors=[]
        worker=MF4Playback('unused',lambda frame:sent.append(frame) or True,codec=BadClockCodec())
        worker.failed.connect(errors.append)
        worker.run()
        self.assertFalse(sent)
        self.assertEqual(len(errors),1)
        self.assertIn('logfile row 3',errors[0])
        self.assertIn('before transmitting',errors[0])
        self.assertNotIn('timeout value is too large',errors[0])

    def test_extreme_deadline_uses_bounded_interruptible_wait(self):
        worker=MF4Playback('unused',lambda frame:True)
        waits=[]
        def stop_after_wait(seconds):
            waits.append(seconds)
            worker.stop()
        with patch.object(worker._condition,'wait',side_effect=stop_after_wait):
            self.assertFalse(worker._wait_until(time.monotonic()+625204447))
        self.assertEqual(waits,[60.0])


if __name__ == '__main__':unittest.main()
