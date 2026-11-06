# CAN transmission and connection workers

The CAN GUI opens native connections in a worker and displays **Connecting…**.
Cancel prevents a late result from becoming the active session, then closes it
before another open is allowed. Native driver opens cannot always be interrupted.
Disconnect also waits for outstanding calls and closes resources in a worker;
the UI remains responsive while it displays the cleanup state. Closing the main
window waits for physical CAN cleanup rather than destroying an active worker.
The synchronous `connect_bus`/`disconnect_bus` API remains for tests and non-GUI
callers; application GUI connection controls use the asynchronous API.

Main-window sends and Python send RPCs now submit background jobs. Script Stop
cancels queued sends and ignores results from a previous worker process. Main
once-send disables Send until completion. Repeated ticks coalesce while one send
is pending. MF4 playback already owns a worker and uses the background-safe
synchronous API. Its preflight, direction selection and recorded timing remain.
Once native transmission has begun, Stop cannot unsend it.

## Native handles and bounds

Each native handle has a serial lane, bounded to 512 waiting jobs. A combined
Vector/Intrepid port remains serialized because concurrent sends on that shared
handle are not assumed safe. Independent ChannelMux handles have separate lanes
and send locks, so one blocked port does not hold the global session-state lock
or prevent other handles making progress. Cyclic rows have at most one queued or
running job per row. Missed periods are skipped, not replayed in a catch-up burst.
Cancelling a queued Future removes its job, preserving the queue bound.

The application requests `timeout=0.0` from python-can for native sends. This
avoids optional acknowledgement/drain waits where supported, including Intrepid's
nonzero-timeout receipt wait. It is not a guarantee that every vendor/firmware
call is nonblocking. A stuck shared native handle can still delay every row using
that handle. The UI remains responsive and other independent handles can run;
reconnecting waits for cleanup. No unsafe forced thread termination is used.

## Cyclic retry policy

Only known pre-acceptance rejections are automatically retryable: application
queue saturation, Vector queue-full code 11, Kvaser TX buffer overflow -13,
explicit PCAN transmit-buffer-full text, and an OS EAGAIN/EWOULDBLOCK with no
partial write. Cyclic rows retry at most three times after the original failure,
with 20, 40 and 80 ms backoffs. A successful send clears the consecutive failure
count. Persistent rejection stops the row with an error; other rows continue.
This is a fixed conservative policy, not a new user-editable timing control.

Generic errors, bus-off, disconnection, invalid frames and uncertain native
timeouts stop the affected row without automatic retry. A timeout may occur
after a frame reached the bus; replaying it could duplicate a test command.
Once-send, script send and logfile replay do not automatically retry. They report
failure to the caller. Unrecognized vendor errors are not guessed to be safe.

`TransmitEngine.diagnostics(uid)` reports retry and missed-cycle counts; the
existing state error field shows retry progress or the terminal error. Accepted
sends increment row counts only once. Driver TX reports still arrive separately;
neither queue submission nor send acceptance is intended-ECU delivery proof.

## Validation

Mocked/virtual checks cover slow open cancellation and late-result cleanup,
Qt-thread adoption, script/UI heartbeat during a blocked native send, responsive
disconnect, bounded queues, independent lanes, explicit transient retries,
persistent failure limits, no retry of uncertain timeouts, live edits and queued
Stop cancellation. Native driver stress qualification remains in Action Items.xlsx.
