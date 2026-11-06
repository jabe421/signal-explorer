# Python scripting

For a beginner tutorial and nine commented CAN/MCU examples, see
`Scripting User's Guide.pdf` in the project root and
`examples/scripts/mcu/`. The four MCU templates require matching firmware and
default to a no-transmit configuration guard; the other five use Virtual CAN.

Choose **File Viewer > Open Python Script...** in the main window. A separate editor
opens while Signal Explorer stays available for monitoring and logging. **New
Python Script** starts a blank template; **Open CAN Simulation Example** opens
the packaged example. This release exposes CAN / CAN FD only, regardless of the
currently selected protocol tab. Ethernet and LIN scripting are not enabled.

## First run without hardware

1. In **CAN / CAN FD**, select **Virtual (no hardware)**, leave **Inject demo
   traffic** checked, and **Connect**. Optionally enable CAN FD before connecting.
2. Choose **File Viewer > Open CAN Simulation Example**. The source copy is also at
   `examples/scripts/can_demo_test.py`.
3. Press **Run**. The script sends standard ID **321 hex** with eight bytes,
   initially `00 01 02 03 04 05 06 07`. The virtual demo peer replies on **221 hex**
   with exactly the same bytes. After verifying that response, the script increments
   each byte by one (wrapping `FF` to `00`), waits 100 ms and repeats until **Stop**.
   A missing response after two seconds or a data mismatch fails the run. It refuses
   to send on physical hardware. These Classic CAN requests also work with CAN FD
   enabled; the existing periodic engine and FD demo traffic continues alongside.
4. Use **Line delay (ms)**, e.g. 100, before Run to follow individual lines. Zero
   runs normally; high-speed line notifications are sampled to keep the UI usable.
5. **Pause** becomes **Resume**; the script and its API timeouts/sleeps pause.
   Monitoring, demo traffic, other manual send controls and logging keep running.
6. **Stop** cancels outstanding script operations. The CAN connection stays open.

The echo response comes from a separate virtual bus endpoint and uses the normal
RX path, so both the request TX and response RX appear in monitoring and logging.
This checks a simulated application exchange, not a physical ECU or UDS exchange.
The peer responds only with Virtual CAN and Inject demo traffic enabled, and only
to standard eight-byte data frames on 321 hex. Manually sent requests work too;
FD requests receive FD responses with the same bitrate-switch flag.
Existing standalone `can.Bus` scripts must be adapted to the `se` interface; do
not open another adapter or receive directly from its underlying handle.

## Editor controls

Open/New, Save/Save As, undo/redo, cut/copy/paste, select all and case-sensitive
Find/Replace are available in the menus. Standard Ctrl+O, Ctrl+S, Ctrl+Shift+S,
Ctrl+Z, Ctrl+Y and Ctrl+F shortcuts apply. Tab/Shift+Tab indent/outdent four spaces;
Enter retains indentation and adds a level after a colon. Source encoding is
detected on open and retained on save. Saves use an atomic replacement.

Python keywords, built-ins, strings, comments and numbers use different colors.
Line numbers appear in the gutter. The executing source line has a yellow
background and `>` marker; paused is orange, and an error location remains red.
**Follow execution** scrolls to that line; uncheck it to inspect other source.
Traceback text includes error locations, including helpers. Line highlighting
follows the editor's main file; it does not open imported helper files.

Run executes a snapshot of the editor contents, including unsaved edits. The
editor locks during execution so its line numbers match that snapshot. Stop or
finish before modifying it; Save is still available. Open/New/Save As are locked
for that running editor. The console retains at most 5,000 text blocks. Output
contains print/log messages, PASS/FAIL checks and final status. A completed script
with no `se.check` calls says COMPLETED rather than claiming checked tests passed.

Several editor windows can be open, but only one script can run at a time.
Closing a running editor stops its script before releasing the window. Closing
Signal Explorer waits for the script to stop before disconnecting buses; normal
unsaved-change prompts let you save, discard or cancel closing.

## Script API

Signal Explorer injects `se` into the script's global namespace. No special import
or `main()` function is required. Ordinary functions, loops and local Python
imports work. Local imports resolve from the saved script's folder. No interactive
`input()` prompt is provided; it raises EOFError instead of consuming control IPC.

```python
# Inspect the connection used by this run.
se.log(se.session)
se.check(se.is_simulated, "Example requires Virtual CAN")

# IDs are integers; 0x200 is hexadecimal. Timeouts are seconds.
frame = se.can.recv(timeout=2.0, arbitration_id=0x200, extended=False)
se.check(frame is not None, "EngineData received")
se.check(len(frame.data) == 8, "Correct byte count")
rpm = int.from_bytes(frame.data[:2], "little") * 0.25
se.log(f"RPM: {rpm}")

# Sends once; uses the connected adapter and its existing FD validation.
se.can.send(0x321, [0, 1, 2, 3, 4, 5, 6, 7])
echo = se.can.recv(timeout=2.0, arbitration_id=0x221, extended=False)
se.check(echo is not None and echo.data == bytes(range(8)), "Demo peer echo")
# For an FD-enabled connection:
# se.can.send(0x322, bytes(12), fd=True, brs=True)

# Script-aware waits are milliseconds and exclude paused time.
se.sleep(100)
```

