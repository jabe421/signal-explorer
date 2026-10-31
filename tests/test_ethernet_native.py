import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import ctypes as C
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import ics
from asammdf import MDF
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.ethernet.discovery import EthernetAdapter, discover_pc, discover_intrepid, virtual_adapter
from signal_explorer.ethernet.pcap import NpcapAPI, PcapTransport, PacketHeader
from signal_explorer.ethernet.intrepid import IntrepidEthernetTransport
from signal_explorer.ethernet.model import EthernetFrame, LOCAL_MAC, preset, make_frame
from signal_explorer.ui.ethernet_trace_model import EthernetTraceModel
from signal_explorer.ethernet.codec import EthernetCodec
from signal_explorer.ethernet.service import EthernetService
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.recording.playback import MF4Playback
from signal_explorer.ui.ethernet_panel import EthernetPanel
from test_lin_native import IntrepidFake
from test_ethernet import wait_for, write_log

app = QApplication.instance() or QApplication([])
PC = dict(name=r'\Device\NPF_{TEST}', mac=LOCAL_MAC, link_up=True)


class PcapFake:
    def __init__(self, fail=None):
        self.calls=[];self.queue=[];self.fail=fail

    def devices(self): return [dict(name=PC['name'], description='Test Ethernet', flags=6)]
    def open(self, name, promiscuous=False):
        self.calls.append(('open', name, promiscuous))
        if self.fail=='open': raise RuntimeError('open failed')
        return 123
    def receive(self, handle):
        if self.fail=='receive': raise RuntimeError('capture lost')
        return self.queue.pop(0) if self.queue else None
    def send(self, handle, raw):
        if self.fail=='send': raise RuntimeError('send failed')
        self.calls.append(('send', raw))
    def stats(self, handle): return dict(driver_received=4, driver_dropped=2, interface_dropped=1)
    def close(self, handle): self.calls.append(('close', handle))


