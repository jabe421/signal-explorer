"""MDF4 container for raw Ethernet frames; explicitly application-specific.

This is NOT presented as the standardized ASAM Ethernet bus-event schema.
Keeps the simulation build's schema for backward-compatible playback.
"""
from dataclasses import replace
import json
import numpy as np
from asammdf import MDF,Signal
from signal_explorer.recording.codecs import BusEvent
from signal_explorer.ethernet.model import EthernetFrame,MAX_FRAME

NAME='SignalExplorer_EthernetFrame'
DTYPE=np.dtype([('BusChannel','u1'),('Direction','u1'),('Length','<u2'),('RawBytes',f'({MAX_FRAME},)u1')])


class EthernetWriter:
    def __init__(self,path,database=None):
        self.path=path;self.mdf=MDF(version='4.10')
        self.mdf.header.author='Signal Explorer'
        self.start=self.mdf.header.start_time.timestamp()
        self.mdf.append(Signal(name=NAME,samples=np.zeros(0,dtype=DTYPE),timestamps=np.zeros(0),
                               comment='Signal Explorer schema v1: raw Ethernet II, no FCS; direction 0=RX, 1=TX; captured padding may be absent.'))

    def write(self,event):
        if event.protocol!='ETHERNET':raise ValueError('Ethernet codec requires Ethernet events.')
        frame=event.payload;row=np.zeros(1,dtype=DTYPE)
        row['BusChannel']=event.channel;row['Direction']=int(event.direction=='tx');row['Length']=len(frame.raw)
        row['RawBytes'][0,:len(frame.raw)]=list(frame.raw)
        self.mdf.extend(0,[(np.array([event.timestamp-self.start]),None),(row,None)])

    def finish(self,metadata):
        simulated=metadata.get('simulated',True)
        metadata.update(schema=NAME,schema_version=1,simulated=simulated,
                        event_extension='Application-specific Ethernet frames; not standardized ASAM Ethernet bus-event interoperability.',
                        tx_semantics=('Accepted by in-process simulator; no physical network transmission' if simulated else
                                      'Accepted by capture/native API; does not confirm physical transmission or reception'),fcs_included=False)
        metadata.setdefault('physical_link',False if simulated else 'not verified')
        self.mdf.attach(json.dumps(metadata,indent=2).encode(),file_name='SignalExplorer-session.json',mime='application/json')
        self.mdf.save(self.path,overwrite=True,compression=2);self.mdf.close()

    def abort(self):self.mdf.close()


class EthernetCodec:
    protocol='ETHERNET'

    def capture(self,frame):return BusEvent(frame.timestamp,frame.channel,frame.direction,self.protocol,frame)
    def writer(self,path,database=None):return EthernetWriter(path)
    def replayable(self,event):return event.protocol==self.protocol

    def prepare_tx(self,event,options):
        if event.protocol!=self.protocol:raise ValueError('Ethernet replay requires a Signal Explorer Ethernet log.')
        return replace(event.payload,raw=event.payload.raw.ljust(60,b'\0'),timestamp=0,direction='tx',channel=1)

    def read(self,path):
        with MDF(path) as mdf:
            groups=[i for i,g in enumerate(mdf.groups) if any(c.name==NAME for c in g.channels)]
            if len(groups)!=1:raise ValueError('Select a Signal Explorer Ethernet MF4 log (schema v1, including simulation logs).')
            group=groups[0];start=mdf.header.start_time.timestamp()
            for offset in range(0,mdf.groups[group].channel_group.cycles_nr,1000):
                signal=mdf.get(NAME,group=group,record_offset=offset,record_count=1000)
                for row,timestamp in zip(signal.samples,signal.timestamps):
                    length=int(row['Length']);direction=int(row['Direction'])
                    if not 14<=length<=MAX_FRAME or direction not in (0,1):raise ValueError('Invalid Ethernet record length/direction.')
                    yield self.capture(EthernetFrame(float(timestamp)+start,bytes(row['RawBytes'][:length]),
                                                     'tx' if direction else 'rx',int(row['BusChannel'])))
