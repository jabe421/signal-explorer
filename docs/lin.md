# LIN simulation and native adapter bring-up

## Tonight: no hardware needed

1. Start Signal Explorer, select LIN. The adapter selector above the tabs switches
   to Virtual LIN. The bundled demo LDF loads automatically.
2. Monitor + Simulated peer + Connect LIN gives header/response traffic.
3. Disconnect, choose Master, connect. Send Header 20 requests EngineStatus;
   Start Schedule cycles MasterCommand (10) and EngineStatus (20), 50 ms per slot.
4. Pause Exploring to freeze rows while scheduling and logging continue. Stop
   Schedule separately. Inject next fault to exercise checksum, no-response,
   parity and collision handling. Sleep stops header transactions until Wake.
5. Disconnect, choose Slave, connect. The peer master requests local responses.
   Edit ID 20 data, Apply Responses, and observe TX only after a header arrives.
6. Start Logging / Stop Logging, choose an MF4 filename. Logs are independent of
   trace filters/pause. Cancellation retains the file for Save Log.
7. Master Playback Headers replays recorded headers using current publishers.
   Slave Replay Response Buffers replays changes to selected local response IDs,
   not unsolicited wire frames. Stop/completion restores configured buffers.

## What is implemented

lin/model.py: protocol values, PID, checksums, events and LinTransport contract.
lin/simulation.py: independent nodes on a shared logical bus; one master,
header-driven response buffers, monitor mode, faults and sleep/wake.
lin/service.py: session orchestration, schedules and demo peers.
lin/peak.py: optional native Windows PLIN API bindings, discovery, Master header
transmission, buffered Slave responses, receive polling and cleanup.
lin/vector.py: XL LIN bindings, LIN-piggy discovery, native response table,
initialization permissions and hardware timestamp mapping.
lin/intrepid.py: ValueCAN4-2EL LIN, legacy J1850 message packing, temporary
settings and hardware response buffers. lin/native.py supplies role guards.
bus/ownership.py: shared CAN/LIN device leases within this app process.
lin/database.py: established ldfparser library for unconditional frames, signals
and supported schedule entries.
lin/codec.py: MDF4 LIN_Frame records and custom SignalExplorer_LINEvent records.
ui/lin_panel.py: protocol-specific controls and trace, separate from CAN.

The recorder and timed playback worker are reused, not copied into the LIN UI.
Ethernet can later supply its own transport, packet model and recording codec.
Protocol tabs must not open competing native handles for the same hardware.
CAN/LIN now share a device-ownership guard for Vector and Intrepid. Simultaneous
CAN/LIN on the same physical device awaits a shared native multi-protocol session.

The simulation has no electrical model and does not qualify native API behavior,
real-time slave turnaround, clock synchronization or full LIN 2.x conformance.
Slot delays use GUI-thread timers and include a 1.4x nominal wire-time budget;
software load can still introduce jitter. Diagnostic IDs use classic checksum,
but diagnostic transport/NAD services are not implemented. Unsupported LDF
schedule commands fail explicitly.

## Native adapters: implemented, powered bench validation pending

PEAK, Vector and Intrepid native LIN connections are exposed as experimental
backends. All buffer Slave responses in the adapter, rather than replying from
a GUI callback. Vendor imports are optional so missing SDKs do not disable the
other adapters. Select the adapter and Refresh before choosing its physical
LIN channel. The simulated peer and fault injection remain Virtual-only.

### Intrepid ValueCAN4-2EL

Install the existing optional `[intrepid]` dependencies and the vendor drivers
with 64-bit `icsneo40.dll`. Discovery checks the actual device type and lists
ValueCAN4-2EL LIN 1. ValueCAN4-2 and ValueCAN3 are not LIN-capable. The connected
EL unit was detected as VE0245 and its settings could be read, but it was
USB-only; no powered native bus/transmission tests were run.

The driver checks for other open clients, opens the selected serial, saves a
copy of its current settings, and changes LIN1 baud and MasterResistor for the
selected role using temporary writes (`save_to_eeprom=False`). The resistor
enum is 0 for Master ON and 1 for Slave/Monitor OFF. Other device settings are
preserved. Master writes carry INIT and can include a local response; Slave
writes have INIT clear and update the native responder table. Empty responder
updates are used to clear entries during setup, removal and cleanup. Verify
this removal behavior on the installed firmware in the powered bench test.

Receive data is unpacked from J1850 Header/Data, accepting the documented ACK
checksum layout as well as the vendor example's trailing-checksum layout.
The hardware epoch is 2007-01-01 UTC. Header observations are reconstructed
from transaction reports for MF4 role-aware playback. RX/TX distinguishes
locally transmitted headers from locally published responses.

Disconnect gates LIN communication off, clears responder entries, restores the
saved temporary settings and closes the handle. Every cleanup step is attempted
even after an earlier failure; errors are reported. LIN communication remains
off until a subsequent session explicitly enables it. No device defaults,
firmware or EEPROM are overwritten. **Wake is disabled for Intrepid:** the
legacy binding has no verified dedicated wake-pulse operation. An external
master can wake the bus. Master Sleep sends the standard ID 3C command.

