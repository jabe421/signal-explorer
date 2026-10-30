# Scripting guide examples

Open these files with **File Viewer > Open Python Script...** in Signal Explorer.
The application injects `se`; running them directly with `python file.py` does not.
Read `Scripting User's Guide.pdf` in the project root for the full
listings, Python introduction, firmware contract and limits.

With Virtual CAN connected and Inject demo traffic enabled:

- `01_engine_smoke.py`: receive/decode EngineData and check ranges.
- `02_heartbeat_counter.py`: check counter continuity and demo period.
- `03_echo_vectors.py`: boundary and walking-bit eight-byte echo vectors.
- `04_can_fd_echo.py`: FD telemetry and echo; enable CAN FD before connecting.
- `09_receive_timeout.py`: demonstrate a receive timeout without transmitting.

The following are templates for a **fictional** firmware interface documented in
section 6 of the guide. They are not implemented by the built-in demo:

- `05_gpio_adc_template.py`: output acknowledgement and independent ADC input.
- `06_reset_recovery_template.py`: retained boot count before/after reset.
- `07_uds_read_did_sf_template.py`: a limited Classic CAN, normal-addressed,
  single-frame UDS DID read with bounded response-pending handling.
- `08_negative_command_template.py`: documented unsupported-command rejection.

Their `FIRMWARE_CONTRACT_CONFIRMED` flag defaults to `False` and fails before TX.
Review and adapt the firmware/fixture contract in a saved copy before enabling it.
The UDS example is not a full ISO-TP client, VIN reader or flashing tool. Stop does
not restore MCU outputs; define fixture and firmware cleanup separately.

`tests/test_scripting_examples.py` verifies these sources through the real worker
using private virtual traffic and a mock firmware peer. It enables the flag only
in the in-memory test source; shipped templates retain the guard.
