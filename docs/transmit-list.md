# Multi-message CAN / CAN FD sender

Updated November 6, 2026.

Open **Transmit > Multi-message sender...** or **Multi-message TX...** beside
the existing main-window Send button. The independent **Send Messages** window
uses the same CAN connection, channel routing, trace and logger. The main
single-message sender remains available. No additional packages are required.

## Message list

- **New Raw** adds an eight-byte standard CAN frame. Edit its name, hexadecimal
  ID, channel, Ext/FD/BRS flags, DLC (payload byte count) and data bytes.
- **New from DBC** copies a definition from the database loaded in the main
  window. It initializes values from signal defaults/ranges, copies the message
  cycle time if present, and retains scaling, byte order and value labels.
- **Use** includes a row in batch operations. Clearing it stops that row and
  cancels its queued sends. **now** sends that row once.
  Check **Cyclic**, set **Cycle (ms)**, then click that row's **Start**. It becomes
  **Stop** while the row runs. Setting a cyclic checkbox does not start traffic.
  Clearing Cyclic stops that row's periodic sending.
- **Send All Once** sends each enabled row once. **Start Cyclic** starts enabled
  cyclic rows, with independent cycle times. Batch operations validate every
  selected row before any transmission is queued.
- **Stop All** cancels scheduled and queued sends. A driver call already in
  progress may finish; it cannot be recalled from the adapter.
- **Clone** duplicates selected rows, retaining configuration and DBC definitions
  with fresh counts and no running state. **Delete** removes selected rows.
- **Open TX List** and **Save TX List** provide direct access to the same file
  operations available under the File menu.
- **Sent** counts locally accepted sends. **Status** shows readiness, cyclic
  state or errors. A failing row stops without stopping other running rows.

**Default TX** resolves to the main window's selected TX channel when a send or
cycle starts. Live channel edits retarget subsequent sends, while an unchanged
Default TX selection retains its pinned destination. Explicit channels
must be among the enabled CAN channels. Transmissions are never broadcast to
every adapter channel.

CAN FD supports payload lengths 0-8, 12, 16, 20, 24, 32, 48 and 64. Connect with
CAN FD enabled before sending FD frames; BRS uses the connection's data bitrate.
The grid shows bytes 0-7. Select a row to edit its **Complete payload** below,
including bytes 8-63. Change DLC first to resize a raw frame, then enter exactly
that many bytes. Changing ID, format or DLC detaches a row's DBC definition and
waveforms; raw payload editing of an unchanged layout keeps its definition.

## Signal panel and generation

Select a DBC row to see active signals, start bits, widths, numeric raw and
physical values, units, increment step and waveform settings. Edit Raw Value or
Physical Value; value labels appear in tooltips. **Dec/Inc** uses the physical
Step value. Encoding honors DBC scaling, signedness, byte order and range checks,
while preserving payload bits that have no active DBC signal definition.

**Waveform** defines physical minimum/maximum and one of:

- **Off:** use the configured payload value.
- **Ramp:** increase by Step per transmitted cyclic frame; wrap at maximum.
- **Pulse:** alternate minimum/maximum with a 50% duty cycle.
- **Sine:** vary smoothly between minimum and maximum over the chosen period.

Waveforms restart when a row starts. Cyclic TX drives waveform updates; Send Once
uses the initial waveform value. Live bytes and decoded values show the last
accepted cyclic frame. Messages and signal values remain editable while sending.
Waveforms cannot
drive multiplexers or inactive multiplexed signals. Signal edits may select a
valid mux branch; new branch fields use their defaults where necessary.

DBC definitions are private snapshots taken when rows are added. Editing or
unloading the main DBC does not change an already configured transmit row.
Re-add a row from the updated DBC when its layout must change.

## Live editing

Edit message ID, channel, frame format, DLC, bytes, cycle time, raw/physical signal
values or waveform settings while a cyclic row runs. Each edit is validated
before the new configuration is applied atomically. Subsequent frames use that
configuration; a frame already in progress may finish with its previous values.
Counts continue across edits. A cycle-time change reschedules the next send using
the new interval. Invalid edits leave the previous configuration running.

Live data refresh pauses for the field you are typing in. Enter commits a table
cell; leaving the full-payload field commits its text. Manually editing a signal
turns off that signal's waveform, while other signal waveforms continue. Editing
raw data bytes or the complete payload turns off the row's waveforms so they do
not overwrite your manually entered data. Changing the message layout still
detaches its DBC snapshot as described above.

Turning Use or Cyclic back on does not restart sending automatically: press Start
again. Loading a transmit list continues to stop existing schedules and never
starts the loaded rows. The existing JSON transmit-list format is unchanged.

## Save, stop and connection lifecycle

Use **Save TX List** / **Open TX List**, or **File > Save transmit list...** /
**Open transmit list...**, for JSON transmit
lists. Files include payloads, channel selections, periods, cyclic configuration,
waveforms and embedded DBC message definitions. Loading validates the complete
file first and does not start any row. Counts and running state are not restored.

Opening a list stops this window's transmissions. Closing the window also stops
them, while retaining the list in memory for reopening within the same app
session. Closing the main application prompts to save unsaved transmit lists.
Disconnecting CAN or starting logfile playback stops all these schedules;
reconnection never resumes them automatically. Monitor pause/display filters do
not stop sends or exclude TX from logging. Python scripts and the main sender
remain independent.

The scheduler runs off the GUI thread. PC timing is best effort: bus arbitration,
adapter/API latency and other rows sharing a native handle can delay transmission.
Missed cycles are skipped rather than sent as a catch-up burst. Pulse/sine
waveforms are sampled at the row's transmit rate. There are no electrical error
frame generators, keyboard-trigger bindings or hardware-timed scheduling in
this first transmit-list implementation. RAW and DBC data frames are supported;
RTR/error-frame generation is not exposed here.

The scheduler submits bounded background jobs to one serial lane per native
handle. Independent handles progress separately; a cyclic row has at most one
pending job. Explicit queue-full rejections receive up to three retries with
20/40/80-ms backoff. Unknown errors/timeouts stop the row without retry to avoid
duplicates. See [transmit-robustness.md](transmit-robustness.md) for limits.

## File format and portability

TX-list JSON carries the format marker `SignalExplorer CAN transmit list` and
schema version 1. It is specific to Signal Explorer, not a universal automotive
TX-list standard. Maximum 256 rows and a 16 MiB load bound apply. Complete file
validation precedes replacement; saves use an atomic temporary-file replacement.
Existing version-1 lists remain compatible with live editing.

DBC describes layouts/scaling and optional cycle metadata; MF4 records traffic
for playback. Neither substitutes for the saved live TX configuration. Embedded
DBC snapshots allow reopening a list without the original database file. Check
enabled channels and FD connection settings before explicitly starting rows on
another PC. Local adapter preferences are not copied into the JSON list.
