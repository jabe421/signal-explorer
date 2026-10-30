import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import ctypes as C
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import ics
from ics.structures.s_device_settings import s_device_settings
from ics.structures.e_device_settings_type import e_device_settings_type
from PySide6.QtWidgets import QApplication

from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.bus.service import BusService
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.discovery import ChannelOption
from signal_explorer.lin.model import Response, protected_id, lin_checksum
from signal_explorer.lin.vector import VectorLinTransport, VectorLinAPI, Event, Parameters, LinMessage, discover_vector
from signal_explorer.lin.intrepid import IntrepidLinTransport, discover_intrepid
from signal_explorer.lin.service import LinService
from signal_explorer.ui.lin_panel import LinPanel

app=QApplication.instance() or QApplication([])
LENGTHS={16:2,32:4,60:8,61:8}
MODELS={i:'enhanced' for i in LENGTHS}


class VectorFake:
    def __init__(self, denied=False, fail=None):
        self.calls=[];self.queue=[];self.denied=denied;self.fail=fail
        self.functions={'xlReceive':self.receive}

    def call(self,name,*args):
        self.calls.append((name,args))
        if name==self.fail:raise RuntimeError('Injected '+name)
        if name=='xlOpenPort':
            args[0]._obj.value=12
            if self.denied:args[3]._obj.value=0
        if name=='xlGetSyncTime':args[1]._obj.value=1_000_000_000

    def receive(self,port,count,event):
        if not self.queue:return 10
        C.memmove(event,C.byref(self.queue.pop(0)),C.sizeof(Event))
        return 0


class IntrepidFake:
    def __init__(self, clients=0, fail=None):
        self.device=SimpleNamespace(Name='ValueCAN4-2EL',DeviceType=10,SerialNumber=1879029185,NumberOfClients=clients)
        self.calls=[];self.transmitted=[];self.queue=[];self.fail=fail
        self.settings=s_device_settings()
        self.settings.DeviceSettingType=e_device_settings_type.DeviceVCAN4SettingsType
        self.settings.Settings.vcan4.lin1.Baudrate=10400
        self.settings.Settings.vcan4.lin1.MasterResistor=1
        self.settings.Settings.vcan4.network_enables=3

    def __getattr__(self,name):return getattr(ics,name)
    def find_devices(self):return [self.device]
    def open_device(self,device):self.calls.append('open')
    def close_device(self,device):self.calls.append('close')
    def get_device_settings(self,device):return deepcopy(self.settings)
    def set_device_settings(self,device,settings,save):
        self.calls.append(('settings',save));self.settings=deepcopy(settings)
        if self.fail=='settings':raise RuntimeError('Injected settings')
    def enable_network_com(self,device,enabled,network):self.calls.append(('network',enabled,network))
    def transmit_messages(self,device,message):
        if self.fail=='transmit':raise RuntimeError('Injected transmit')
        # SpyMessage extension objects are not deepcopyable.
        self.transmitted.append(message)
    def get_messages(self,device,j1850,timeout):
        result,self.queue=self.queue,[]
        return result,0
    def get_timestamp_for_msg(self,device,message):return 100.25


