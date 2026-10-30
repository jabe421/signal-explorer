"""Ethernet sessions: in-process simulator or an explicitly selected transport."""
from dataclasses import replace
from threading import RLock
from queue import Queue,Full,Empty
import time
import struct
import ipaddress
from PySide6.QtCore import QObject,QTimer,Signal,QThread
from signal_explorer.ethernet.model import (EthernetFrame,LOCAL_MAC,PEER_MAC,LOCAL_IP,PEER_IP,
                                          PRESETS,preset,make_frame,udp_payload,decode)


class _EthernetReader(QThread):
    failed=Signal(str)

    def __init__(self,transport):
        super().__init__();self.transport=transport

    def run(self):
        try:
            while not self.isInterruptionRequested():
                self.transport.poll()
                self.msleep(5)
        except Exception as exc:self.failed.emit(str(exc))


class EthernetService(QObject):
    connected=Signal(str)
    disconnected=Signal()
    about_to_disconnect=Signal()
    message=Signal(object)
    traffic=Signal(object)
    error=Signal(str)
    statistics_changed=Signal()
    fd_enabled=False

    def __init__(self,parent=None):
        super().__init__(parent)
        self._lock=RLock();self._connected=False;self.peer_replies=True
        self.session_info={};self._counter=0
        self.transport=None;self._reader=None
        self._ui_queue=Queue(maxsize=2000);self.ui_dropped=0
        self._ui_timer=QTimer(self);self._ui_timer.setInterval(20);self._ui_timer.timeout.connect(self._flush_native)
        self._demo=QTimer(self);self._demo.setInterval(100);self._demo.timeout.connect(self._demo_tick)

    @property
    def is_connected(self): return self._connected

    @property
    def simulated(self): return self.session_info.get('simulated',True)

    @property
    def diagnostics(self):
        return dict(ui_dropped=self.ui_dropped,rejected=getattr(self.transport,'rejected',0),
                    driver_dropped=getattr(self.transport,'driver_dropped',0))

    def connect_simulated(self,demo=True,peer_replies=True):
        with self._lock:
            if self._connected: raise ValueError('Disconnect the current Ethernet simulation first.')
            self._connected=True;self.peer_replies=peer_replies;self._counter=0
            self.ui_dropped=0
            self.session_info=dict(protocol='ETHERNET',adapter='Virtual Ethernet (in-process)',
                                   native_channel='Virtual Ethernet 1',simulated=True,
                                   local_mac=LOCAL_MAC,peer_mac=PEER_MAC,mtu=1500,
                                   fcs_included=False,physical_link=False)
            if demo:self._demo.start()
        self.connected.emit('Virtual Ethernet 1 | simulated link')

    def connect_hardware(self,backend,config):
        if self.is_connected:raise ValueError('Disconnect Ethernet before selecting another adapter.')
        if backend=='pcap':
            from signal_explorer.ethernet.pcap import PcapTransport as transport
            label='PC Ethernet / Npcap'
        elif backend=='neovi':
            from signal_explorer.ethernet.intrepid import IntrepidEthernetTransport as transport
            label='Intrepid ValueCAN4-2EL'
        else:raise ValueError('Unknown Ethernet backend.')
        self._ui_queue=Queue(maxsize=2000);self.ui_dropped=0
        try:
            self.transport=transport(config,self._native_event)
            self.session_info=dict(protocol='ETHERNET',adapter=label,simulated=False,
                                   native_channel=config.get('name','Ethernet 1'),serial=config.get('serial'),
                                   local_mac=self.transport.local_mac,mtu=1500,fcs_included=False,
                                   physical_link='not verified',direction_method='NIC source MAC inference' if backend=='pcap' else 'native RX / locally accepted TX')
            self._connected=True
            self._reader=_EthernetReader(self.transport)
            self._reader.failed.connect(self._native_failed)
            self._ui_timer.start()
            self._reader.start()
        except Exception:
            self.disconnect_bus()
            raise
        self.connected.emit(f'{label} | Ethernet session open')

    def _native_event(self,frame):
        # Logger receives every accepted event directly. The bounded display
        # queue prevents a busy NIC from flooding Qt's queued signal delivery.
        self.traffic.emit(frame)
        try:self._ui_queue.put_nowait(frame)
        except Full:self.ui_dropped+=1

    def _flush_native(self,limit=100):
        for _ in range(limit):
            try:frame=self._ui_queue.get_nowait()
            except Empty:break
            self.message.emit(frame)
        self.statistics_changed.emit()

    def _native_failed(self,text):
        if self.sender() is not self._reader:return
        self.disconnect_bus()
        self.error.emit(f'Ethernet receive stopped: {text}')

    def _emit(self,frame):
        self.traffic.emit(frame);self.message.emit(frame)

    def _demo_tick(self):
        with self._lock:
            if not self._connected:return
            self._counter+=1
            self._emit(preset(PRESETS[(self._counter-1)%len(PRESETS)],self._counter,'rx'))

    def inject_test(self,name):
        with self._lock:
            if not self._connected:raise ValueError('Connect the Ethernet simulation first.')
            if not self.simulated:raise ValueError('Inject Test RX is available only in simulation.')
            self._counter+=1;self._emit(preset(name,self._counter,'rx'))

    def send_message(self,frame,report_errors=True):
        with self._lock:
            try:
                if not self._connected:raise ValueError('Connect the Ethernet simulation first.')
                if not isinstance(frame,EthernetFrame):raise ValueError('Expected an Ethernet frame.')
                frame=replace(frame,raw=frame.raw.ljust(60,b'\0'),timestamp=time.time(),direction='tx',channel=1)
                if self.transport is not None:
                    self.transport.send(frame)
                    self._native_event(frame)
                    return True
                self._emit(frame)
                reply=self._reply(frame) if self.peer_replies else None
                if reply:self._emit(reply)
                return True
            except Exception as exc:
                if report_errors:self.error.emit(str(exc))
                return False

    def _reply(self,frame):
        # The peer is deterministic, has no timers to outlive a disconnect, and
        # never responds recursively to its own reply. No network I/O occurs.
        if frame.destination!=PEER_MAC.upper():return None
        protocol,summary=decode(frame)
        if protocol=='UDP' and ' | ' in summary:
            data=frame.payload;ihl=(data[0]&15)*4
            sport,dport,length,_=struct.unpack('!HHHH',data[ihl:ihl+8])
            if data[16:20]!=ipaddress.IPv4Address(PEER_IP).packed:return None
            payload=udp_payload(b'ACK:'+data[ihl+8:ihl+length][:128],
                                str(ipaddress.IPv4Address(data[16:20])),str(ipaddress.IPv4Address(data[12:16])),dport,sport)
        elif frame.ethertype==0x88b5:
            payload=b'ACK:'+frame.payload.rstrip(b'\0')[:128]
        else:return None
        return make_frame(PEER_MAC,frame.source,frame.ethertype,payload,
                          vlan=frame.vlan,pcp=frame.pcp or 0,direction='rx')

    def disconnect_bus(self):
        # Playback cancellation is signalled before acquiring the send lock.
        if self._connected:self.about_to_disconnect.emit()
        self._demo.stop()
        self._ui_timer.stop()
        # Mark disconnected under the send lock, then join without holding it.
        with self._lock:self._connected=False
        if self._reader:
            self._reader.requestInterruption();self._reader.wait();self._reader=None
        if self.transport is not None:self._flush_native(limit=2000)
        with self._lock:
            transport,self.transport=self.transport,None
            try:
                if transport is not None:transport.close()
            except Exception as exc:self.error.emit(f'Ethernet cleanup failed: {exc}')
        self.disconnected.emit()
