"""Ethernet II frames, a single 802.1Q tag, and small deterministic test packets.

Raw bytes exclude preamble/SFD/FCS. Captured NIC frames may omit minimum-size
padding. Transmit builders pad to 60 bytes; payload MTU is 1500 bytes.
"""
from dataclasses import dataclass
import ipaddress
import re
import struct
import time

LOCAL_MAC = '02:00:00:00:00:01'
PEER_MAC = '02:00:00:00:00:02'
LOCAL_IP = '192.0.2.1'
PEER_IP = '192.0.2.2'
MAX_FRAME = 1518  # Single VLAN tag, no FCS.


def mac_bytes(value):
    if not re.fullmatch(r'[0-9a-fA-F]{2}([:-])[0-9a-fA-F]{2}(?:\1[0-9a-fA-F]{2}){4}', value.strip()):
        raise ValueError('MAC address must contain six hex bytes, e.g. 02:00:00:00:00:01.')
    return bytes.fromhex(value.strip().replace(':','').replace('-',''))


def mac_text(value):
    return ':'.join(f'{byte:02X}' for byte in value)


def checksum(data):
    if len(data) % 2: data += b'\0'
    total = sum(struct.unpack(f'!{len(data)//2}H', data))
    while total >> 16: total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def udp_payload(data, source=LOCAL_IP, destination=PEER_IP, source_port=50000, destination_port=50001):
    data=bytes(data)
    if len(data)>1472: raise ValueError('UDP test data exceeds the standard MTU.')
    src,dst=ipaddress.IPv4Address(source).packed,ipaddress.IPv4Address(destination).packed
    udp=struct.pack('!HHHH',source_port,destination_port,8+len(data),0)+data
    pseudo=src+dst+struct.pack('!BBH',0,17,len(udp))
    crc=checksum(pseudo+udp) or 0xffff
    udp=udp[:6]+struct.pack('!H',crc)+udp[8:]
    ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(udp),0,0x4000,64,17,0,src,dst)
    ip=ip[:10]+struct.pack('!H',checksum(ip))+ip[12:]
    return ip+udp


@dataclass(frozen=True)
class EthernetFrame:
    timestamp: float
    raw: bytes
    direction: str = 'rx'
    channel: int = 1

    def __post_init__(self):
        object.__setattr__(self,'raw',bytes(self.raw))
        if self.direction not in {'rx','tx'}: raise ValueError('Ethernet direction must be rx or tx.')
        if not 14 <= len(self.raw) <= MAX_FRAME: raise ValueError('Captured Ethernet frames require 14-1518 bytes without FCS.')
        if not 0 <= self.channel <= 255: raise ValueError('Ethernet channel must be 0-255.')
        if self.raw[6]&1 or not any(self.raw[6:12]): raise ValueError('Source MAC must be a nonzero unicast address.')
        outer=struct.unpack('!H',self.raw[12:14])[0]
        if outer==0x88a8: raise ValueError('Q-in-Q/service tags are not supported.')
        if outer==0x8100 and len(self.raw)<18: raise ValueError('Incomplete VLAN header.')
        if self.ethertype < 0x0600 or self.ethertype in (0x8100,0x88a8):
            raise ValueError('Use an Ethernet II EtherType (0600-FFFF); stacked VLAN tags/802.3 length frames are not supported.')
        if self.vlan == 4095: raise ValueError('VLAN ID 4095 is reserved.')
        if len(self.payload)>1500: raise ValueError('Ethernet payload exceeds 1500 bytes.')

    @property
    def destination(self): return mac_text(self.raw[:6])
    @property
    def source(self): return mac_text(self.raw[6:12])
    @property
    def tagged(self): return self.raw[12:14]==b'\x81\x00'
    @property
    def vlan(self): return (int.from_bytes(self.raw[14:16],'big') & 0xfff) if self.tagged else None
    @property
    def pcp(self): return self.raw[14] >> 5 if self.tagged else None
    @property
    def ethertype(self): return int.from_bytes(self.raw[16:18] if self.tagged else self.raw[12:14],'big')
    @property
    def payload(self): return self.raw[18:] if self.tagged else self.raw[14:]