class NativeLINTests(unittest.TestCase):
    def vector(self,role='master',api=None):
        self.api=api or VectorFake();self.events=[]
        result=VectorLinTransport(dict(channel=3,channel_index=4,mask=16,serial=111),role,19200,LENGTHS,MODELS,self.events.append,api=self.api)
        self.addCleanup(result.close)
        return result

    def intrepid(self,role='master',api=None):
        self.api=api or IntrepidFake();self.events=[]
        result=IntrepidLinTransport(dict(channel=1,serial='V2Q475'),role,19200,LENGTHS,MODELS,self.events.append,api=self.api)
        self.addCleanup(result.close)
        return result

    def test_vector_abi_and_configuration(self):
        self.assertEqual((C.sizeof(Event),C.sizeof(Parameters),C.sizeof(LinMessage)),(48,16,13))
        self.assertEqual(Event.tagData.offset,16)
        endpoint=self.vector()
        params=next(args[2] for name,args in self.api.calls if name=='xlLinSetChannelParams')
        self.assertEqual((params.LINMode,params.baudrate,params.LINVersion),(1,19200,3))
        activation=next(args for name,args in self.api.calls if name=='xlActivateChannel')
        self.assertEqual(activation[2:],(2,0))
        endpoint.header(32)
        self.assertEqual(self.api.calls[-1][0],'xlLinSendRequest')
        self.assertEqual(self.api.calls[-1][1][2],32) # XL uses six-bit ID, not PID.

    def test_vector_native_slave_buffer_and_removal(self):
        endpoint=self.vector('slave')
        endpoint.set_response(16,Response(b'\1\2'))
        calls=self.api.calls
        self.assertEqual(calls[-2][0],'xlLinSetSlave')
        self.assertEqual(calls[-2][1][-1],0x200)
        self.assertEqual(calls[-1][1][-1],255)
        endpoint.remove_response(16)
        self.assertEqual(self.api.calls[-1][1][-1],0)
        with self.assertRaises(ValueError):endpoint.header(32)
        self.assertNotIn('xlLinSendRequest',[name for name,_ in calls])

    def test_vector_monitor_guards_and_sleep_wake(self):
        endpoint=self.vector('monitor')
        for callback in (lambda:endpoint.header(32),lambda:endpoint.set_response(32,Response(bytes(4))),endpoint.sleep,endpoint.wake):
            with self.assertRaises(ValueError):callback()
        endpoint.close()
        endpoint=self.vector('master');endpoint.sleep();endpoint.wake()
        self.assertEqual([name for name,_ in self.api.calls[-2:]],['xlLinSetSleepMode','xlLinWakeUp'])

    def test_vector_permission_failure_releases_port_and_lease(self):
        api=VectorFake(denied=True)
        with self.assertRaisesRegex(RuntimeError,'access denied'):self.vector(api=api)
        names=[name for name,_ in api.calls]
        self.assertNotIn('xlLinSetChannelParams',names)
        self.assertEqual(names[-2:],['xlClosePort','xlCloseDriver'])
        self.vector().close() # No stale lease after failed initialization.

    def test_vector_receive_tx_rx_crc_no_response_and_overrun(self):
        endpoint=self.vector('slave');endpoint.set_response(16,Response(b'\1\2'))
        event=Event(tag=20,chanIndex=4,timeStamp=1_500_000_000)
        msg=event.tagData.lin;msg.id=16;msg.dlc=2;msg.flags=0x40;msg.data[:2]=b'\1\2';msg.crc=0xac
        self.api.queue.append(event);endpoint.poll()
        self.assertEqual([e.kind for e in self.events],['header','frame'])
        self.assertEqual(self.events[-1].direction,'tx')
        self.assertEqual(self.events[-1].channel,3)
        self.assertEqual(self.events[-1].timestamp,endpoint._epoch+1.5)
        event.tagData.lin.flags=0x81;endpoint._receive(event)
        self.assertEqual(self.events[-1].kind,'checksum_error')
        event.tag=23;event.tagData.raw[0]=32;endpoint._receive(event)
        self.assertEqual(self.events[-1].kind,'no_response')
        event.flags=1
        with self.assertRaisesRegex(RuntimeError,'overrun'):endpoint._receive(event)

    def test_intrepid_temporary_settings_restore_and_master_resistor(self):
        endpoint=self.intrepid()
        self.assertEqual(self.api.settings.Settings.vcan4.lin1.Baudrate,19200)
        self.assertEqual(self.api.settings.Settings.vcan4.lin1.MasterResistor,0)
        self.assertEqual(self.api.settings.Settings.vcan4.network_enables,7)
        self.assertEqual(len(self.api.transmitted),62)
        self.assertTrue(all(not m.StatusBitField and m.NumberBytesHeader==1 and m.NumberBytesData==0 for m in self.api.transmitted))
        endpoint.close()
        self.assertEqual(self.api.settings.Settings.vcan4.lin1.Baudrate,10400)
        self.assertEqual(self.api.settings.Settings.vcan4.network_enables,3)
        self.assertTrue(all(not call[1] for call in self.api.calls if isinstance(call,tuple) and call[0]=='settings'))
        self.assertEqual(self.api.calls[-1],'close')

    def test_intrepid_master_header_and_combined_data_checksum(self):
        endpoint=self.intrepid()
        endpoint.header(32);message=self.api.transmitted[-1]
        self.assertEqual(message.Header,(0x20,))
        self.assertEqual(message.NumberBytesData,0)
        self.assertEqual(message.StatusBitField,ics.SPY_STATUS_INIT_MESSAGE)
        endpoint.set_response(16,Response(b'\1\2'));endpoint.header(16)
        message=self.api.transmitted[-1]
        self.assertEqual(message.Header,(0x50,1,2));self.assertEqual(message.Data,(0xac,))
        endpoint.set_response(60,Response(bytes(range(8)),'enhanced'));endpoint.header(60)
        message=self.api.transmitted[-1]
        self.assertEqual(len(message.Header),3);self.assertEqual(len(message.Data),7)
        self.assertEqual(message.Data[-1],lin_checksum(60,bytes(range(8)),'classic'))

    def test_intrepid_slave_buffer_clear_and_monitor_no_wire_requests(self):
        endpoint=self.intrepid('slave')
        endpoint.set_response(32,Response(bytes(4)))
        message=self.api.transmitted[-1]
        self.assertEqual(message.StatusBitField,0)
        endpoint.remove_response(32);message=self.api.transmitted[-1]
        self.assertEqual((message.Header,message.Data,message.StatusBitField),((0x20,),(),0))
        endpoint.close();endpoint=self.intrepid('monitor')
        self.assertEqual(self.api.settings.Settings.vcan4.lin1.MasterResistor,1)
        with self.assertRaises(ValueError):endpoint.set_response(32,Response(bytes(4)))
        with self.assertRaises(ValueError):endpoint.header(32)
        with self.assertRaises(NotImplementedError):endpoint.wake()

    def test_intrepid_receive_packing_and_host_anchored_time(self):
        endpoint=self.intrepid('slave');endpoint.set_response(16,Response(b'\1\2'))
        message=ics.SpyMessageJ1850();message.Header=(0x50,1,2);message.Data=(0xac,)
        message.Protocol=ics.SPY_PROTOCOL_LIN;message.NetworkID=ics.NETID_LIN;message.StatusBitField=ics.SPY_STATUS_TX_MSG
        self.api.queue=[message]
        with patch('signal_explorer.bus.timestamps.time.time',return_value=1800000000.0):endpoint.poll()
        self.assertEqual(self.events[-1].data,b'\1\2')
        self.assertEqual(self.events[-1].checksum,0xac)
        self.assertEqual(self.events[-1].direction,'tx')
        self.assertEqual(self.events[-1].timestamp,1800000000.0)
        message.StatusBitField2=ics.SPY_STATUS2_LIN_ERR_MSG_ID_PARITY
        endpoint._receive(message);self.assertEqual(self.events[-1].kind,'parity_error')
        message.StatusBitField2=ics.SPY_STATUS2_LIN_NO_SLAVE_DATA
        message.Header=(0x20,);message.Data=()
        endpoint._receive(message);self.assertEqual(self.events[-1].kind,'no_response')

    def test_intrepid_setup_failure_and_occupied_guard(self):
        api=IntrepidFake(clients=1)
        with self.assertRaisesRegex(RuntimeError,'in use'):self.intrepid(api=api)
        self.assertEqual(api.calls,[])
        api=IntrepidFake(fail='transmit')
        with self.assertRaisesRegex(RuntimeError,'transmit'):self.intrepid(api=api)
        self.assertEqual(api.calls[-1],'close')
        self.intrepid().close()

    def test_vendor_discovery_filters_can_only_hardware(self):
        el=IntrepidFake().device
        can=SimpleNamespace(Name='ValueCAN4-2',DeviceType=ics.NEODEVICE_VCAN42,SerialNumber=12)
        with patch('ics.find_devices',return_value=[can,el]):
            options,note=discover_intrepid()
        self.assertEqual(len(options),1);self.assertEqual(options[0].config['serial'],'V2Q475')
        with patch('signal_explorer.lin.vector.VectorLinAPI',side_effect=OSError('missing XL')):
            self.assertEqual(discover_vector(),([], 'missing XL'))

    def test_vector_discovery_uses_lin_capability_and_physical_channel(self):
        from can.interfaces.vector.xlclass import XLdriverConfig
        api=VectorLinAPI.__new__(VectorLinAPI);api.Config=XLdriverConfig
        def call(name,*args):
            if name=='xlGetDriverConfig':
                config=args[0]._obj;config.channelCount=2
                for i in range(2):
                    ch=config.channel[i];ch.name=b'VN1640';ch.serialNumber=100
                    ch.channelIndex=i+4;ch.hwChannel=i+2;ch.channelMask=1<<(i+4)
                config.channel[0].channelBusCapabilities=1<<16 # CAN only.
                config.channel[1].channelBusCapabilities=2<<16
                config.channel[1].transceiverName=b'LIN piggy'
        api.call=call
        channels=api.channels()
        self.assertEqual(len(channels),1)
        self.assertEqual(channels[0].config,dict(channel=4,channel_index=5,mask=32,serial=100))

    def test_cleanup_failures_still_close_ports_and_restore_settings(self):
        endpoint=self.vector();self.api.fail='xlDeactivateChannel'
        with self.assertRaisesRegex(RuntimeError,'xlDeactivateChannel'):endpoint.close()
        self.assertEqual([n for n,_ in self.api.calls[-2:]],['xlClosePort','xlCloseDriver'])
        endpoint=self.intrepid('slave');api=self.api;api.fail='transmit'
        with self.assertRaisesRegex(RuntimeError,'transmit'):endpoint.close()
        self.assertEqual(api.settings.Settings.vcan4.lin1.Baudrate,10400)
        self.assertEqual(api.calls[-1],'close')
        self.assertEqual([c for c in api.calls if isinstance(c,tuple) and c[0]=='network'][-1],('network',False,16))

    def test_partial_settings_failure_restores_original(self):
        api=IntrepidFake(fail='settings')
        with self.assertRaisesRegex(RuntimeError,'settings'):self.intrepid(api=api)
        self.assertEqual(api.settings.Settings.vcan4.lin1.Baudrate,10400)
        self.assertEqual(api.calls[-1],'close')

    def test_can_lease_blocks_native_lin_and_wildcard_blocks_unknown_device(self):
        lease=DeviceLease('neovi','V2Q475','CAN')
        try:
            with self.assertRaisesRegex(RuntimeError,'already open'):self.intrepid()
            with self.assertRaisesRegex(RuntimeError,'already open'):DeviceLease('neovi',None,'LIN')
            independent=DeviceLease('neovi','OTHER','LIN');independent.release()
        finally:lease.release()

    def test_intrepid_receive_checksum_in_ack_and_master_external_response(self):
        endpoint=self.intrepid()
        message=ics.SpyMessageJ1850();message.Header=(0x50,1,2);message.Data=();message.AckBytes=(0xac,)
        message.StatusBitField=ics.SPY_STATUS_INIT_MESSAGE|ics.SPY_STATUS_TX_MSG
        endpoint._receive(message)
        self.assertEqual(self.events[-2].direction,'tx') # Header originated here.
        self.assertEqual(self.events[-1].direction,'rx') # Response is externally published.
        self.assertEqual(self.events[-1].kind,'frame')
        self.assertEqual(self.events[-1].checksum,0xac)

    def test_device_ownership_can_lin_conflict_and_failed_can_release(self):
        lease=DeviceLease('vector',111,'LIN');self.addCleanup(lease.release)
        service=BusService();self.addCleanup(service.disconnect_bus)
        backend=next(b for b in BACKENDS if b.interface=='vector')
        errors=[];service.error.connect(errors.append)
        with patch('signal_explorer.bus.service.can.Bus') as bus:
            service.connect_bus(backend,'0',500000,False,{'serial':111})
            bus.assert_not_called()
        self.assertIn('already open',errors[-1])
        lease.release()
        with patch('signal_explorer.bus.service.can.Bus',side_effect=RuntimeError('failed')):
            service.connect_bus(backend,'0',500000,False,{'serial':111})
        other=DeviceLease('vector',111,'LIN');other.release()

    def test_ui_routes_vector_and_intrepid_and_keeps_peak_and_virtual(self):
        panel=LinPanel();self.addCleanup(panel.close)
        self.assertEqual({panel.adapter.itemData(i) for i in range(panel.adapter.count())},{'virtual','vector','neovi','peak'})
        for adapter,module in [('vector','vector'),('neovi','intrepid')]:
            option=ChannelOption('Test LIN channel',dict(channel=1,serial=111))
            with patch(f'signal_explorer.lin.{module}.discover_{module}',return_value=([option],'Found')):
                panel.adapter.setCurrentIndex(panel.adapter.findData(adapter))
            with patch.object(panel.service,'connect_hardware') as connect:
                panel._toggle_connection()
            self.assertEqual(connect.call_args.args[0],adapter)
            self.assertEqual(connect.call_args.args[1],option.config)

    def test_service_routes_native_factories(self):
        for adapter,module,classname in [('vector','vector','VectorLinTransport'),('neovi','intrepid','IntrepidLinTransport')]:
            service=LinService();self.addCleanup(service.disconnect_bus)
            endpoint=SimpleNamespace(responses={},poll=lambda:None,close=lambda:None)
            with patch(f'signal_explorer.lin.{module}.{classname}',return_value=endpoint) as factory:
                service.connect_hardware(adapter,dict(channel=1,serial=111),'monitor',19200)
            self.assertTrue(service.is_connected);self.assertEqual(factory.call_args.args[1],'monitor')
            self.assertFalse(service.session_info['simulated']);service.disconnect_bus()
