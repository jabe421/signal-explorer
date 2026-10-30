import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import unittest
from unittest.mock import patch
import time
import uuid
import can
import cantools
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.canfd import fd_configuration, validate_frame, FD_LENGTHS
from signal_explorer.bus.demo import demo_frames
from signal_explorer.bus.service import BusService
from signal_explorer.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])

def wait_for(predicate, timeout=2):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        time.sleep(0.01)
        app.processEvents()
        if predicate():
            return True
    return False

class CanFD(unittest.TestCase):
    def test_payload_lengths_and_classic_rules(self):
        for length in range(66):
            msg = can.Message(arbitration_id=1, is_fd=True, data=bytes(length))
            if length in FD_LENGTHS:
                validate_frame(msg, True)
            else:
                with self.assertRaises(ValueError):
                    validate_frame(msg, True)
        with self.assertRaises(ValueError):
            validate_frame(can.Message(is_fd=True, data=bytes(12)), False)
        with self.assertRaises(ValueError):
            validate_frame(can.Message(data=bytes(9)), True)
        with self.assertRaises(ValueError):
            validate_frame(can.Message(data=bytes(8), bitrate_switch=True), True)

    def test_backend_configuration_and_capability_guard(self):
        for interface in ('vector', 'kvaser', 'neovi'):
            self.assertEqual(fd_configuration(interface,500000,2000000), {'fd':True,'data_bitrate':2000000})
        timing = fd_configuration('pcan',500000,2000000)['timing']
        self.assertEqual(timing.nom_bitrate,500000)
        self.assertEqual(timing.data_bitrate,2000000)
        for interface in ('slcan','gs_usb'):
            with self.assertRaises(ValueError):
                fd_configuration(interface,500000,2000000)
        service = BusService()
        with patch('signal_explorer.bus.service.can.Bus') as bus:
            service.connect_bus(next(b for b in BACKENDS if b.key=='vector'),'0',500000,False,
                                {'supports_fd':False},fd=True)
            bus.assert_not_called()
        for key in ('vector','kvaser','ics_neovi','pcan'):
            backend = next(b for b in BACKENDS if b.key==key)
            with patch('signal_explorer.bus.service.can.Bus', side_effect=RuntimeError('stop')) as bus:
                service.connect_bus(backend,backend.default_channel,500000,False,fd=True)
            kwargs=bus.call_args.kwargs
            self.assertTrue(kwargs['fd'])
            if key=='pcan':
                self.assertEqual(kwargs['timing'].data_bitrate,2000000)
            else:
                self.assertEqual(kwargs['data_bitrate'],2000000)

    def test_demo_dbc_consistency_and_signals_past_eighth_byte(self):
        db=cantools.database.load_file('examples/demo.dbc')
        self.assertTrue(db.get_message_by_frame_id(0x300).is_fd)
        for tick in (1,30,100,255,256,1000):
            for msg in demo_frames(tick,True):
                values=db.decode_message(msg.arbitration_id,msg.data)
                if msg.arbitration_id in (0x200,0x300):
                    self.assertTrue(800 <= values['EngineRPM'] <= 2800)
                    self.assertTrue(10 <= values['MAF'] <= 50)
                    self.assertTrue(0.1 <= values['O2B1S1Voltage'] <= 0.8)
                if msg.is_fd:
                    self.assertEqual(msg.dlc,32)
                    self.assertEqual(values['SampleCounter'],tick)
                    self.assertIn('O2B2S1Voltage',values)
        self.assertEqual(len(demo_frames(1,False)),2)

    def test_virtual_demo_receive_decode_modes_and_pause(self):
        window=MainWindow()
        try:
            window._model.set_database(cantools.database.load_file('examples/demo.dbc'))
            window._service.connect_bus(BACKENDS[0],str(uuid.uuid4()),500000,True,fd=True)
            self.assertTrue(wait_for(lambda: window._rx_count>=6))
            window._auto_scroll.setChecked(False)
            self.assertEqual(window._model.rowCount(),3)
            rows={window._model.data(window._model.index(r,3)):r for r in range(3)}
            row=rows['300']
            self.assertEqual(window._model.data(window._model.index(row,10)),'Y')
            self.assertEqual(window._model.data(window._model.index(row,11)),'Y')
            self.assertIn('O2B2Lambda=',window._model.data(window._model.index(row,9)))
            window._filter.setText('300'); window._apply_filter()
            self.assertEqual(window._proxy.rowCount(),1)
            window._explore.click()
            count=window._rx_count
            wait_for(lambda:False,0.25)
            self.assertEqual(window._rx_count,count)
            window._explore.click()
            self.assertTrue(wait_for(lambda:window._rx_count>count))
        finally:
            window.close()

    def test_ui_repeated_64byte_fd_send_and_error_validation(self):
        window=MainWindow()
        channel=str(uuid.uuid4())
        peer=can.Bus(interface='virtual',channel=channel,protocol=can.CanProtocol.CAN_FD)
        try:
            window._service.connect_bus(BACKENDS[0],channel,500000,False,fd=True)
            window._tx_fd.setChecked(True)
            window._tx_brs.setChecked(True)
            window._tx_data.setText(bytes(range(64)).hex(' '))
            window._tx_interval.setText('100')
            window._send.click()
            self.assertTrue(wait_for(lambda:window._tx_count>=3))
            window._send.click()
            frames=[]
            while (msg:=peer.recv(0)) is not None:
                frames.append(msg)
            self.assertGreaterEqual(len(frames),3)
            self.assertTrue(all(m.is_fd and m.bitrate_switch and len(m.data)==64 for m in frames))
            self.assertEqual(window._send.text(),'Send')
            window._tx_interval.clear()
            window._tx_data.setText(bytes(9).hex(' '))
            with patch('signal_explorer.ui.main_window.QMessageBox.warning') as warning:
                window._send.click()
                warning.assert_called_once()
            self.assertIsNone(peer.recv(0))
            window._tx_fd.setChecked(False)
            self.assertFalse(window._tx_brs.isChecked())
            window._service.disconnect_bus()
            self.assertFalse(window._tx_fd.isChecked())
        finally:
            window.close()
            peer.shutdown()

if __name__=='__main__':
    unittest.main()
