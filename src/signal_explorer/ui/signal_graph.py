"""Bounded live plots for physical values decoded from the active CAN DBC."""
from math import floor, isfinite

from cantools.database.errors import DecodeError
from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from signal_explorer.ui.trace_timestamps import valid_timestamp


TIME_WINDOWS = (10, 30, 60)
OVERLAY_COLORS = ("#e69f00", "#009e73", "#cc79a7", "#d55e00", "#56b4e9")


class SignalHistory:
    """Keep the actual low and high samples in each 20 ms time bucket."""

    BUCKETS_PER_SECOND = 50
    CAPACITY = max(TIME_WINDOWS) * BUCKETS_PER_SECOND + 1

    def __init__(self):
        self.clear()

    def clear(self):
        self._buckets = {}

    def append(self, stamp, value):
        number = floor(stamp * self.BUCKETS_PER_SECOND)
        slot = number % self.CAPACITY
        point = (stamp, value)
        bucket = self._buckets.get(slot)
        if bucket is None or number > bucket[0]:
            self._buckets[slot] = (number, point, point)
        elif number == bucket[0]:
            _, low, high = bucket
            self._buckets[slot] = (
                number, point if value < low[1] else low,
                point if value >= high[1] else high,
            )

    def __iter__(self):
        for _, low, high in self._buckets.values():
            yield low
            if high != low:
                yield high


class SignalPlot(QWidget):
    def __init__(self, graph):
        super().__init__(graph)
        self.graph = graph
        self.setMinimumSize(270, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        # A live trace uses the theme accent even when the window loses focus.
        trace_color = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)
        painter.fillRect(self.rect(), palette.base())
        painter.setPen(palette.text().color())
        graph = self.graph
        if not graph.series or graph.latest is None:
            text = "Add DBC signals to start plotting." if not graph.series else "Waiting for matching CAN frames…"
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
            return

        seconds = graph.seconds.currentData()
        left = graph.latest - seconds
        width = self.width() - 82
        lane_height = (self.height() - 26) / len(graph.groups)
        for index, group in enumerate(graph.groups):
            top = index * lane_height
            legend_height = 18 * len(group)
            area = QRectF(68, top + legend_height, width, max(1, lane_height - legend_height - 5))
            traces = []
            for slot, key in enumerate(group):
                color = trace_color if slot == 0 else QColor(OVERLAY_COLORS[slot - 1])
                label = painter.fontMetrics().elidedText(
                    graph.series_label(key), Qt.TextElideMode.ElideRight, self.width() - 30)
                painter.setPen(color)
                y = top + slot * 18
                painter.drawLine(QPointF(6, y + 9), QPointF(20, y + 9))
                painter.setPen(palette.text().color())
                painter.drawText(QRectF(25, y, self.width() - 30, 18), Qt.AlignmentFlag.AlignLeft, label)
                traces.append((color, sorted((t, y) for t, y in graph.series[key] if t >= left)))
            painter.setPen(palette.mid().color())
            painter.drawRect(area)
            for tick in range(1, 4):
                x = area.left() + width * tick / 4
                painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            values = [value for _, points in traces for _, value in points]
            if not values:
                continue
            low, high = min(values), max(values)
            painter.setPen(palette.text().color())
            painter.drawText(4, int(area.top() + 11), f"{high:.4g}")
            if low != high and area.height() > 20:
                painter.drawText(4, int(area.bottom()), f"{low:.4g}")
            for color, points in traces:
                if not points:
                    continue
                path = QPainterPath()
                for point_index, (stamp, value) in enumerate(points):
                    x = area.left() + (stamp - left) * width / seconds
                    fraction = (value - low) / (high - low) if high != low else 0.5
                    y = area.bottom() - fraction * area.height()
                    if point_index == 0:
                        path.moveTo(x, y)
                    else:
                        path.lineTo(x, y)
                painter.setPen(color)
                painter.drawPath(path)
                painter.drawEllipse(path.currentPosition(), 2.5, 2.5)

        painter.setPen(palette.text().color())
        painter.drawText(68, self.height() - 7, f"−{seconds} s")
        painter.drawText(self.width() - 111, self.height() - 7, "Latest sample")


