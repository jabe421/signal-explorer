# CAN monitor batching and decode caching

Updated November 5, 2026. This change applies to CAN/CAN FD; LIN and Ethernet
keep their existing display paths.

## Display handoff

The receive worker captures each frame immediately, normalizes its timestamp and
channel, and notifies raw traffic subscribers. The recorder and script transport
use direct, thread-safe subscriptions before display queuing. Capturing takes a
frame/payload snapshot: python-can's shallow copy alone shares its bytearray.

A lock-protected queue retains up to 20,000 display observations, including local
TX. A GUI timer drains at most 2,000 every 40 ms and emits a frame batch. The CAN
window consumes batches, with one model update and at most one scroll per batch.
RX no longer queues a Qt event for each frame from the reader to the UI. The
legacy `BusService.message` display signal remains available to existing callers;
its per-frame RX emissions run only when it has observers. MainWindow subscribes
to `messages` instead. Local TX's legacy notification remains immediate.

Overflow drops the oldest queued display observation and increments the visible
**Display drops** counter. This counter is separate from logger/script overflow
and hardware/driver losses. Trace counters reflect frames admitted to the model;
they do not claim to include observations discarded by the display queue.
History retains the latest 10,000 displayed observations. Latest-value mode
retains one record for every observed channel/ID/standard-or-extended key.

Pause/resume and Clear advance a display generation so pending older observations
cannot appear after resume or refill a cleared trace. They still reach raw
logging/script subscribers. A disconnect stops the reader and flushes the bounded
remaining display queue before closing the session. Successful close stops the
counter timer. Counters refresh at most every 100 ms during traffic; explicit
control/filter changes refresh immediately.

## Model and decoding

`TraceModel.append_messages` coalesces latest-value updates by key while counting
every frame, preserves per-observation history counts and emits row ranges once
per batch. Both caches remain available when toggling display modes. The model
uses lists for constant-time row access; history trims a prefix once per batch.
Owned service snapshots are reused, while standalone model callers receive a
defensive frame/payload copy.

Loading a DBC builds dictionaries for `(arbitration_id, extended)` definitions
and signal units. Raw columns never decode. The two DBC columns and expanded
signal children share one cached decode per retained record and database
revision. Latest-value updates and database changes invalidate the appropriate
cache. Unknown, remote/error and invalid payloads retain their prior display
semantics; decode failures are cached as well.

Each signal child retains its owning record. A record position and history
offset allow constant-time parent lookup without scanning rows or reindexing
every retained history row after eviction. Database/mode resets, child-count
changes and row removals use Qt model notifications to keep expanded/persistent
indexes valid.

## Verification and repeatable benchmark

`tests/test_can_batching.py` covers batch ranges, oversize history batches,
multi-channel counts, persistent children across eviction, lazy/shared DBC
decodes, reload/error invalidation, standard/extended lookup, payload ownership,
queue overflow, pause/clear boundaries, counter throttling and reader delivery.
The existing integration suite covers logging, script execution, FD/BRS,
transmission and channel mapping.

Run the model benchmark from the repository root:

```powershell
.\.venv\Scripts\python.exe tools/benchmark_can_trace.py --baseline <commit-before-this-change>
```

The recorded comparison used `--baseline ca02cdb`. It processed 20,000 synthetic frames,
200 retained latest-value rows, two channels and a 1,000-message DBC. Ingestion
took 4.3891 s before and 0.1352 s after. Three simulated passes over cells and
signal children took 1.2824 s before and 0.0976 s after. Top-level insert
notifications fell from 200 to 1; data-change notifications from 39,800 to 19.
One sample definition decoded 51 times before and once after. These are model
timings on this development PC, not bus-throughput or rendered-GUI guarantees.
Physical sustained-load testing remains necessary; queue limits deliberately
prevent a stalled UI from consuming unbounded display memory.

An end-to-end virtual-bus test also sends 4,000 frames through the reader and
the GUI batch handoff while logging: all 4,000 reach MF4 in order, history retains
only its configured last 128, and latest-value mode keeps four IDs with counts
of 1,000 each. No physical frames are transmitted by these tests.
