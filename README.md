# Signal Explorer

Desktop tool for connecting to automotive buses and inspecting live traffic.
The first milestone is **CAN**: pick an adapter, connect, and read frames.

Supported CAN backends (via [python-can](https://python-can.readthedocs.io/)):

| Adapter | python-can interface | Extra software |
| --- | --- | --- |
| Virtual (no hardware) | `virtual` | None |
| CANable (SLCAN) | `slcan` | Device on a COM port |
| CANable / candleLight | `gs_usb` | WinUSB / Zadig on Windows if needed |
| Kvaser | `kvaser` | Kvaser CANlib drivers |
| Vector | `vector` | Vector driver + XL Library; assign channels in Vector Hardware Config |
| Intrepid neoVI | `ics_neovi` | Intrepid drivers; optional `pip install python-ics` |

Ethernet, LIN, and SENT are planned as additional transports behind the same UI. They are not implemented yet.

Vendor DLLs are **not** bundled. Install each vendor’s driver on the PC that has the hardware.

## Run

Python 3.12+ is required.

```powershell
cd "C:\Users\Jabe\Documents\Signal Explorer"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m signal_explorer
```

Connect to **virtual** first. Enable **Inject demo traffic** to see frames without a physical bus.

## First development target

`src/signal_explorer/bus/service.py` is the bus session (connect, read thread, disconnect).
`src/signal_explorer/ui/main_window.py` is the connection bar and live trace.
Add a new adapter by extending `BACKENDS` in `src/signal_explorer/bus/backends.py`.