class SignalGraph(QWidget):
    MAX_SIGNALS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.series = {}
        self.groups = []
        self.definitions = {}
        self.by_frame = {}
        self.latest = None
        self._dirty = False
        self._paused = False

        self.channel = QComboBox()
        self.direction = QComboBox()
        self.direction.addItem("Received (RX)", True)
        self.direction.addItem("Sent (TX)", False)
        self.message = QComboBox()
        self.signal = QComboBox()
        for combo in (self.message, self.signal, self.channel):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.seconds = QComboBox()
        for span in TIME_WINDOWS:
            self.seconds.addItem(f"{span} s", span)
        self.seconds.setCurrentIndex(1)
        self.add_to = QComboBox()
        self.add_to.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        form = QFormLayout()
        for label, combo in (("Channel", self.channel), ("Direction", self.direction),
                             ("Message", self.message), ("Signal", self.signal),
                             ("Time window", self.seconds), ("Add To", self.add_to)):
            form.addRow(label, combo)
        self.add_button = QPushButton("Add signal")
        remove = QPushButton("Remove selected")
        clear = QPushButton("Clear samples")
        buttons = QHBoxLayout()
        for button in (self.add_button, remove, clear):
            buttons.addWidget(button)
        self.selection = QListWidget()
        self.selection.setMaximumHeight(75)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.plot = SignalPlot(self)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.selection)
        layout.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.plot)
        layout.addWidget(scroll, 1)

        self.message.currentIndexChanged.connect(self._update_signals)
        self.signal.currentIndexChanged.connect(self._update_picker)
        self.add_button.clicked.connect(self.add_signal)
        remove.clicked.connect(self.remove_signal)
        clear.clicked.connect(self.clear_samples)
        self.channel.currentIndexChanged.connect(self.clear_samples)
        self.direction.currentIndexChanged.connect(self.clear_samples)
        self.seconds.currentIndexChanged.connect(self.plot.update)
        self._redraw = QTimer(self, interval=100)
        self._redraw.timeout.connect(self._refresh_plot)
        self.set_database(None)
        self.set_channels(())

    def set_database(self, database):
        self.series.clear()
        self.groups.clear()
        self._update_add_to()
        self.selection.clear()
        self.by_frame.clear()
        self.definitions.clear()
        self.message.blockSignals(True)
        self.message.clear()
        if database is not None:
            for definition in sorted(database.messages, key=lambda item: item.name.casefold()):
                if definition.is_container or not definition.signals:
                    continue
                key = (definition.frame_id, definition.is_extended_frame)
                self.definitions[key] = definition
                self.message.addItem(definition.name, key)
        self.message.blockSignals(False)
        self._update_signals()
        self.clear_samples()

    def set_channels(self, channels):
        previous = self.channel.currentData()
        self.channel.blockSignals(True)
        self.channel.clear()
        for channel in channels:
            self.channel.addItem(channel['label'], str(channel['native_channel']))
        index = self.channel.findData(previous)
        if index >= 0:
            self.channel.setCurrentIndex(index)
        self.channel.blockSignals(False)
        self.clear_samples()

    def _update_signals(self):
        self.signal.clear()
        definition = self.definitions.get(self.message.currentData())
        if definition is not None:
            self.signal.addItems(sorted(signal.name for signal in definition.signals))
        self._update_picker()

    def set_paused(self, paused):
        self._paused = paused
        self._update_picker()

    def _update_picker(self):
        key = (self.message.currentData(), self.signal.currentText())
        self.add_button.setEnabled(self.signal.count() > 0 and key not in self.series
                                   and len(self.series) < self.MAX_SIGNALS)
        if not self.definitions:
            text = "Load a CAN DBC in the main window."
        elif len(self.series) == self.MAX_SIGNALS:
            text = f"{self.MAX_SIGNALS} signals selected. Remove one to add another."
        else:
            text = "High-rate data retains the minimum and maximum every 20 ms."
        self.status.setText(f"Exploring paused · {text}" if self._paused else text)

    def series_label(self, key):
        frame_key, name = key
        definition = self.definitions[frame_key]
        unit = definition.get_signal_by_name(name).unit
        return f"{definition.name}.{name}" + (f" ({unit})" if unit else "")

    def _update_add_to(self):
        previous = self.add_to.currentData()
        self.add_to.clear()
        self.add_to.addItem("New graph")
        for key in self.series:
            self.add_to.addItem(self.series_label(key), key)
        self.add_to.setCurrentIndex(max(0, self.add_to.findData(previous)))

    def _resize_plot(self):
        largest_group = max(map(len, self.groups), default=0)
        lane_height = 45 + 18 * largest_group
        self.plot.setMinimumHeight(max(180, lane_height * len(self.groups) + 26))

    def add_signal(self):
        key = (self.message.currentData(), self.signal.currentText())
        if not self.add_button.isEnabled():
            return
        target = self.add_to.currentData()
        if target is None:
            self.groups.append([key])
        else:
            next(group for group in self.groups if target in group).append(key)
        self.series[key] = SignalHistory()
        self._update_add_to()
        self.by_frame.setdefault(key[0], set()).add(key[1])
        self.selection.addItem(self.series_label(key))
        self._resize_plot()
        self._update_picker()
        self.plot.update()

    def remove_signal(self):
        row = self.selection.currentRow()
        if row < 0:
            return
        frame, name = list(self.series)[row]
        key = (frame, name)
        del self.series[key]
        group = next(group for group in self.groups if key in group)
        group.remove(key)
        if not group:
            self.groups.remove(group)
        self._update_add_to()
        self.by_frame[frame].remove(name)
        if not self.by_frame[frame]:
            del self.by_frame[frame]
        self.selection.takeItem(row)
        if not self.series:
            self.latest = None
        self._resize_plot()
        self._update_picker()
        self.plot.update()

    def clear_samples(self):
        for samples in self.series.values():
            samples.clear()
        self.latest = None
        self._resize_plot()
        self.plot.update()

    def append_frames(self, frames):
        channel = self.channel.currentData()
        if channel is None or not self.by_frame:
            return
        is_rx = self.direction.currentData()
        for frame in frames:
            key = (frame.arbitration_id, frame.is_extended_id)
            names = self.by_frame.get(key)
            if (not names or frame.is_error_frame or frame.is_remote_frame or frame.is_rx != is_rx
                    or str(frame.channel) != channel or not valid_timestamp(frame.timestamp)):
                continue
            try:
                values = self.definitions[key].decode(frame.data, decode_choices=False)
            except DecodeError:
                continue
            for name in names:
                value = values.get(name)
                if isinstance(value, (int, float)) and isfinite(value):
                    self.series[key, name].append(frame.timestamp, value)
                    self.latest = frame.timestamp if self.latest is None else max(self.latest, frame.timestamp)
                    self._dirty = True

    def _refresh_plot(self):
        if self._dirty:
            self._dirty = False
            self.plot.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._redraw.start()

    def hideEvent(self, event):
        self._redraw.stop()
        super().hideEvent(event)
