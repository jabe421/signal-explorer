import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from types import SimpleNamespace as NS
from threading import Event, Thread
from unittest.mock import patch
import time
import can
import pytest
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.health import BusHealth, frame_time_range
from signal_explorer.bus.health_native import query_health, install_vector_events, kvaser_health
from signal_explorer.bus.service import BusService
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.ui.main_window import MainWindow
from test_multichannel import wait_for, LoopBus

app=QApplication.instance() or QApplication([])
VIRTUAL=next(b for b in BACKENDS if b.interface=='virtual')


def health_model():
    clock=[100.0]; health=BusHealth(lambda:clock[0])
    health.start([dict(native_channel=0,label='CAN1'),dict(native_channel=1,label='CAN2')],
                 dict(bitrate=500000,data_bitrate=2000000,interface='virtual'))
    return health,clock


def test_classic_length_remote_extended_and_fd_rate_split():
    standard=can.Message(arbitration_id=1,is_extended_id=False,data=bytes(8))
    lo,hi=frame_time_range(standard,500000,2000000)
    assert lo==111/500000 and hi==135/500000
    extended=can.Message(arbitration_id=1,is_extended_id=True,data=bytes(8))
    assert frame_time_range(extended,500000,2000000)[0]==131/500000
    remote=can.Message(arbitration_id=1,is_extended_id=False,is_remote_frame=True,dlc=8)
    assert frame_time_range(remote,500000,2000000)[0]==47/500000
    fd=can.Message(is_fd=True,bitrate_switch=True,data=bytes(64))
    fast=frame_time_range(fd,500000,2000000)[0]
    fd.bitrate_switch=False
    assert fast<frame_time_range(fd,500000,2000000)[0]
    assert frame_time_range(can.Message(is_error_frame=True),500000,2000000)==(0,0)


def test_counts_rates_expiry_bounds_and_no_inferred_hardware_counters():
    health,clock=health_model()
    for i in range(100):
        clock[0]=100+i*.01
        health.observe(can.Message(channel=0,is_rx=True,is_extended_id=False,data=bytes(8)))
    clock[0]=101
    row=health.snapshot()['rows'][0]
    assert row['rx']==100 and row['rate']==100
    assert row['load_low']==pytest.approx(2.22) and row['native']=={}
    assert health.snapshot()['rows'][1]['rx']==0
    for i in range(1000):
        clock[0]+=1
        health.observe(can.Message(channel=0,data=[1]))
    assert len(health.channels['0'].buckets)<=21
    clock[0]+=3
    assert health.snapshot()['rows'][0]['load_low']==0


def test_error_frames_send_failures_native_events_and_reset_are_separate():
    health,clock=health_model()
    health.observe(can.Message(channel=0,is_error_frame=True))
    health.send_failed(0,'adapter queue full')
    health.native_update(0,dict(tec=129,rec=7,state='Error passive'))
    health.native_update(0,dict(last_error='native error'),error_event=True)
    row=health.snapshot()['rows'][0]
    assert (row['rx'],row['error_frames'],row['native_events'],row['send_failures'])==(0,1,1,1)
    health.reset_observations();row=health.snapshot()['rows'][0]
    assert row['error_frames']==0 and row['send_failures']==0 and row['native']['tec']==129
    clock[0]+=5
    assert health.snapshot()['rows'][0]['native_age']==5
    health.stop(); frozen=health.snapshot();clock[0]+=100
    assert health.snapshot()==frozen
    old=health.generation
    health.start([dict(native_channel=0,label='CAN1')],dict(bitrate=500000))
    health.native_update(0,dict(tec=200),old)
    assert health.snapshot()['rows'][0]['native']=={}


def test_vector_classic_fd_hooks_route_without_draining_or_sending():
    health,clock=health_model(); seen=[]
    bus=NS(index_to_channel={7:0,8:1},handle_can_event=lambda e:seen.append(e),handle_canfd_event=lambda e:seen.append(e))
    install_vector_events(bus,health,health.generation)
    classic=NS(tag=4,chanIndex=7,tagData=NS(chipState=NS(busStatus=2,txErrorCounter=130,rxErrorCounter=1)))
    bus.handle_can_event(classic)
    fd=NS(tag=1033,chanIndex=8,tagData=NS(canChipState=NS(busStatus=1,txErrorCounter=255,rxErrorCounter=9)))
    bus.handle_canfd_event(fd)
    clock[0]+=5
    bus.handle_canfd_event(NS(tag=1025,chanIndex=8,tagData=NS(canError=NS(errorCode=3))))
    rows=health.snapshot()['rows']
    assert rows[0]['native']['tec']==130 and rows[1]['native']['tec'] is None
    assert rows[1]['native_events']==1 and rows[1]['native_age']==5 and len(seen)==3


