"""Independent CAN analysis window; polling only, no per-frame Qt work."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, QAbstractItemView)


class BusHealthWindow(QMainWindow):
    close_requested = Signal()

    HEADERS = ('Channel', 'Observed load est.', 'Driver load', 'Frames/s', 'RX', 'TX',
               'Error frames', 'Native error events', 'Send-call failures', 'Controller state',
               'TEC', 'REC', 'Driver overruns', 'Native update age')

    def __init__(self, service, loss_provider=lambda: {}, parent=None, *, embedded=False):
        super().__init__(parent, Qt.WindowType.Widget if embedded else Qt.WindowType.Window)
        self.service, self.loss_provider = service, loss_provider
        self.setWindowTitle('Signal Explorer - CAN Bus Health')
        self.resize(1360, 620)
        root = QWidget(); layout = QVBoxLayout(root); self.setCentralWidget(root)
        self.session = QLabel(); self.session.setWordWrap(True); layout.addWidget(self.session)
        self.note = QLabel('Observed load is a frame-based estimate over the last 2 seconds, not a wire measurement. '
            'Classic uses a stuffing range; CAN FD uses an approximate nominal/data phase split. '
            'Retransmissions, error occupancy and lost/filtered traffic are not accounted for. '
            'TX acceptance estimates can differ from on-wire timing. Values above 100% remain visible as a quality warning.')
        self.note.setWordWrap(True); layout.addWidget(self.note)
        self.table = QTableWidget(0, len(self.HEADERS)); self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSortingEnabled(False); layout.addWidget(self.table)
        self.losses = QLabel(); self.losses.setWordWrap(True); layout.addWidget(self.losses)
        self.details = QLabel(); self.details.setWordWrap(True); layout.addWidget(self.details)
        self.table.itemSelectionChanged.connect(self.show_details)
        row = QHBoxLayout(); layout.addLayout(row)
        reset = QPushButton('Reset observed counts'); reset.clicked.connect(self.reset_counts)
        reset.setToolTip('Reset only application health counts and the rate window. Does not clear hardware counters, trace, script history or logging.')
        close = QPushButton('Close')
        if embedded:
            close.clicked.connect(self.close_requested)
        else:
            close.clicked.connect(self.close)
        row.addWidget(reset); row.addStretch(); row.addWidget(close)
        self._snapshot = None
        self._paused = False
        self.timer = QTimer(self); self.timer.setInterval(250); self.timer.timeout.connect(self.refresh)
        self.refresh()

    @staticmethod
    def value(value):
        return 'Unavailable' if value is None else str(value)

    def set_paused(self, paused):
        if self._paused == paused:
            return
        self._paused = paused
        if paused:
            self.timer.stop()
            self.session.setText("Exploring paused | " + self.session.text())
        elif self.isVisible():
            self.refresh()
            self.timer.start()

    def refresh(self):
        if self._paused:
            return
        snapshot = self._snapshot = self.service.health_snapshot()
        info = snapshot['session']
        if not info:
            self.session.setText('CAN disconnected. Connect a CAN adapter or Virtual CAN to start health observations.')
        else:
            state = 'Live' if snapshot['active'] else 'Disconnected - last session frozen'
            mode = 'Listen-only' if info.get('listen_only') else 'Normal'
            synthetic = ' | SIMULATED timing, not physical bus load' if info.get('interface')=='virtual' else ''
            self.session.setText(f"{state} | {info['adapter']} | {info['bitrate']} bit/s nominal"
                +(f" / {info['data_bitrate']} bit/s data" if info.get('fd') else '')+f' | {mode}'+synthetic)
        if snapshot['fault']:
            self.session.setText(self.session.text()+' | Capture fault: '+snapshot['fault'])
        rows = snapshot['rows']
        if self.table.rowCount()!=len(rows): self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            native = row['native']; age = row['native_age']
            load = ('Warming up' if row['load_low'] is None else
                    f"{row['load_low']:.1f}-{row['load_high']:.1f}%")
            driver_load = native.get('driver_load')
            state = native.get('state') or 'Unavailable'
            if age is not None and age>3: state += ' (stale)'
            overruns = self.value(native.get('overruns'))
            if native.get('overrun_flag') is not None:
                overruns = 'Flag set' if native['overrun_flag'] else 'Flag not set'
            values = (row['label'],load, 'Unavailable' if driver_load is None else f'{driver_load:.1f}%',
                'Warming up' if row['rate'] is None else f"{row['rate']:.1f}", row['rx'],row['tx'],
                row['error_frames'], row['native_events'], row['send_failures'], state,
                self.value(native.get('tec')),self.value(native.get('rec')),overruns,
                'Not reported' if age is None else f'{age:.1f} s'+(' (stale)' if age>3 else ''))
            detail = self.row_details(row)
            for j, value in enumerate(values):
                item = self.table.item(i,j)
                if item is None: item = QTableWidgetItem(); self.table.setItem(i,j,item)
                text = str(value)
                if item.text()!=text: item.setText(text)
                item.setToolTip(detail)
                item.setForeground(QColor('#B00020') if j==9 and 'Bus off' in text else
                                   QColor('#995300') if j==9 and ('passive' in text.lower() or 'warning' in text.lower())
                                   else QColor('#202020'))
        loss = self.loss_provider()
        self.losses.setText(f"Separate capture accounting | Display queue drops: {snapshot['display_drops']} | "
            f"Script ring drops (current/last run): {self.value(loss.get('script_drops'))} | "
            f"Recorder: {loss.get('recorder', 'Unavailable')}. These are not CAN controller error counters.")
        self.show_details()

    def row_details(self, row):
        native = row['native']
        return (f"Native channel: {row['channel']} | Source: {native.get('source', 'Controller diagnostics unavailable/not yet reported')}\n"
                f"Last observed send/error event: {row['last_error'] or 'None observed'}\n"
                f"Native detail: {native.get('last_error') or native.get('status_hex') or 'Not reported'}\n"
                f"Query note: {native.get('note') or 'None'}\n"
                'TEC/REC are current driver-reported counters, not error-frame totals. Driver overrun counts/flags have vendor-defined scope.')

    def show_details(self):
        if self._snapshot is None: return
        row = self.table.currentRow()
        if 0<=row<len(self._snapshot['rows']):
            self.details.setText(self.row_details(self._snapshot['rows'][row]))
        else:
            self.details.setText(self._snapshot['fault'] or 'Select a channel for the data source and last error. Unavailable never means zero.')

    def reset_counts(self):
        self.service.health.reset_observations(); self.refresh()

    def showEvent(self, event):
        if not self._paused:
            self.refresh()
            self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop(); super().hideEvent(event)