- `se.protocol`: currently `"CAN"`.
- `se.session`: connection metadata snapshot, including adapter, native channel,
  `fd`, bitrates, interface and simulation flag. The `channels` list contains
  `native_channel`, `label` and recorded `bus_channel` for each enabled port.
- `se.is_simulated`: true only for the selected Virtual CAN session.
- `se.log(value)`: append a message; ordinary `print(...)` is captured too.
- `se.check(condition, description)`: record PASS or FAIL. Failure raises
  AssertionError and normally ends the run. Catch it if you intentionally want
  to continue collecting checks; the recorded failure still makes the final
  verdict FAILED. Unhandled standard Python exceptions also fail the run.
- `se.sleep(milliseconds)`: interruptible wait, with elapsed pause time excluded.
- `se.can.send(arbitration_id, data=b"", extended=False, fd=False, brs=False, channel=None)`:
  validates and sends once through the service; returns the local TX acceptance
  timestamp as Unix seconds (float), and raises on failure. It previously returned
  True; ordinary callers that ignore the result continue to work.
- `se.can.recv(timeout=1.0, arbitration_id=None, extended=None, direction="rx", channel=None)`:
  return a matching `CanFrame`, or None at timeout. `timeout=None` waits until a
  match or Stop. Direction is `"rx"`, `"tx"` or `"all"`; received frames are the
  default, so a local TX echo is not mistaken for an ECU response. Error/remote
  frames are excluded from this API. Only the first matching frame is removed;
  unmatched frames remain available to subsequent receives until flushed or
  evicted. Thus B arriving while waiting for A can be received afterward.
- `se.can.stats()`: returns `capacity`, `buffered`, `received`, `consumed`,
  `dropped`, `filtered` and `flushed` counters. Counters reset at each Run.
- `se.can.flush()`: discards the currently buffered history and returns its frame
  count. It preserves the cumulative dropped counter. New arrivals can follow
  immediately; flush is not a transaction/correlation barrier.
- `se.can.set_filters(filters=None)`: apply a list of ORed capture filters to this
  script only. Each requires integer `can_id` and `can_mask`, with optional
  `extended` (True/False/None), `direction` (rx/tx/all, default all) and `channel`
  (enabled label/native ID). None or [] restores all eligible CAN data frames.
  Changes prune queued nonmatches, count them as `flushed`, and return that count.
  Invalid filter changes leave the previous filters/history intact. At most 256
  filters are accepted; monitoring, hardware receive filters and logging are
  unaffected. Error/remote events are excluded before buffering.

`CanFrame` provides `arbitration_id`, immutable `data` bytes, `timestamp`, `channel`,
`direction`, `is_extended_id`, `is_fd`, `bitrate_switch`, `is_error_frame` and
`is_remote_frame`. `CanFrame.channel` is the native identifier as a string.
Frames also expose `timestamp_source`, `raw_timestamp` (the python-can value
before application normalization) and `tx_confirmation` for timing evidence.
The optional send/recv `channel` accepts an enabled label (e.g. `"CAN1"`) or
native ID. Omitted send channel uses the current main TX destination; omitted
receive channel accepts any enabled port. Scripts select among already enabled
ports, but do not open adapters or change bus timing. Disabled TX is rejected.
See [can-channels.md](can-channels.md) for a CAN1-to-CAN2 loopback example.

The [multi-message sender](transmit-list.md) is independent of Python Pause/Stop.
Stop its cyclic rows before tests that require a quiet bus. Its rows support live
editing; the Python source editor still locks during execution. No scripting API
for configuring the TX window is provided. MF4 logging captures all enabled CAN
channels before display filters/pause; replay selects one recorded bus and one
TX destination. See [recording.md](recording.md) for channel maps.

## Pause, Stop and execution boundaries

Each run starts a fresh Python process with its own namespace. A control thread
handles Pause/Resume/Stop; source-line tracing pauses Python execution. Use
`se.sleep` and `se.can.recv` for waits. A blocking native call such as a long
`time.sleep`, third-party driver operation or extension can delay Pause until it
returns to a trace/checkpoint; the UI displays PAUSING until acknowledged.

Stop immediately rejects further bus RPC operations from the script, and requests
cooperative termination. If the process has not exited after one second, it is
killed. Forced termination cannot guarantee execution of the script's `finally`
blocks. Already accepted frames cannot be unsent. The parent owns bus handles,
so stopping/killing a script does not disconnect the device. A CAN disconnect
stops the run; reconnection requires a fresh Run, not an automatic resume.