def make_frame(source, destination, ethertype, payload=b'', vlan=None, pcp=0, direction='tx', timestamp=None):
    src,dst=mac_bytes(source),mac_bytes(destination)
    if src[0] & 1 or not any(src): raise ValueError('Source MAC must be a nonzero unicast address.')
    if not 0x0600<=ethertype<=0xffff or ethertype in (0x8100,0x88a8):
        raise ValueError('Select an inner EtherType 0600-FFFF, not a VLAN TPID.')
    if not 0<=pcp<=7 or (vlan is not None and not 0<=vlan<=4094): raise ValueError('VLAN must be 0-4094 and PCP 0-7.')
    payload=bytes(payload)
    if len(payload)>1500: raise ValueError('Ethernet payload must not exceed 1500 bytes.')
    header=dst+src
    if vlan is not None: header+=struct.pack('!HH',0x8100,(pcp<<13)|vlan)
    raw=header+struct.pack('!H',ethertype)+payload
    raw=raw.ljust(60,b'\0')
    return EthernetFrame(time.time() if timestamp is None else timestamp,raw,direction)


def decode(frame):
    """Display-only summary. Invalid packet payloads stay visible as raw frames."""
    data=frame.payload
    if frame.ethertype==0x0800:
        if len(data)<20 or data[0]>>4!=4: return 'IPv4','Malformed IPv4 header'
        ihl=(data[0]&15)*4;total=int.from_bytes(data[2:4],'big')
        if ihl<20 or not ihl<=total<=len(data): return 'IPv4','Malformed IPv4 length'
        src,dst=str(ipaddress.IPv4Address(data[12:16])),str(ipaddress.IPv4Address(data[16:20]))
        if checksum(data[:ihl]): return 'IPv4',f'{src} -> {dst}; bad IPv4 checksum'
        if int.from_bytes(data[6:8],'big') & 0x3fff: return 'IPv4',f'{src} -> {dst}; fragment (no reassembly)'
        if data[9]==17:
            if total<ihl+8: return 'UDP','Malformed UDP header'
            sport,dport,length,crc=struct.unpack('!HHHH',data[ihl:ihl+8])
            if length<8 or ihl+length!=total: return 'UDP','Malformed UDP length'
            pseudo=data[12:20]+struct.pack('!BBH',0,17,length)
            if crc and checksum(pseudo+data[ihl:ihl+length]): return 'UDP',f'{src}:{sport} -> {dst}:{dport}; bad UDP checksum'
            body=data[ihl+8:ihl+length].decode('ascii',errors='replace')
            body=''.join(c if c.isprintable() else '.' for c in body)
            return 'UDP',f'{src}:{sport} -> {dst}:{dport} | {body[:110]}'
        return {6:'TCP',1:'ICMP'}.get(data[9],'IPv4'),f'{src} -> {dst}; IP protocol {data[9]}'
    if frame.ethertype==0x0806:
        if len(data)<28 or data[:8] not in (bytes.fromhex('0001080006040001'),bytes.fromhex('0001080006040002')):
            return 'ARP','Unsupported or malformed ARP'
        return 'ARP',f'{"Request" if data[7]==1 else "Reply"}: {ipaddress.IPv4Address(data[14:18])} -> {ipaddress.IPv4Address(data[24:28])}'
    if frame.ethertype==0x86dd: return 'IPv6','Raw IPv6 payload; no higher-layer decoder'
    return f'0x{frame.ethertype:04X}',''.join(chr(b) if 32<=b<127 else '.' for b in data[:110])


PRESETS=('UDP telemetry','ARP request','VLAN UDP','Raw test (88B5)')


def preset(name, counter=0, direction='tx'):
    src,dst=(LOCAL_MAC,PEER_MAC) if direction=='tx' else (PEER_MAC,LOCAL_MAC)
    sip,dip=(LOCAL_IP,PEER_IP) if direction=='tx' else (PEER_IP,LOCAL_IP)
    if name in ('UDP telemetry','VLAN UDP'):
        body=f'EngineRPM={1800+counter%1000}; MAF={12+counter%20}; Counter={counter}'.encode()
        return make_frame(src,dst,0x0800,udp_payload(body,sip,dip),vlan=100 if name=='VLAN UDP' else None,pcp=3 if name=='VLAN UDP' else 0,direction=direction)
    if name=='ARP request':
        payload=struct.pack('!HHBBH',1,0x0800,6,4,1)+mac_bytes(src)+ipaddress.IPv4Address(sip).packed+bytes(6)+ipaddress.IPv4Address(dip).packed
        return make_frame(src,'FF:FF:FF:FF:FF:FF',0x0806,payload,direction=direction)
    if name=='Raw test (88B5)': return make_frame(src,dst,0x88b5,f'Signal Explorer test {counter}'.encode(),direction=direction)
    raise ValueError('Unknown Ethernet test frame.')
