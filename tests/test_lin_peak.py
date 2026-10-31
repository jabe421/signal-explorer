"""Native PLIN contract tests; no physical adapter or DLL is needed."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import ctypes as C
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication
from signal_explorer.lin.peak import (PeakLinTransport, Message, ReceiveMessage,
                                      FrameEntry, discover_peak)
from signal_explorer.lin.model import Response, protected_id
from signal_explorer.lin.service import LinService
from signal_explorer.ui.lin_panel import LinPanel

app = QApplication.instance() or QApplication([])


class FakeAPI:
    def __init__(self, fail=None, occupied=False):
        self.calls, self.entries, self.writes, self.queue = [], {}, [], []
        self.fail, self.occupied, self.attached = fail, occupied, False
        self.dll = SimpleNamespace(LIN_Read=self.read)

    def call(self, name, *args):
        self.calls.append(name)
        if name == self.fail:
            raise RuntimeError('Injected ' + name)
        if name == 'RegisterClient': args[2]._obj.value = 7
        if name == 'ConnectClient': self.attached = True
        if name == 'GetHardwareParam':
            args[2][0] = 1 if self.occupied or self.attached else 0
            args[2][1] = 8 if self.occupied else 7
        if name == 'GetTargetTime': args[1]._obj.value = 1_000_000
        if name == 'SetFrameEntry':
            entry = FrameEntry.from_buffer_copy(args[2]._obj)
            self.entries[entry.FrameId] = entry
        if name == 'Write': self.writes.append(Message.from_buffer_copy(args[2]._obj))
        if name == 'InitializeHardware': self.mode, self.rate = args[2:]

    def read(self, client, pointer):
        if not self.queue: return 3
        msg = self.queue.pop(0)
        C.memmove(pointer, C.byref(msg), C.sizeof(msg))
        return 0


class PeakLINTests(unittest.TestCase):
    def connect(self, role='master', api=None):
        self.api = api or FakeAPI()
        self.events = []
        transport = PeakLinTransport(dict(hardware=1, channel=1), role, 19200,
                                     {16: 2, 32: 4, 60: 8}, {16:'enhanced',32:'enhanced'},
                                     self.events.append, api=self.api)
        self.addCleanup(transport.close)
        return transport

    def test_native_abi_layout(self):
        self.assertEqual(C.sizeof(Message), 13)
        self.assertEqual(C.sizeof(FrameEntry), 14)
        self.assertEqual(C.sizeof(ReceiveMessage), 40)
        self.assertEqual(ReceiveMessage.ErrorFlags.offset, 16)
        self.assertEqual(ReceiveMessage.TimeStamp.offset, 24)
        self.assertEqual(ReceiveMessage.hHw.offset, 32)

    def test_master_header_and_local_publisher_write(self):
        endpoint = self.connect()
        endpoint.header(32)
        msg = self.api.writes[-1]
        self.assertEqual((msg.FrameId,msg.Length,msg.Direction),(protected_id(32),4,2))
        endpoint.set_response(16,Response(b'\x01\x02'))
        endpoint.header(16)
        msg = self.api.writes[-1]
        self.assertEqual((msg.FrameId,msg.Direction,msg.ChecksumType),(0x50,1,2))
        self.assertEqual(bytes(msg.Data[:2]),b'\x01\x02')
        self.assertEqual(msg.Checksum,0xac)
        self.assertEqual(self.events,[])  # Queuing is not physical TX confirmation.

    def test_slave_buffer_updates_removal_and_role_guards(self):
        endpoint=self.connect('slave')
        endpoint.set_response(32,Response(bytes(4)))
        self.assertEqual(self.api.entries[32].Direction,1)
        self.assertEqual(self.api.entries[32].Flags,1)
        self.assertEqual(self.api.writes,[])
        endpoint.remove_response(32)
        self.assertEqual(self.api.entries[32].Direction,2)
        self.assertEqual(self.api.entries[32].Flags,0)
        self.assertNotIn(32,endpoint.responses)
        with self.assertRaises(ValueError):endpoint.header(32)
        with self.assertRaises(ValueError):endpoint.sleep()
        endpoint.wake()
        self.assertIn('XmtWakeUp',self.api.calls)

    def test_monitor_never_publishes(self):
        endpoint=self.connect('monitor')
        self.assertEqual(self.api.mode,1)
        self.assertTrue(all(e.Direction in (2,3) and e.Flags==0 for e in self.api.entries.values()))
        self.assertEqual((self.api.entries[1].Direction,self.api.entries[1].ChecksumType),(3,3))
        for call in (lambda:endpoint.header(32),lambda:endpoint.set_response(32,Response(bytes(4))),endpoint.sleep,endpoint.wake):
            with self.assertRaises(ValueError):call()
        self.assertEqual(self.api.writes,[])

    def test_sleep_diagnostic_classic_and_invalid_data(self):
        endpoint=self.connect()
        endpoint.sleep()
        msg=self.api.writes[-1]
        self.assertEqual((msg.FrameId,msg.Length,msg.ChecksumType),(60,8,1))
        self.assertEqual(bytes(msg.Data),b'\0'+b'\xff'*7)
        with self.assertRaises(ValueError):endpoint.header(62)
        with self.assertRaises(ValueError):endpoint.set_response(32,Response(bytes(2)))
        with self.assertRaises(ValueError):endpoint.set_response(60,Response(bytes(2)))

    def test_receive_direction_timestamp_errors_and_header_logging(self):
        endpoint=self.connect()
        msg=ReceiveMessage(FrameId=0x50,Length=2,Direction=1,ChecksumType=2,
                           TimeStamp=1_500_000,hHw=1,Checksum=0xac)
        msg.Data[:2]=b'\1\2'
        self.api.queue.append(msg);endpoint.poll()
        self.assertEqual([e.kind for e in self.events],['header','frame'])
        self.assertEqual(self.events[-1].direction,'tx')
        self.assertEqual(self.events[-1].timestamp,endpoint._epoch+1.5)
        self.assertEqual(self.events[-1].data,b'\1\2')
        for flags,kind in ((0x200,'frame'),(0x20,'checksum_error'),(6,'parity_error'),(0x18,'no_response')):
            msg.ErrorFlags=flags;self.api.queue.append(msg);endpoint.poll()
            self.assertEqual(self.events[-1].kind,kind)
            if flags==0x200:self.assertEqual(self.events[-1].direction,'rx')
        self.api.queue.append(ReceiveMessage(Type=5,hHw=1))
        with self.assertRaisesRegex(RuntimeError,'lost'):endpoint.poll()

    def test_occupied_channel_and_failed_setup_cleanup(self):
        api=FakeAPI(occupied=True)
        with self.assertRaisesRegex(RuntimeError,'in use'):self.connect(api=api)
        self.assertNotIn('InitializeHardware',api.calls)
        api=FakeAPI(fail='SetClientFilter')
        with self.assertRaisesRegex(RuntimeError,'Injected'):self.connect(api=api)
        self.assertEqual(api.calls[-3:],['ResetHardwareConfig','DisconnectClient','RemoveClient'])

    def test_cleanup_attempts_every_release_and_closed_guard(self):
        endpoint=self.connect()
        self.api.fail='ResetHardwareConfig'
        with self.assertRaises(RuntimeError):endpoint.close()
        self.assertEqual(self.api.calls[-3:],['ResetHardwareConfig','DisconnectClient','RemoveClient'])
        with self.assertRaisesRegex(RuntimeError,'closed'):endpoint.header(32)
        endpoint.close()

    def test_missing_api_scan_and_ui_choice(self):
        with patch('signal_explorer.lin.peak.PeakAPI',side_effect=RuntimeError('Install PLIN')):
            self.assertEqual(discover_peak(),([], 'Install PLIN'))
            panel=LinPanel()
            self.addCleanup(panel.close)
            panel.adapter.setCurrentIndex(panel.adapter.findData('peak'))
            self.assertEqual(panel.channel.count(),0)
            self.assertIn('Install PLIN',panel.note.text())
            self.assertTrue(panel.demo_peer.isHidden())
            panel.adapter.setCurrentIndex(panel.adapter.findData('virtual'))
            panel.service.connect_simulated('master',19200,False)
            self.addCleanup(panel.service.disconnect_bus)
            self.assertTrue(panel.inject.isEnabled())

    def test_service_hardware_lifecycle_and_response_restore(self):
        endpoint=self.connect('slave')
        service=LinService()
        self.addCleanup(service.disconnect_bus)
        with patch('signal_explorer.lin.peak.PeakLinTransport',return_value=endpoint):
            service.connect_hardware('peak',dict(hardware=1,channel=1,serial=123),
                                     'slave',19200,{32:Response(bytes(4))})
        self.assertTrue(service.is_connected)
        self.assertFalse(service.session_info['simulated'])
        self.assertEqual(service.session_info['serial'],123)
        self.assertTrue(service.send_message(('slave',32,b'\1\2\3\4','enhanced')))
        service.restore_responses()
        self.assertEqual(endpoint.responses[32].data,bytes(4))
        service.disconnect_bus()
        self.assertIsNone(service._reader)
        self.assertFalse(service.is_connected)