The process is **not a security sandbox**. Run trusted local scripts only: Python
still has the user's filesystem/network privileges. Directly importing hardware
APIs, spawning child processes/threads, changing the tracing hook, or opening
separate adapter handles bypasses the intended API and is outside supported
runner-controlled behavior. The runner controls its worker process, not arbitrary
programs spawned by user code. Sub-millisecond/real-time scheduling is not claimed.

RX/TX is copied before monitor filters/pause into an independent 2,000-event script
ring buffer. Sleep/pause and gaps between receives retain history up to that
limit. Overflow evicts the oldest frame, increments `dropped`, and reports a
warning plus a final loss total; it does not automatically stop the script.
`received` counts accepted entries; `filtered` counts rejected captures;
`flushed` counts intentional clears/pruning, while `consumed` counts successful
receives. Therefore received = buffered + consumed + dropped + flushed.
Use capture filters to exclude unrelated traffic and explicitly check loss before
accepting a test result. Neither buffering policy nor script filters change
independent monitoring/recording. A response can already be queued when recv
starts; use transaction identifiers to reject stale responses.

```python
# Virtual CAN with demo traffic enabled. Keep only relevant RX data frames.
se.can.set_filters([
    {"can_id": 0x221, "can_mask": 0x7FF, "extended": False, "direction": "rx"},
    {"can_id": 0x200, "can_mask": 0x7FF, "extended": False, "direction": "rx"},
])
se.can.flush()
before = se.can.stats()["dropped"]
tx_time = se.can.send(0x321, bytes(range(8)))
reply = se.can.recv(2.0, arbitration_id=0x221, extended=False)
se.check(reply is not None and reply.data == bytes(range(8)), "Demo echo")
se.log(f"Observed RX minus local TX: {reply.timestamp - tx_time:.6f} s")
se.check(se.can.stats()["dropped"] == before, "No buffer loss during test")
```

Measure periods using frame timestamps, rather than `recv()` return times. The
send result is the host acceptance timestamp. With driver TX reports disabled,
it is also the local TX observation timestamp in the monitor/logger. With
**Use driver TX reports** enabled, no synthetic TX is published: receive a
driver TX event separately using `recv(direction="tx", ...)` for its timestamp.
Allow TX in script filters and correlate channel/ID/payload/transaction; other
local clients may contribute reports. A report is not intended-ECU receipt. Mixed
hardware/host clocks and driver latency limit TX-to-RX measurements; check frame
correlation, clock alignment and loss before asserting timing. Pause and source
line delay continue to affect script execution.

See [timestamps.md](timestamps.md) for backend capabilities, clock policies and
recording diagnostics. For a compatible physical adapter, with driver reports
enabled before connecting:

```python
# Replace IDs and payload with the ECU's documented test contract.
# Retained RX history lets us wait for the TX report before the response.
se.can.set_filters(None)  # Include TX reports as well as RX.
se.can.flush()
payload = bytes(range(8))
accepted_at = se.can.send(0x321, payload, channel="CAN1")
tx = se.can.recv(1.0, arbitration_id=0x321, extended=False,
                 direction="tx", channel="CAN1")
se.check(tx is not None, "Driver TX event observed")
se.check(tx.data == payload, "TX payload matches")
reply = se.can.recv(2.0, arbitration_id=0x221, channel="CAN1")
se.check(reply is not None, "ECU response observed")
se.log(f"Driver RX minus TX: {reply.timestamp - tx.timestamp:.6f} s")
se.log(f"TX evidence: {tx.tx_confirmation}; host acceptance: {accepted_at}")
```

This example is not a unique per-send acknowledgement tracker. Validate the ECU
response's payload/transaction and buffer-loss counters for a real timing test.

The worker now uses condition notifications for RPC/control waits. A pending
receive uses a compiled matcher and a coalesced notification for matching new
frames; unrelated traffic does not enqueue per-frame GUI wakeups. The 20-ms host
timer remains for deadlines and connection checks. Previously the worker's
30-ms queue wait was already interruptible on response arrival, not a fixed
30-ms delay. Receive deadlines use host monotonic capture arrival: frames that
arrive after a deadline remain available to the next receive.
The full UDS/ISO-TP client layer, CAPL conversion and test-result file export are
not implemented in this release.

## Reuse for future Ethernet scripting

`scripting/controller.py` owns process lifecycle, bounded mailboxes, control/RPC,
timeouts and state. `scripting/runtime.py` owns script context, tracing, pausable
waits and output. `scripting/transport.py` defines connected-state, event encoding,
matching and send operations; `CanScriptTransport` is the only implementation.
The editor depends on that transport boundary and the controller, not a native
driver. Future Ethernet support can add an Ethernet transport/frame/client without
replacing the editor, worker control or shared monitor/logger architecture.