Native implementation references:
[Intrepid LIN API FAQ](https://docs.intrepidcs.com/neovi-api/development-faqs/how-do-i-communicate-on-lin-intrepidcs-api),
[vendor transmit example](https://github.com/intrepidcs/python_ics/blob/master/examples/transmit_lin_example.py),
[vendor settings example](https://github.com/intrepidcs/python_ics/blob/master/examples/lin_settings_example.py).

### Vector VN1640 / XL LIN

Install Vector hardware drivers and the XL Driver Library, as for CAN. The LIN
backend uses the installed vxlapi header's packed event ABI and independent
ctypes function bindings; it does not modify python-can's CAN wrappers.
Discovery filters `XL_BUS_ACTIVE_CAP_LIN`, preserving the global channel mask
and index separately from the physical connector number. The VN1640 must have
LIN piggies installed/configured; a VN1610 with CAN transceivers is excluded.

The port requests LIN initialization access and refuses to reconfigure a
channel when access is denied. It sets the role, baud, known response lengths
and checksum models; disables stale responders; and enables selected response
buffers using xlLinSetSlave/xlLinSwitchSlave. Master Send Header and schedules
use xlLinSendRequest. Sleep and Wake use XL's native LIN functions.

Receive polling maps XL nanosecond timestamps to wall time using xlGetSyncTime
without resetting shared clocks. Header rows are reconstructed from transaction
reports, not independent header notifications. No-response and checksum errors
appear in the trace; generic LIN errors and receive overruns stop capture with
an error. Disconnect disables owned responders, deactivates the channel and
closes the port/driver. Bench-check direction flags, unknown-ID checksum
detection, hardware timing and piggy power/termination before qualification.

### PEAK PLIN (implemented; physical validation pending)

Install the vendor LIN driver, PLIN Manager service, and matching 64-bit
PLIN-API (`PLinApi.dll`). PCAN-Basic is still used for CAN, separately. No new
Python dependency is needed. The loader also accepts an absolute DLL path via
`SIGNAL_EXPLORER_PLIN_DLL`. See the vendor's
[PLIN API package](https://www.peak-system.com/products/software/development-packages/plin-api/)
and [API manual](https://www.peak-system.com/produktcd/Develop/PC%20interfaces/Windows/PLIN-API/PLINAPI_enu.pdf).

Select LIN > PEAK PLIN / PCAN-USB Pro FD > Refresh. Channels are enumerated from
PLIN, not guessed from CAN channel names. Select the required physical LIN
channel, role and baud rate. An absent driver/API or absent hardware produces
an actionable message and does not affect Virtual LIN or CAN.

- Master: Send Header or Start Schedule. For locally published IDs the native
  write includes the header and current response; other IDs request a slave.
- Slave: Apply Responses writes the hardware response table. Unchecking an ID
  changes its native entry to subscriber, removing automatic transmission.
  Replay Response Buffers updates that table at recorded intervals; it does
  not generate unsolicited responses.
- Monitor: slave hardware mode with no publishers, header requests or wake
  pulses. Known IDs use configured lengths/checksums; unknown unconditional
  IDs use the driver's automatic length/checksum recognition.
- Sleep: master sends the standard classic-checksum ID 3C sleep command.
  Wake uses PLIN's native wake pulse. Simulated fault injection is unavailable.
- Disconnect stops the receive worker, clears the owned channel configuration,
  disconnects and removes the PLIN client, attempting every cleanup step even
  when an earlier step fails. Close other PLIN clients on that LIN channel
  before connecting. Avoid opening another application on it during a session.

PLIN receive records use hardware timestamps at the LIN-break falling edge.
Signal Explorer maps the device clock to wall time at connection. Header rows
are reconstructed from completed transaction records at that same timestamp,
not independent native header notifications. RX/TX describes the response
publisher; header direction describes the master. Native TX is shown after a
driver receive report, not merely after a successful queue write. Timeout rows
do not present the driver's byte-count field as a valid checksum. Bus errors
and receive overruns are reported; severe errors stop the session. Changing
checksum models requires disconnecting first.

Logging remains independent of the trace pause/filter. Scheduling and playback
use desktop timers; no deterministic timing guarantee is implied. The PEAK
driver ABI, calls, roles, buffer removal and cleanup have mock coverage. No
physical PEAK unit was available for this implementation: external power,
termination behavior, sleep/wake, native data packing and MF4 interoperability
must be checked on the actual powered bench before engineering qualification.
Use the PEAK hardware's documented LIN connector pinout, external supply and
common ground. A CAN-only adapter cannot be used for LIN.

For ValueCAN4-2EL, use python-ics / the neoVI DLL LIN API, which is separate from
python-can. Configure LIN and the master resistor for the selected role; preserve
and restore temporary settings. Match the unit's actual connector pinout and
provide the external supply required by the vendor.

For VN1640, enumerate installed LIN piggies and use the XL LIN API for channel
configuration, response tables, headers, events and sleep/wake. Do not assume CAN
channel indices or CAN bit-timing settings describe a LIN channel.

Bench sequence: CANalyzer/VN1640 as master, Signal Explorer/ValueCAN4-2EL as slave;
then reverse roles if the CANalyzer configuration/license permits responder
simulation. Match LDF, baud rate, data lengths and checksum model on both ends.
Use one active master per wire, correct master pull-up and common ground as
specified by the hardware documentation. LIN does not use CAN's 120-ohm
termination arrangement. Verify the applicable vendor pinouts before wiring.

Check header/response timing, passive monitoring without unintended responses,
checksum/parity/no-response errors, sleep/wake, disconnect cleanup, and MF4 import
in the external engineering tool. The existing simulation tests are useful
regression tests but do not replace these bench checks.
