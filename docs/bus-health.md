# CAN Bus Health

Open **Analysis > CAN Bus Health...**. The independent window can remain open
beside the trace, script editor or sender. No new controls or columns are added
to the main trace. Reopening reuses the same window. Closing it stops its UI
refresh timer; observation continues for the active CAN connection.

The window is CAN/CAN FD only. It shows each enabled channel independently:

- Observed load estimate, driver-reported load when available and frames/second.
- Cumulative received and transmitted data/RTR observations.
- Raw CAN error-frame observations, native Vector FD error events and send-call
  failures as separate counts. These are different events and are not summed
  into a controller error counter.
- Driver-reported controller state, TEC, REC and overruns/overrun flags where
  supported, including source/detail and update age. Stale values are marked.
- Separate session display-queue drops, current/last script-ring drops and
  recording status. Unsupported native counters say **Unavailable**, not zero.

**Reset observed counts** resets only this health model's application counts
and recent-rate buckets. It does not clear hardware counters, trace, scripts,
recordings, device configuration or fault states. Disconnect freezes the last
session display; reconnect starts fresh observations. Display Clear, ID filters
and Pause Exploring do not reset or pause health capture. Pause Exploring freezes
the CAN trace, Signal Graph, and CAN Bus Health display. On resume, Bus Health
shows current totals, and the trace and graph resume with new frames.

## Load estimation and limitations

The observed load estimate uses raw service observations before the display
queue/filter/pause. Bounded 100-ms buckets cover a rolling two-second arrival
window; startup uses elapsed connection/reset time. This adds no per-frame Qt
signals, extra receiver, file logging or DBC decoding. At most 21 buckets per
enabled channel are retained. UI refresh is 250 ms.

Classic unstuffed frame lengths, including intermission, are 47 + 8*N bits for
standard frames and 67 + 8*N for extended frames. RTR uses no payload bits even
when its requested DLC is nonzero. The high estimate adds a worst-case dynamic
stuffing allowance. The displayed range describes the observed frames, not a
guaranteed bound on physical bus utilization.

FD estimation uses an approximate arbitration/data-phase split, CRC17/CRC21 and
fixed/dynamic stuffing allowances. BRS uses the configured data bitrate for the
estimated fast phase; without BRS all bits use the nominal rate. It is not a
bit-exact ISO CAN FD encoder or a certified bus-load measurement. Sample-point
transitions, device timing and arbitration effects limit precision.

Error/retransmission occupancy, unknown/filtered/lost frames and driver batching
are not reconstructed. A host TX acceptance observation is not proof of on-wire
transmission, and its timing can differ from the bus. Native TX report mode
improves the evidence when supported. Estimates are not clamped at 100%: excess
indicates timing/model/observation uncertainty, not a bus physically exceeding
capacity. Virtual sessions prominently label synthetic timing. Their rates are
not a physical measurement.

## Backend diagnostics

- **Vector XL:** existing python-can Classic/FD event hooks observe chip state,
  TEC/REC and FD RX/TX error events. Periodic chip-state requests use the existing
  handle; the normal reader receives the response. No second RX consumer is
  created. Bus-off TEC is shown unavailable rather than presenting a saturated
  byte as a valid current counter. FD native error events shown here do not add
  new raw-MDF diagnostic channels.
- **Kvaser CANlib:** read-only CANlib error-counter/status calls and python-can
  bus statistics provide reported load, TEC/REC and overruns when supported.
  CANlib can estimate counters on controllers that cannot expose them directly;
  the source label preserves that qualification. Driver load units 0-10000 are
  converted to percent, independently of the frame-based estimate.
- **NI-XNET:** poll the existing session's CAN communication state and TEC/REC.
  Bus-off TEC is invalid and displayed unavailable. No automatic bus restart or
  error-counter reset is performed.
- **PEAK PCAN:** GetStatus provides bus-off/passive/warning and overrun flags.
  These flags are not numeric TEC/REC or cumulative overrun counts. No-error
  flags are labeled as such rather than inferring a hardware counter of zero.
- **Intrepid, SLCAN, gs_usb and Virtual:** frame/application observations remain
  available. This build does not expose numeric controller diagnostics for
  those backends; unavailable is explicit.

Queries run in bounded background I/O lanes, even for listen-only sessions.
There is at most one pending query per native handle. The UI never waits for a
native query. Calls share native-handle serialization with TX; a driver call
that blocks can still delay that handle. Updates normally poll once per second
and may miss short state transitions. Read failures appear in channel detail;
stale data is marked after three seconds. Device/driver qualification is pending.

## Future graphing

`bus/health.py` owns the thread-safe metrics and snapshot
data. `ui/bus_health_window.py` only reads snapshots. Future signal/time-series
graphing can be another **Analysis** window using background raw capture and
bounded data, without coupling DBC decoding or plot rendering to the health
model or main trace. Use [Signal Graph](signal-graph.md) for decoded CAN signals.

## References and acceptance tests

- [Kvaser status/counter semantics](https://kvaser.com/canlib-webhelp/page_user_guide_bus_errors.htm)
- [Kvaser status flags](https://kvaser.com/canlib-webhelp/canstat_8h.htm)
- [NI-XNET communication/error types](https://nixnet.readthedocs.io/en/latest/api_reference/types.html)
- [python-can Vector event hooks](https://python-can.readthedocs.io/en/v4.6.1/interfaces/vector.html)

Mock/virtual tests cover framing, FD BRS timing, bounded windows, channel
separation, reset/freeze/reconnect, unsupported counters, Vector hooks, PCAN
status masks, CANlib units, NI counter validity, listen-only background polling
and observation while the trace is paused/filtered. Action Items.xlsx tracks
physical reference-analyzer comparison and native driver acceptance checks.