@pytest.mark.parametrize('status,state',[(0,'No bus-error flag'),(0x10,'Bus off'),(0x40000,'Error passive'),(0x8,'Error warning'),(0x200,'Status fault')])
def test_pcan_status_is_not_a_numeric_error_counter(status,state):
    sample=query_health(NS(status=lambda:status),'pcan')
    assert sample['state']==state and 'tec' not in sample and 'rec' not in sample
    assert query_health(NS(status=lambda:0x40),'pcan')['overrun_flag']


def test_ni_bus_off_counter_validity_and_nonzero_rec():
    comm=NS(state=NS(name='BUS_OFF'),tx_err_count=255,rx_err_count=4,last_err=NS(name='ACK'))
    sample=query_health(NS(_session_send=NS(can_comm=comm)),'nixnet')
    assert sample['tec'] is None and sample['rec']==4 and sample['state']=='Bus Off'


def test_kvaser_native_units_and_status_masks_without_loading_any_driver():
    class Function:
        def __init__(self,callback):self.callback=callback
        def __call__(self,*args):return self.callback(*args)
    def counters(handle,tec,rec,overruns):
        tec._obj.value=5;rec._obj.value=3;overruns._obj.value=2;return 0
    def status(handle,flags):flags._obj.value=2;return 0
    library=NS(canReadErrorCounters=Function(counters),canReadStatus=Function(status))
    bus=NS(_write_handle=10,get_stats=lambda:NS(bus_load=3456,overruns=2))
    with patch('signal_explorer.bus.health_native.kvaser_library',return_value=library):sample=kvaser_health(bus)
    assert sample['driver_load']==34.56 and sample['rec']==3 and sample['overruns']==2
    assert sample['state']=='Bus off' and sample['tec'] is None


def test_virtual_health_continues_while_trace_paused_or_filtered_and_reuses_window():
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=([],'')):
        window=MainWindow()
    service=window._service; peer=can.Bus(interface='virtual',channel='health-ui',ignore_config=True)
    try:
        service.connect_bus(VIRTUAL,'health-ui',500000,False)
        health_window=window._open_bus_health()
        assert health_window is window._open_bus_health()
        window._set_exploring(False)
        window._filter.setText('999');window._apply_filter()
        peer.send(can.Message(arbitration_id=0x321,data=[1]))
        assert wait_for(lambda:service.health_snapshot()['rows'][0]['rx']==1)
        health_window.refresh()
        assert health_window.table.item(0,4).text()=='1'
        assert health_window.table.item(0,10).text()=='Unavailable'
        assert 'SIMULATED' in health_window.session.text()
        assert window._model.rowCount()==0
        health_window.close();assert not health_window.timer.isActive()
    finally:peer.shutdown();service.disconnect_bus();window.close()


def test_query_worker_is_bounded_and_allowed_in_listen_only():
    service=BusService(); wire=LoopBus(); entered=Event();release=Event()
    with patch('signal_explorer.bus.service.open_bus',return_value=wire):
        service.connect_bus(next(b for b in BACKENDS if b.interface=='pcan'),'PCAN_USBBUS1',500000,False,listen_only=True)
    def slow(bus,interface):entered.set();release.wait(2);return dict(state='Error active',tec=0)
    try:
        with patch('signal_explorer.bus.service.query_health',side_effect=slow) as query:
            before=time.monotonic();service._poll_health()
            assert time.monotonic()-before<.1 and entered.wait(1)
            for _ in range(10):service._poll_health()
            assert query.call_count==1 and len(service._health_pending)==1
            release.set();assert wait_for(lambda:service._health_pending['shared'][0].done())
            service._poll_health()
            assert service.health_snapshot()['rows'][0]['native']['tec']==0
            assert not service.can_send and wire.sent==[]
    finally:release.set();service.disconnect_bus()
