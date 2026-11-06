"""Modeless CAN timing workbench; physical scan never transmits or connects TX."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QPushButton, QLabel, QPlainTextEdit, QTabWidget, QWidget)
from signal_explorer.bus.timing import TimingSettings, ADVANCED_INTERFACES, SCAN_INTERFACES, SCAN_RATES
from signal_explorer.bus.bitrate_scan import BitrateScanner


class TimingWindow(QDialog):
    scan_busy = Signal(bool)

    def __init__(self, context, apply_settings, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.context, self.apply_settings = context, apply_settings
        self.scanner = None
        self.setWindowTitle('Signal Explorer - CAN Timing')
        self.resize(800, 650)
        layout = QVBoxLayout(self)
        self.profile = QLabel(); self.profile.setWordWrap(True); layout.addWidget(self.profile)
        tabs = QTabWidget(); layout.addWidget(tabs)
        custom = QWidget(); form = QFormLayout(custom); tabs.addTab(custom, 'Custom bitrate and timing')
        self.custom = QCheckBox('Use custom connection rates')
        form.addRow(self.custom)
        self.rate = QSpinBox(); self.rate.setRange(1, 1000000); self.rate.setSuffix(' bit/s')
        self.data_rate = QSpinBox(); self.data_rate.setRange(1, 20000000); self.data_rate.setSuffix(' bit/s')
        form.addRow('Arbitration bitrate', self.rate); form.addRow('FD data bitrate', self.data_rate)
        self.advanced = QCheckBox('Specify sample point and SJW'); form.addRow(self.advanced)
        self.sample, self.data_sample = QDoubleSpinBox(), QDoubleSpinBox()
        self.sjw, self.data_sjw = QSpinBox(), QSpinBox()
        for widget in (self.sample, self.data_sample):
            widget.setRange(50, 99.9); widget.setDecimals(2); widget.setSuffix(' %')
        for widget in (self.sjw, self.data_sjw):
            widget.setRange(1, 128); widget.setSuffix(' tq')
        for label, widget in [('Arbitration sample point',self.sample), ('Arbitration SJW',self.sjw),
                               ('FD sample point',self.data_sample), ('FD SJW',self.data_sjw)]:
            form.addRow(label, widget)
        text = QLabel('Sample points are requests: controller timing is quantized. Preview shows calculated values. '
                      'Driver/hardware validation still applies. Applied settings affect the next CAN connection and all enabled CAN channels.')
        text.setWordWrap(True); form.addRow(text)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True); form.addRow(self.preview)
        row = QHBoxLayout(); form.addRow(row)
        self.preview_button = QPushButton('Preview timing'); self.preview_button.clicked.connect(self.show_preview)
        self.apply_button = QPushButton('Apply for next connection'); self.apply_button.clicked.connect(self.apply)
        row.addWidget(self.preview_button); row.addWidget(self.apply_button)
        scan = QWidget(); scan_layout = QVBoxLayout(scan); tabs.addTab(scan, 'Auto-bitrate detection')
        text = QLabel('Classic CAN only. Disconnect CAN before scanning. Each candidate opens the displayed channel in '
                      'hardware listen-only mode; no test frames are sent. The bus must already have active traffic. '
                      'An existing sender needs another active node to acknowledge frames. Detection never enables transmission.')
        text.setWordWrap(True); scan_layout.addWidget(text)
        scan_form = QFormLayout(); scan_layout.addLayout(scan_form)
        from PySide6.QtWidgets import QLineEdit
        self.candidates = QLineEdit(', '.join(map(str, SCAN_RATES)))
        scan_form.addRow('Candidate rates (bit/s)', self.candidates)
        self.dwell = QDoubleSpinBox(); self.dwell.setRange(.1,10); self.dwell.setValue(1); self.dwell.setSuffix(' s / candidate')
        self.minimum = QSpinBox(); self.minimum.setRange(2,1000); self.minimum.setValue(5)
        scan_form.addRow('Observation time', self.dwell); scan_form.addRow('Minimum valid data frames', self.minimum)
        self.scan_status = QLabel(); self.scan_status.setWordWrap(True); scan_layout.addWidget(self.scan_status)
        self.scan_output = QPlainTextEdit(); self.scan_output.setReadOnly(True); scan_layout.addWidget(self.scan_output)
        self.scan_button = QPushButton('Start passive scan'); self.scan_button.clicked.connect(self.start_scan)
        self.cancel_button = QPushButton('Cancel scan'); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel_scan)
        row = QHBoxLayout(); row.addWidget(self.scan_button); row.addWidget(self.cancel_button); scan_layout.addLayout(row)
        close = QPushButton('Close'); close.clicked.connect(self.close); layout.addWidget(close)
        self.custom.toggled.connect(self.sync_controls); self.advanced.toggled.connect(self.sync_controls)
        self.refresh_context()

    @property
    def busy(self):
        return self.scanner is not None

    def refresh_context(self):
        if self.busy:
            return
        c = self.context()
        request = c['settings'] or TimingSettings(c['bitrate'], c['data_bitrate'])
        self.custom.setChecked(c['settings'] is not None)
        for widget, value in ((self.rate, request.bitrate),(self.data_rate, request.data_bitrate),
            (self.sample,request.sample_point),(self.data_sample,request.data_sample_point),
            (self.sjw,request.sjw),(self.data_sjw,request.data_sjw)):
            widget.setValue(value)
        self.advanced.setChecked(request.advanced)
        self.profile.setText(f"CAN adapter: {c['label']} | Channel: {c['channel']} | "
                             + ('CAN FD' if c['fd'] else 'Classic CAN'))
        self.sync_controls()

    def sync_controls(self):
        c = self.context(); editable = not c['connected'] and not self.busy
        custom = editable and self.custom.isChecked()
        capable = c['interface'] in ADVANCED_INTERFACES
        self.custom.setEnabled(editable); self.rate.setEnabled(custom)
        self.data_rate.setEnabled(custom and c['fd'])
        self.advanced.setEnabled(custom and capable)
        if not capable: self.advanced.setChecked(False)
        for widget in (self.sample, self.sjw): widget.setEnabled(custom and capable and self.advanced.isChecked())
        for widget in (self.data_sample, self.data_sjw): widget.setEnabled(custom and capable and self.advanced.isChecked() and c['fd'])
        self.apply_button.setEnabled(editable); self.preview_button.setEnabled(not self.busy)
        scan_ok = editable and c['interface'] in SCAN_INTERFACES and not c['fd'] and bool(c['channel'])
        self.scan_button.setEnabled(scan_ok); self.cancel_button.setEnabled(self.busy)
        for widget in (self.candidates, self.dwell, self.minimum): widget.setEnabled(not self.busy)
        if not self.busy:
            self.scan_status.setText('Ready for a receive-only Classic CAN scan.' if scan_ok else
                'Scan unavailable: disconnect CAN, select Classic CAN and a supported Vector/PCAN/Kvaser/NI channel. '
                'Virtual traffic cannot determine physical bitrate; Intrepid and CANable silent setup is not yet qualified.')

    def settings(self):
        return TimingSettings(self.rate.value(), self.data_rate.value(), self.advanced.isChecked(),
                              self.sample.value(), self.sjw.value(), self.data_sample.value(), self.data_sjw.value())

    def show_preview(self):
        try:
            c = self.context(); request = self.settings()
            timing = request.build(c['interface'], c['fd'])
            self.preview.setPlainText(str(timing) if timing is not None else
                                     f'{request.bitrate} bit/s arbitration; {request.data_bitrate} bit/s FD data. Driver timing defaults.')
            return True
        except Exception as exc:
            self.preview.setPlainText(str(exc)); return False

    def apply(self):
        if self.context()['connected'] or self.busy:
            return
        if self.custom.isChecked() and not self.show_preview():
            return
        self.apply_settings(self.settings() if self.custom.isChecked() else None)
        self.preview.appendPlainText('Applied for the next connection.' if self.custom.isChecked() else 'Standard bitrate presets restored.')

    def start_scan(self):
        if self.busy or not self.scan_button.isEnabled():
            return
        try:
            c = self.context()
            rates = [int(part.strip()) for part in self.candidates.text().split(',')]
            if not rates or len(rates)>32 or any(not 1<=r<=1000000 for r in rates):
                raise ValueError('Enter 1-32 rates from 1 to 1000000 bit/s.')
            self.scanner = BitrateScanner(c['scan_kwargs'],rates,self.dwell.value(),self.minimum.value(),self)
            self.scanner.progress.connect(self.scan_output.appendPlainText)
            self.scanner.result.connect(self.scan_result)
            self.scanner.failed.connect(self.scan_output.appendPlainText)
            self.scanner.finished.connect(self.scan_finished)
            self.scan_output.clear(); self.scan_busy.emit(True); self.sync_controls()
            self.scan_status.setText('Scanning in hardware listen-only mode...')
            self.scanner.start()
        except Exception as exc:
            self.scanner = None; self.scan_output.appendPlainText(str(exc)); self.scan_busy.emit(False); self.sync_controls()

    def scan_result(self, rates):
        if self.scanner.cancelled.is_set():
            self.scan_output.appendPlainText('Scan cancelled. Connection settings unchanged.')
        elif len(rates)==1:
            # A detected rate is a candidate for a subsequent listen-only session.
            self.custom.setChecked(True); self.rate.setValue(rates[0]); self.advanced.setChecked(False)
            self.apply_settings(TimingSettings(bitrate=rates[0], data_bitrate=self.data_rate.value()), detected=True)
            self.scan_output.appendPlainText(f'Candidate: {rates[0]} bit/s. Applied for next connection in listen-only mode; verify known traffic.')
        else:
            self.scan_output.appendPlainText('Unable to determine bitrate: no unique candidate. '
                                             + ('Multiple rates matched.' if rates else 'Check existing traffic and wiring or increase observation time.'))

    def scan_finished(self):
        scanner, self.scanner = self.scanner, None
        scanner.deleteLater(); self.scan_busy.emit(False); self.sync_controls()

    def cancel_scan(self):
        if self.scanner:
            self.scanner.cancel(); self.scan_status.setText('Cancelling; waiting for the current driver operation to return...')

    def closeEvent(self, event):
        if self.busy:
            self.cancel_scan(); event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        if self.busy: self.cancel_scan()
        else: super().reject()
