# CAN DBC editor

Click **Edit DBC** beside **Load DBC** and **Unload DBC** on the CAN screen.
The editor opens in its own window inside Signal Explorer. It starts with a
private copy of the loaded database, or an empty database if none is loaded.
Use **Open** to edit a different `.dbc` file or **New** to start from scratch.

## Basic editing

- Add, edit or delete messages. Message fields include hexadecimal CAN ID,
  standard/extended format, Classic CAN/CAN FD, payload length, cycle time,
  senders and comment.
- Add, edit or delete signals. Signal fields include name, DBC start bit,
  length, Intel/Motorola order, signedness, factor, offset, optional minimum
  and maximum, unit, receivers, comment and raw-value labels.
- Enter value labels one per line, such as `0=Released` and `1=Pressed`.
- Double-click a message or signal to open its properties.
- Use **Edit > Undo/Redo** to reverse or restore accepted edits.
- Use **Validate** to check the database. Invalid edits, including overlapping
  or out-of-bounds signals, are rejected without changing the document.

The message map sits to the right of the signal list, with a draggable divider
to resize the panels. It uses byte rows and bit columns 7 through 0. Selecting a
signal highlights its bits and shows its start, length and byte order below the
map. Motorola start bits follow DBC sawtooth numbering:
a signal starting at bit 7 proceeds through bit 0, then bit 15, and so on.
Hover over a cell for its DBC bit number and signal names. The DBC preview tab
shows the generated database text.

Enter a hexadecimal payload of exactly the selected message's length in
**Test decoding** to see its decoded physical values and value labels. This is
an offline calculation and does not transmit CAN traffic.

## Saving and isolation

Opening, editing and closing the editor do not connect or disconnect hardware,
send frames, pause a trace, stop scripts or affect recording. Unsaved edits do
not change the active decoder. **Save** writes the file atomically and reloads
the main CAN decoder only if that exact file is currently loaded. **Save As**
to a different file does not replace the active DBC. If no DBC is loaded, use
**Load DBC** after saving to begin decoding with the new file.

Closing, opening another file or starting a new document prompts to save any
unsaved changes. Canceling a close keeps the editor and main application open.

Existing multiplexing and floating-point definitions remain in the database,
but the basic forms do not configure multiplexing or float types. Multiplexer
signals cannot be renamed, and a multiplexer with dependent signals cannot be
deleted until its dependents are removed. Saving uses cantools serialization;
DBC whitespace and ordering may change. Text must be representable in the
DBC loader's default Windows-1252 encoding.

## Module boundaries

`signal_explorer.dbc.document` provides document snapshots, validation,
undo/redo and serialization without Qt, hardware or application dependencies.
`signal_explorer.ui.dbc_editor` provides the window and property dialogs.
`MainWindow` lazily opens and tracks editor windows, and accepts a saved
database only when its path matches the loaded DBC. There is no separate
application or editor connection to `BusService`.
