# CAN timestamps and TX evidence

## Choosing TX observations

Before connecting, optionally select **Use driver TX reports** for Vector XL,
Intrepid neoVI, Kvaser CANlib, PEAK PCAN or NI-XNET. It defaults off and locks
while connected. CAN FD remains supported where the selected hardware supports
it. With this option on, receive-own-message notifications are requested from
the driver and the application does not manufacture a second TX observation.
Missing reports remain missing; there is no synthetic confirmation fallback.
An incompatible driver can reject connection; disable the option to reconnect
with local estimates. Hardware/driver qualification is still required.

With the option off, successful `send()` produces a single local TX estimate
after the backend call returns. This means the backend accepted/completed that
call, not proof of transmission on the wire or receipt by a particular ECU.
Virtual CAN, CANable SLCAN and gs_usb retain this mode. Backend-internal waiting
can make even an acceptance timestamp later than the corresponding bus event.

Driver reports have backend-specific meaning:

- Vector: python-can exposes Classic TX_COMPLETED and FD TX_OK events as TX.
- Intrepid: python-can exposes transmit reports without GLOBAL_ERR. Its send
  path also has its own acknowledgement wait; an application-level response is
  still necessary to prove an ECU processed a request.
- Kvaser: LOCAL_TXACK requires separate read/write handles, enabled here.
  Reports may include transmissions by other local handles on that channel;
  this is not an application-specific per-send receipt.
- PEAK: PCAN_MESSAGE_ECHO requires compatible PCAN-Basic and device drivers.
  PEAK documents PCAN-Basic 4.6+ and PCAN driver 4.3+ for this capability.
- NI-XNET: input-stream transmit echo is enabled and echoed frames are TX.

No report proves that the intended ECU received or processed a frame. Correlate
channel, ID, format, payload and a transaction field when available. Main-window
Sent counts accepted sends; TX trace/log entries in driver mode count observed
reports, so these need not match. Other local clients can contribute reports.

## Clock policy

Each CAN connection captures host Unix time once and advances it using a
monotonic clock. Later PC wall-clock changes cannot step local TX estimates.
Virtual/SLCAN RX uses this same clock at receipt, so its intervals include
application/driver latency and are estimates.

Intrepid uses one device-to-host offset for both RX and TX reports, anchored at
the first received event to the connection's monotonic host epoch. Native
intervals are preserved. A device clock backstep exceeding one second reanchors
the clock. The first-event anchor includes driver latency; it is not PTP/GPS
synchronization. Other physical CAN backends retain python-can's driver epoch
timestamps rather than estimating another offset from first-frame arrival.
Invalid non-Intrepid driver stamps fall back to an explicitly labeled receipt
estimate; invalid Intrepid device stamps stop reception as before.

Host and device estimates are not fully synchronized clocks. Driver reports on
one device improve TX/RX interval comparisons, but mixed adapters, driver clock
resets and wall-clock adjustments inside vendor libraries still need separate
qualification. No cross-device synchronization is implemented. LIN/Ethernet
driver policies are unchanged by this CAN update.

Hover CAN Timestamp or Direction cells for source, native timestamp and TX
evidence. Scripts receive `timestamp_source`, `raw_timestamp` (the timestamp
returned by python-can before application normalization) and `tx_confirmation`.
`se.can.send()` always returns host acceptance time. In driver mode obtain an
actual TX event through `se.can.recv(direction="tx", ...)`; do not treat the
send return value as a driver report. The script's filters must admit TX.

## Recording diagnostics

The shared recorder holds up to 50 ms of timestamp skew (bounded to 8192 events
or the configured queue capacity), writing small out-of-order arrivals in
timestamp order without changing their timestamps. Idle/stop flushes pending
events. Data arriving after that bounded window or already-written data can
still require chronological clamping. Invalid timestamps also require repair.
This protects replay order; it does not synchronize the source clocks.

Embedded `SignalExplorer-session.json` retains `timestamp_corrections` and adds
`timestamp_reordered_frames`, `timestamp_correction_reasons`, the reorder window,
and up to 100 diagnostic correction samples with original/corrected/native
timestamps, channel and direction. A truncation flag marks additional samples.
CAN logs include `timestamp_source_counts`, `tx_evidence_counts` and the session's
TX/clock policy. MDF frame timestamps are normalized; complete per-frame native
timestamps/provenance are not stored as extra MDF channels. Existing logs are
not repaired or rewritten.

## Verification and primary references

`tests/test_tx_reports.py` uses mocked versions of all five backends, including
FD flags, no duplicate echo, paired timestamps, wall-clock jumps, provenance,
UI capability gating and MDF reordering/diagnostics. This does not qualify real
driver versions or prove physical wire timing.

- [python-can Vector implementation](https://github.com/hardbyte/python-can/blob/main/can/interfaces/vector/canlib.py)
- [python-can Intrepid implementation](https://github.com/hardbyte/python-can/blob/main/can/interfaces/ics_neovi/neovi_bus.py)
- [Intrepid transmit reports](https://docs.intrepidcs.com/neovi-api/win32-api-overview-intrepidcs-api/message-functions-overview-intrepidcs-api/txmessages-method-intrepidcs-api)
- [Kvaser CANlib definitions](https://kvaser.com/canlib-webhelp/canlib_8h.htm)
- [PEAK AllowEchoFrames requirements](https://www.peak-system.com/documentation/API/PCAN-Basic.Net/html/4a6f0c95-988e-4873-8966-5e482b889cd1.htm)
- [python-can NI-XNET implementation](https://github.com/hardbyte/python-can/blob/main/can/interfaces/nixnet.py)

Implementation checked against installed python-can 4.6.1. Recheck semantics
when upgrading drivers or python-can.
