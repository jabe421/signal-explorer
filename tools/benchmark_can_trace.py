"""Compare current CAN model work with a git revision (no hardware access).

Run from the repository root with the application Python environment:
    python tools/benchmark_can_trace.py --baseline HEAD
This measures model ingestion and simulated cell reads, not physical bus rates.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import can
import cantools
from PySide6.QtWidgets import QApplication
from signal_explorer.ui.trace_model import SignalTreeModel


def measure(model_class, frames, database, batched):
    model = model_class(history_mode=False)
    model.set_database(database)
    inserted, changed = [0], [0]
    model.rowsInserted.connect(lambda *_: inserted.__setitem__(0, inserted[0]+1))
    model.dataChanged.connect(lambda *_: changed.__setitem__(0, changed[0]+1))
    start = time.perf_counter()
    if batched:
        for offset in range(0, len(frames), 1000):
            model.append_messages(frames[offset:offset+1000])
    else:
        for frame in frames:
            model.append_message(frame)
    ingest = time.perf_counter()-start
    definition = database.get_message_by_frame_id(0x200)
    with patch.object(definition, 'decode', wraps=definition.decode) as decoder:
        # Two cells per observed ID: every summary/child read shares cached decode.
        start = time.perf_counter()
        for _ in range(3):
            for row in range(model.rowCount()):
                for column in range(model.columnCount()):
                    model.index(row, column).data()
                parent = model.index(row, 0)
                for child in range(model.rowCount(parent)):
                    model.index(child, 9, parent).data()
        paint = time.perf_counter()-start
        decodes = decoder.call_count
    return dict(ingest_seconds=round(ingest, 4), cell_read_seconds=round(paint, 4),
                example_definition_decodes=decodes, row_insert_notifications=inserted[0],
                data_change_notifications=changed[0], latest_rows=model.rowCount())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', default='HEAD')
    parser.add_argument('--frames', type=int, default=20000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = subprocess.run(['git', 'show', args.baseline+':src/signal_explorer/ui/trace_model.py'],
                            cwd=root, capture_output=True, text=True, check=True).stdout
    namespace = {'__name__': 'baseline_trace_model'}
    exec(compile(source, '<baseline trace model>', 'exec'), namespace)
    app = QApplication.instance() or QApplication([])
    original = cantools.database.load_file(str(root/'examples/demo.dbc')).get_message_by_frame_id(0x200)
    definitions = []
    for identifier in range(1000):
        definition = deepcopy(original)
        definition.frame_id = identifier
        definition.name = f'Message{identifier}'
        definitions.append(definition)
    database = cantools.database.Database(messages=definitions)
    frames = [can.Message(timestamp=1000+i*.0001, channel=i%2,
                          arbitration_id=0x200+i%200, is_extended_id=False,
                          data=(i%65536).to_bytes(2, 'little')+bytes(6)) for i in range(args.frames)]
    result = dict(frames=len(frames), dbc_messages=len(definitions),
                  baseline_ref=args.baseline,
                  baseline=measure(namespace['SignalTreeModel'], frames, database, False),
                  current=measure(SignalTreeModel, frames, database, True))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