class EthernetNativeTests(unittest.TestCase):
    def test_trace_bounds_history_and_distinct_native_flows(self):
        for history in (True,False):
            model=EthernetTraceModel();model.set_history_mode(history)
            with patch('signal_explorer.ui.ethernet_trace_model.MAX_ROWS',3):
                for i in range(6):
                    model.append(make_frame(LOCAL_MAC,f'02:00:00:00:00:{i+2:02x}',0x88b5,b'test'))
            self.assertEqual(model.rowCount(),3)
            self.assertEqual(len(model.latest),3);self.assertEqual(len(model.history),3)
            self.assertEqual(model.data(model.index(0,5)),'02:00:00:00:00:05')

    def pcap(self, api=None, events=None):
        endpoint=PcapTransport(PC, (events if events is not None else []).append, api=api or PcapFake())
        self.addCleanup(endpoint.close);return endpoint

    def intrepid(self, api=None, events=None):
        endpoint=IntrepidEthernetTransport(dict(serial='V2Q475'), (events if events is not None else []).append, api=api or IntrepidFake())
        self.addCleanup(endpoint.close);return endpoint

    def test_pc_discovery_maps_guids_and_retains_unavailable_interfaces(self):
        adapters=[dict(guid='{TEST}', label='Bench port', description='Intel wired', mac=LOCAL_MAC, up=True),
                  dict(guid='{MISSING}', label='Unplugged port', description='USB wired', mac=LOCAL_MAC, up=False)]
        options, report=discover_pc(adapters, PcapFake())
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0].config['name'], PC['name'])
        self.assertTrue(options[0].available);self.assertFalse(options[1].available)
        self.assertIn('up', options[0].label);self.assertIn('down', options[1].label)
        with patch('signal_explorer.ethernet.pcap.NpcapAPI', side_effect=RuntimeError('Install Npcap')):
            options, report=discover_pc(adapters)
        self.assertEqual(len(options), 2)
        self.assertTrue(all(not o.available for o in options));self.assertIn('Install Npcap', report)

    def test_intrepid_discovery_excludes_can_only_devices_and_never_opens(self):
        api=IntrepidFake()
        devices=[api.device, SimpleNamespace(Name='ValueCAN4-2', DeviceType=7, SerialNumber=1, NumberOfClients=0)]
        with patch.object(api, 'find_devices', return_value=devices):
            options, _=discover_intrepid(api)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].config['serial'], 'V2Q475')
        self.assertEqual(api.calls, [])

    def test_npcap_open_flags_datalink_nonblock_and_error_cleanup(self):
        from unittest.mock import MagicMock
        api=object.__new__(NpcapAPI);api.dll=MagicMock()
        api.dll.pcap_open.return_value=123;api.dll.pcap_datalink.return_value=1
        api.dll.pcap_setnonblock.return_value=0
        self.assertEqual(api.open(PC['name'], True), 123)
        self.assertEqual(api.dll.pcap_open.call_args.args[2], 25)  # Promiscuous + no local echo + immediate.
        self.assertEqual(api.dll.pcap_setnonblock.call_args.args[1], 1)
        api.dll.pcap_datalink.return_value=127
        with self.assertRaisesRegex(RuntimeError, 'DLT_EN10MB'): api.open(PC['name'])
        api.dll.pcap_close.assert_called_once_with(123)
        self.assertEqual(C.sizeof(PacketHeader), 16)

    def test_pcap_receive_short_padding_direction_skips_and_drop_counters(self):
        api=PcapFake();events=[];endpoint=self.pcap(api, events)
        outgoing=preset('ARP request')
        incoming=preset('ARP request', direction='rx')
        short=incoming.raw[:42]  # NIC omits padding; retain exact bytes, not synthetic zeros.
        api.queue=[(123, outgoing.raw, len(outgoing.raw)), (124, short, len(short)),
                   (125, incoming.raw, len(incoming.raw)+1), (126, bytes(15), 15)]
        endpoint.poll()
        self.assertEqual([f.direction for f in events], ['tx', 'rx'])
        self.assertEqual(events[-1].raw, short)
        self.assertEqual(endpoint.rejected, 2);self.assertEqual(endpoint.driver_dropped, 3)
        endpoint.send(outgoing);self.assertEqual(api.calls[-1], ('send', outgoing.raw))
        endpoint.close();endpoint.close()
        self.assertEqual(api.calls.count(('close',123)), 1)
        with self.assertRaisesRegex(RuntimeError, 'closed'):endpoint.send(outgoing)

    def test_pcap_failure_and_duplicate_open_release_lease(self):
        with self.assertRaisesRegex(RuntimeError, 'open failed'):self.pcap(PcapFake('open'))
        endpoint=self.pcap()
        with self.assertRaisesRegex(RuntimeError, 'already open'):self.pcap()
        endpoint.close();self.pcap().close()

    def test_intrepid_ram_enable_restore_preserves_phy_and_no_wire_tx_on_connect(self):
        api=IntrepidFake();api.settings.Settings.vcan4.ethernet.link_speed=100
        before=bytes(api.settings);endpoint=self.intrepid(api)
        self.assertEqual(api.settings.Settings.vcan4.network_enables_2, 1<<13)
        self.assertEqual(api.settings.Settings.vcan4.ethernet.link_speed, 100)
        self.assertEqual(api.transmitted, [])
        endpoint.close()
        self.assertEqual(bytes(api.settings), before)
        self.assertTrue(all(not call[1] for call in api.calls if isinstance(call,tuple) and call[0]=='settings'))
        self.assertEqual(api.calls[-1], 'close')

    def test_intrepid_packs_more_than_255_bytes_with_installed_binding(self):
        api=IntrepidFake();endpoint=self.intrepid(api)
        frame=make_frame(LOCAL_MAC,'02:00:00:00:00:02',0x88b5,bytes(range(256))*4,100,3)
        endpoint.send(frame);message=api.transmitted[-1]
        self.assertEqual(message.Protocol, ics.SPY_PROTOCOL_ETHERNET)
        self.assertEqual(message.NetworkID, ics.NETID_ETHERNET)
        self.assertEqual((message.NumberBytesHeader<<8)|message.NumberBytesData, len(frame.raw))
        self.assertEqual(bytes(message.ExtraDataPtr), frame.raw)
        self.assertEqual(message.ExtraDataPtrEnabled, 0)

    def test_intrepid_rx_fcs_timestamp_skip_other_protocols_and_no_duplicate_tx(self):
        api=IntrepidFake();events=[];endpoint=self.intrepid(api,events)
        frame=preset('VLAN UDP',direction='rx')
        def message(raw, flags=0, flags2=0):
            msg=ics.SpyMessage();msg.Protocol=ics.SPY_PROTOCOL_ETHERNET;msg.NetworkID=ics.NETID_ETHERNET
            msg.ExtraDataPtr=tuple(raw);msg.StatusBitField=flags;msg.StatusBitField2=flags2
            return msg
        can=ics.SpyMessage();can.Protocol=ics.SPY_PROTOCOL_CAN
        api.queue=[can, message(frame.raw+b'FCS!',flags2=ics.SPY_STATUS2_ETHERNET_FCS_AVAILABLE),
                   message(frame.raw,ics.SPY_STATUS_TX_MSG),message(frame.raw,flags2=ics.SPY_STATUS2_ETHERNET_CRC_ERROR)]
        with patch('signal_explorer.bus.timestamps.time.time',return_value=1800000000.0):endpoint.poll()
        self.assertEqual(len(events),1);self.assertEqual(events[0].raw, frame.raw)
        self.assertEqual(events[0].timestamp,1800000000.0);self.assertEqual(endpoint.rejected,1)

    def test_intrepid_client_guard_settings_failure_cleanup_and_cross_protocol_lease(self):
        with self.assertRaisesRegex(RuntimeError,'in use'):self.intrepid(IntrepidFake(clients=1))
        api=IntrepidFake(fail='settings')
        with self.assertRaisesRegex(RuntimeError,'settings'):self.intrepid(api)
        self.assertEqual(api.calls[-1],'close')
        endpoint=self.intrepid()
        for protocol in ('CAN','LIN'):
            with self.assertRaisesRegex(RuntimeError,'already open'):DeviceLease('neovi','V2Q475',protocol)
        endpoint.close();self.intrepid().close()

    def test_native_service_no_demo_no_peer_log_independent_of_ui_and_disconnect(self):
        service=EthernetService();api=PcapFake();display=[];traffic=[]
        service.message.connect(display.append);service.traffic.connect(traffic.append)
        factory=lambda config, callback:PcapTransport(config, callback, api=api)
        try:
            with patch('signal_explorer.ethernet.pcap.PcapTransport',side_effect=factory):service.connect_hardware('pcap',PC)
            self.assertFalse(service.simulated);self.assertFalse(service._demo.isActive())
            self.assertTrue(service.send_message(preset('UDP telemetry')))
            self.assertEqual(len(traffic),1)  # No synthetic ACK on hardware.
            self.assertTrue(wait_for(lambda:len(display)==1))
            with self.assertRaisesRegex(ValueError,'only in simulation'):service.inject_test('ARP request')
            with self.assertRaisesRegex(ValueError,'Disconnect'):service.connect_simulated()
            service._ui_timer.stop()
            for _ in range(2010):service._native_event(preset('ARP request',direction='rx'))
            self.assertEqual(service.ui_dropped,10)
            self.assertEqual(len(traffic),2011)  # UI queue limit does not drop recorder events.
            self.assertEqual(len(display),1)
        finally:service.disconnect_bus()
        self.assertIsNone(service._reader);self.assertIsNone(service.transport)
        self.assertEqual(api.calls[-1],('close',123))
        self.assertFalse(service.send_message(preset('UDP telemetry'),False))

    def test_native_receive_failure_stops_reader_and_reports(self):
        service=EthernetService();errors=[];service.error.connect(errors.append)
        api=PcapFake(fail='receive')
        with patch('signal_explorer.ethernet.pcap.PcapTransport',side_effect=lambda c,cb:PcapTransport(c,cb,api=api)):
            service.connect_hardware('pcap',PC)
        try:
            self.assertTrue(wait_for(lambda:not service.is_connected))
            self.assertIn('capture lost',errors[0])
        finally:service.disconnect_bus()

    def test_native_short_frame_log_metadata_and_replay_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'native.mf4';codec=EthernetCodec()
            frame=replace(preset('ARP request',direction='rx'),raw=preset('ARP request',direction='rx').raw[:42])
            writer=codec.writer(path);writer.write(codec.capture(frame));writer.finish(dict(simulated=False,adapter='PC Ethernet'))
            restored=list(codec.read(path));self.assertEqual(restored[0].payload.raw,frame.raw)
            self.assertEqual(len(codec.prepare_tx(restored[0],{}).raw),60)
            with MDF(path) as mdf:
                metadata=mdf.extract_attachment(index=0)[0].decode()
                self.assertIn('"simulated": false',metadata);self.assertNotIn('no physical network transmission',metadata)

    def test_native_logging_and_timed_playback_through_mocked_port(self):
        service=EthernetService();api=PcapFake();codec=EthernetCodec();recorder=MF4Recorder(codec=codec)
        from PySide6.QtCore import Qt
        service.traffic.connect(recorder.capture,Qt.ConnectionType.DirectConnection)
        try:
            with patch('signal_explorer.ethernet.pcap.PcapTransport',side_effect=lambda c,cb:PcapTransport(c,cb,api=api)):
                service.connect_hardware('pcap',PC)
            recorder.start(service.session_info)
            incoming=preset('VLAN UDP',direction='rx');api.queue.append((time.time(),incoming.raw,len(incoming.raw)))
            self.assertTrue(wait_for(lambda:recorder.count==1))
            self.assertTrue(service.send_message(preset('ARP request')))
            recorder.stop();self.assertTrue(wait_for(lambda:recorder.pending_path is not None))
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'native.mf4';recorder.save_as(path)
                events=list(codec.read(path));self.assertEqual([e.direction for e in events],['rx','tx'])
                with MDF(path) as mdf:self.assertIn('"simulated": false',mdf.extract_attachment(index=0)[0].decode())
                worker=MF4Playback(path,lambda frame:service.send_message(frame,False),codec=codec)
                worker.start();self.assertTrue(wait_for(lambda:worker.isFinished()));worker.wait()
                self.assertEqual(worker.sent,2)
                self.assertEqual(sum(call[0]=='send' for call in api.calls),3)
        finally:
            recorder.stop();wait_for(lambda:not recorder.busy)
            if recorder.pending_path:Path(recorder.pending_path).unlink(missing_ok=True)
            service.disconnect_bus()

    def test_disconnect_cancels_hardware_playback_and_repeat(self):
        panel=EthernetPanel();native=EthernetAdapter('PC Ethernet','pcap',PC)
        panel.adapter.addItem(native.label,native);panel.adapter.setCurrentIndex(1)
        api=PcapFake()
        try:
            with patch('signal_explorer.ethernet.pcap.PcapTransport',side_effect=lambda c,cb:PcapTransport(c,cb,api=api)):
                panel.connect_button.click()
            panel.interval.setText('20');panel.send.click();self.assertTrue(panel._repeat.isActive())
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'replay.mf4';now=time.time()
                write_log(path,[replace(preset('ARP request'),timestamp=now),replace(preset('ARP request'),timestamp=now+10)])
                with patch('signal_explorer.ui.recording_panel.QFileDialog.getOpenFileName',return_value=(str(path),'')):
                    panel.recording.replay.click()
                self.assertFalse(panel._repeat.isActive())
                self.assertTrue(wait_for(lambda:panel.recording.playback.sent>=1))
                panel.connect_button.click()
                self.assertTrue(wait_for(lambda:not panel.recording.playback_busy))
                count=sum(call[0]=='send' for call in api.calls)
                wait_for(lambda:False,.05)
                self.assertEqual(sum(call[0]=='send' for call in api.calls),count)
        finally:panel.service.disconnect_bus();panel.close()

    def test_ui_scanning_hardware_selection_and_simulation_controls(self):
        panel=EthernetPanel()
        native=EthernetAdapter('PC Ethernet | Bench','pcap',PC,True,'Bench port')
        unavailable=EthernetAdapter('PC Ethernet | Missing driver','pcap',PC,False,'Install Npcap')
        try:
            with patch('signal_explorer.ui.ethernet_panel.discover_adapters',return_value=([virtual_adapter(),native,unavailable],'scan done')):
                panel.scan.click()
                self.assertEqual(panel.adapter.count(),3)
                native_index=next(i for i in range(panel.adapter.count()) if panel.adapter.itemData(i)==native)
                unavailable_index=next(i for i in range(panel.adapter.count()) if panel.adapter.itemData(i)==unavailable)
                panel.adapter.setCurrentIndex(native_index)
                self.assertFalse(panel.demo.isEnabled());self.assertFalse(panel.peer.isEnabled())
                self.assertTrue(panel.promiscuous.isEnabled())
                self.assertFalse(panel.adapter.model().item(unavailable_index).isEnabled())
                self.assertEqual(panel.recording.replay.text(),'Playback Log as TX')
                panel.scan.click();self.assertEqual(panel.adapter.currentIndex(),native_index)
            api=PcapFake()
            with patch('signal_explorer.ethernet.pcap.PcapTransport',side_effect=lambda c,cb:PcapTransport(c,cb,api=api)):
                panel.connect_button.click()
            self.assertTrue(panel.service.is_connected);self.assertFalse(panel.inject.isEnabled())
            panel.send.click();self.assertTrue(wait_for(lambda:panel.tx==1))
            panel.connect_button.click();panel.adapter.setCurrentIndex(next(i for i in range(panel.adapter.count()) if panel.adapter.itemData(i).backend=='virtual'))
            self.assertTrue(panel.demo.isEnabled());self.assertFalse(panel.promiscuous.isEnabled())
            panel.demo.setChecked(False);panel.connect_button.click();panel.send.click()
            self.assertEqual(panel.rx,1)
        finally:panel.service.disconnect_bus();panel.close()


if __name__=='__main__':unittest.main()
