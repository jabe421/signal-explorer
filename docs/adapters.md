# CAN adapter strategies

The CAN backend registry in `bus/strategies.py` is the shared source of vendor
policy. It preserves the existing Virtual, CANable SLCAN/candleLight, Vector XL,
Intrepid neoVI, Kvaser CANlib, PEAK PCAN and NI-XNET options. This refactor adds
no adapter models or hardware capabilities.

## Responsibilities

- `bus/backends.py`: GUI labels, default channels and declarative extra options.
- `bus/strategies.py`: FD/TX-report/listen-only/advanced-timing capabilities,
  controller clocks, connection options, native channel conversion, shared-port
  versus independent-handle routing, ownership leases, termination, timestamp
  source, recorded bus numbering, metadata and native health hooks.
- `bus/service.py`: connection lifecycle, background driver operations, shared
  capture/clock handling, TX dispatch, display batching and disconnect cleanup.
- `bus/multichannel.py`: generic shared-port or independent-handle opening,
  channel routing and rollback. Strategy policy decides which form is used.
- `bus/canfd.py` and `bus/timing.py`: common validation/calculation using the
  strategy's FD options and controller clocks. GUI capability sets derive from
  the registry instead of separately maintained vendor lists.
- `bus/discovery.py`, `bus/listen_only.py`, `bus/health_native.py` and vendor
  modules: native SDK enumeration, verified silent setup and diagnostic calls.
  Strategies select hooks; the SDK calls and their error checks stay explicit.

Strategies are stateless and do not retain live handles. Optional vendor SDKs
are imported only when their driver-specific operation is needed. An absent
optional SDK must not prevent launching with the virtual backend. Register
strategies during module initialization; runtime plugin installation/hot-loading
is not part of this release.

Backend capability flags are ceilings, not hardware detection. A discovered
channel with `supports_fd=False` still rejects an FD connection, even if its
vendor backend supports FD. NI termination availability and shared-port device
ownership checks also remain enforced. Unsupported listen-only requests never
fall back to an active connection. TX reports retain their existing semantics
and do not prove intended-ECU delivery.

## Adding an adapter

1. Add a `BackendStrategy` subclass and register it in `STRATEGIES` under the
   python-can interface name. Override only the connection/channel/FD/timing,
   clock, metadata or native hooks that differ. Keep per-connection state in
   the service/transport. Do not infer capability from a marketing model name.
2. Add a `BackendSpec` entry to `BACKENDS` for its GUI label and defaults.
3. Extend enumeration in the discovery/native adapter layer where required.
   Provide channel identity and actual per-channel capabilities. Implement
   native silent/health hooks only when the driver supports and verifies them.
4. Add optional Python bindings and packaging metadata if needed. Vendor driver
   DLLs/installers remain separately installed, not redistributed in the app.
5. Add mocked tests for capability gating, exact driver arguments, channel types,
   routing, rollback, timestamp provenance, silent-mode rejection and cleanup.
   Test real hardware before marking its manual tracker items Verified.

Adding a catalog entry alone does not integrate an adapter. Unknown strategy
names fail explicitly. `tests/test_backend_strategies.py` includes an example
custom strategy exercised through the shared connection path, plus checks that
loading the registry does not import proprietary vendor SDKs.

## Refactor verification

November 6, 2026 physical checks on VN1630A serial 604069 used CAN1 connected
to CAN2. Classic CAN at 500 kbit/s and CAN FD at 500 kbit/s / 2 Mbit/s passed
with both local TX estimates and driver TX reports. Selective channel routing,
RX/TX observations, trace views/filtering and MF4 raw timestamps were checked.
The other backends retain mocked regression coverage; this does not qualify
their installed driver versions, physical silent operation or timing accuracy.
