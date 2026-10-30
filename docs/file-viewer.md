# File Viewer and log replay

The main **File Viewer** menu replaces the former Scripts menu. New Python Script,
Open Python Script and Open CAN Simulation Example still open the Python editor.
**Open Logfile** opens an MDF4 (`.mf4`) bus log in a separate window using the same
editor layout, line numbers, execution highlighting, Find, and output pane.
The script editor's File menu also offers Open Logfile.

## Open and inspect

Logs can be opened without connecting hardware. Loading runs in the background.
The viewer detects single-protocol CAN / CAN FD, LIN, and Signal Explorer Ethernet
logs and shows one event per numbered line with its original timestamp, bus,
recorded RX/TX direction and data. CAN rows also show hexadecimal ID, format/FD
flags and DLC; LIN rows show ID, event kind and checksum model.

Log contents are read-only: editing and Save/Save As are disabled to preserve the
binary recording. Copy and Find remain available. The lower pane is labeled
**Replay Output**; Python windows retain **Script Output**.

For large files, the display is capped at 100,000 rows or about 32 MiB of text.
Replay Output reports truncation. This cap affects viewing only: replay streams
the entire file. Mixed-protocol MDFs and unsupported bus schemas are rejected.
Supported formats match Signal Explorer's existing MDF4 recorder/replayer;
this does not add ASC, BLF, CSV or PCAP import.

## Replay to an adapter or virtual device

1. Connect the destination in the corresponding CAN / CAN FD, LIN, or Ethernet tab.
   The connected channel is the replay destination; opening a file does not connect
   or configure an adapter.
2. Choose **File Viewer > Open Logfile** and select the recording.
3. Set **Recorded bus** to the source BusChannel to replay. The first available
   channel is selected automatically; the tooltip lists detected channels.
4. Select **RX only**, **TX only**, or **RX and TX**. These select the original
   recorded directions. Selected CAN/Ethernet events are transmitted as TX on the
   connected destination, including events originally recorded as RX.
5. Press **Replay**. The complete selection is validated before any transmission.
   CAN FD requires an FD-enabled connection. Error events that cannot be transmitted
   are skipped; invalid settings or incompatible data fail preflight.
6. Use **Pause** / **Resume** or **Stop**. Pausing excludes paused time from the
   schedule, preserving the remaining recorded gaps on Resume. Stop interrupts long
   waits promptly and leaves the connection open. Replay finishes at end of file;
   Replay starts again from the beginning.

Replay uses **1x recorded timing**, starting with the first selected event. Gaps
between selected events are retained even when other directions or buses are
filtered out. Scheduling is best effort and reports maximum scheduling delay;
desktop Python and adapter APIs do not guarantee hard real-time timing.

Preflight rejects selected gaps exceeding 24 hours as a timestamp discontinuity
before any transmission. The diagnostic identifies the original logfile row and
gap. The scheduler also bounds individual platform waits to prevent timeout
overflow; this does not compress valid gaps. There is no skip-gap control.

Intrepid CAN, LIN and Ethernet hardware clocks are now anchored to PC time at the
first received frame of each connection, retaining subsequent hardware intervals.
This aligns received events with host-timestamped TX events. Absolute alignment
includes first-frame driver latency; it is not GPS/PTP clock synchronization.
Earlier Intrepid logs could combine a device clock near the 2007 epoch with PC
timestamps. The logger's chronological correction could then flatten later RX
timestamps. Such historical timing cannot be reconstructed from the saved file;
make a new recording with the corrected application. The existing log is not
modified by opening or replay preflight.

**Line delay (ms)** controls the refresh interval of the highlighted replay row,
not transmission timing. At zero it follows at UI speed; at 1000 the display
samples the latest sent row approximately once per second. Fast traffic can skip
intermediate visual highlights while every selected event is still transmitted.
**Follow execution** scrolls to the highlighted row; uncheck it to inspect another
part of the file. Rows retain their original positions when replay filters skip
events. Rows beyond the viewing cap have no visible highlight.

Replay Output reports validation, progress, pause/resume, completion, failure or
stopping. The normal monitor and logger see replay traffic through the same send
path as manual transmission. Monitor filters or display pause do not stop replay.

## Shared controls and protocol behavior

Only one playback per protocol can run at once, whether started in a viewer or
the main protocol tab. Starting replay stops that protocol's repeated-send or LIN
schedule controls using the existing panel behavior. The main Stop Playback button
can stop a viewer's replay. Other log viewers disable Replay while that protocol
is busy. Disconnecting stops playback; closing the viewer or application waits for
its loader/replay worker to finish before releasing the window or adapter.

LIN uses its existing role semantics: Master replays header events using current
response configuration; Slave replays timed updates to configured response buffers,
with incoming headers triggering transmission. Monitor-only LIN cannot replay.
Replay does not convert a captured slave response into an unsolicited LIN frame.

Ethernet continues to use Signal Explorer's application-specific MDF frame schema.
Native port and adapter behavior is the same as the main tab's Playback function.

For Python editing and execution, see [scripting.md](scripting.md).
