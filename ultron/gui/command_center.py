"""Native Qt dashboard and navigation for the ULTRON control center."""

from datetime import datetime
from math import cos, pi, sin
from pathlib import Path
import sqlite3
import json
from functools import lru_cache
from time import monotonic

import psutil
try:
    from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
    from PySide6.QtWidgets import (
        QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
        QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
    )
except ImportError:
    from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
    from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
    from PyQt5.QtWidgets import (
        QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
        QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
    )

from ultron.image import image_generator
from ultron.kernel import kernel
from ultron.model.loader import model_manager
from ultron.tasks.manager import TaskStatus, task_manager
from ultron.tools.registry import tool_registry
from ultron.voice.assistant import AssistantState, voice_assistant
from ultron.workspace.selector import workspace_manager


STYLE = """
QLineEdit#commandPrompt { background: #120e11; color: #f2e3e5; border: 1px solid #55343d; border-radius: 6px; padding: 8px; }
QLineEdit#commandPrompt:focus { border-color: #fb625e; }
QWidget#commandShell, QWidget#commandHome, QWidget#commandContent, QStackedWidget#commandPages { background: #100d0f; }
QFrame#commandRail { background: #151014; border-right: 1px solid #35232a; }
QFrame#commandHeader, QFrame#commandDock { background: #151014; border-bottom: 1px solid #35232a; }
QFrame#commandDock { border-bottom: none; border-top: 1px solid #35232a; }
QFrame#commandCard { background: #181316; border: 1px solid #432c34; border-radius: 12px; }
QFrame#coreCard { background: #140e13; border: 1px solid #a33e46; border-radius: 12px; }
QWidget#commandRail QLabel, QFrame#commandHeader QLabel, QFrame#commandDock QLabel,
QFrame#commandCard QLabel, QWidget#commandHome QLabel { background: transparent; }
QFrame#commandRail QLabel#commandMark { color: #100509; background: #f34c55; border-radius: 10px; font-size: 21px; }
QLabel#commandBrand { color: #fff3f2; font: 800 22px "Segoe UI"; letter-spacing: 3px; }
QLabel#commandPageName { color: #fff0ee; font: 700 18px "Segoe UI"; }
QLabel#commandTitle { color: #fff0ee; font: 700 32px "Segoe UI"; }
QLabel#commandSection { color: #ffc0b9; font: 700 15px "Segoe UI"; }
QLabel#commandText { color: #f2e3e5; font-size: 13px; }
QLabel#commandMuted { color: #b6a3a9; font-size: 12px; }
QLabel#commandClock { color: #ff9289; font: 700 16px "Consolas"; }
QLabel#commandCoreStatus { color: #ffa9a1; background: #2b141a; border: 1px solid #663039;
    border-radius: 8px; padding: 6px 10px; font: 600 12px "Segoe UI"; }
QFrame#coreCard QLabel#commandCoreStatus { background: #2b141a; }
QPushButton#commandNav { min-height: 26px; padding: 7px 12px; text-align: left;
    background: transparent; border: 1px solid transparent; color: #c5adb2; }
QPushButton#commandNav:hover { background: #2b1920; color: #fff1ee; }
QPushButton#commandNav:checked { background: #381a22; border: 1px solid #a3454d; color: #ffb4a9; font-weight: 700; }
QPushButton#commandAction { padding: 8px 11px; text-align: left;
    background: #28191e; border: 1px solid #55343d; color: #f3e0e2; }
QPushButton#commandAction:hover { background: #492029; border-color: #de5a62; }
QPushButton#commandLink { padding: 4px 0; text-align: left;
    color: #ff978e; background: transparent; border: 1px solid transparent; }
QPushButton#commandLink:hover { color: #fff1ee; }
QPushButton#commandTalk { min-width: 185px; padding: 9px 20px; background: #fb625e;
    color: #18080a; font-weight: 800; border: 1px solid #ff8c83; border-radius: 9px; }
QPushButton#commandTalk:hover { background: #ff827a; }
QPushButton#commandTalk:pressed { background: #de484c; }
QPushButton#commandNav:focus, QPushButton#commandAction:focus, QPushButton#commandLink:focus,
QPushButton#commandTalk:focus { border: 1px solid #ffaea6; }
QProgressBar#commandMeter { background: #38272d; border: none; border-radius: 3px; }
QProgressBar#commandMeter::chunk { background: #f96862; border-radius: 3px; }
QTextBrowser#commandTranscript { background: #120e11; border: 1px solid #422d35;
    border-radius: 7px; color: #dcc7cb; font-size: 12px; }
"""


def label(text, role):
    widget = QLabel(text)
    widget.setObjectName(role)
    widget.setTextFormat(Qt.PlainText)
    return widget


def button(text, role, callback):
    widget = QPushButton(text)
    widget.setObjectName(role)
    widget.setCursor(Qt.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


@lru_cache(maxsize=1)
def _land_points():
    asset = Path(__file__).resolve().parents[2] / "assets" / "globe_land.json"
    try:
        coordinates = json.loads(asset.read_text(encoding="utf-8"))["points"]
    except (OSError, ValueError, KeyError):
        return []
    return [(lon * pi / 180, sin(lat * pi / 180), cos(lat * pi / 180)) for lon, lat in coordinates]


class CoreGraphic(QWidget):
    """Render ULTRON's core as a scalable, original instrument graphic."""

    ACTIVE = {
        AssistantState.ARMED: ("Listening", "#f6a99b"),
        AssistantState.CAPTURING: ("Capturing voice", "#ff716c"),
        AssistantState.TRANSCRIBING: ("Transcribing", "#ff9278"),
        AssistantState.THINKING: ("Processing", "#ffbc82"),
        AssistantState.SPEAKING: ("Speaking", "#ffc1a1"),
    }

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(170)
        self.setAccessibleName("ULTRON globe")
        self.setToolTip("Drag to rotate. Land data from Natural Earth.")
        self.setCursor(Qt.OpenHandCursor)
        self._longitude = 62 * pi / 180
        self._last_x = None
        self._event_points = []
        self._projection = (sin(18 * pi / 180), cos(18 * pi / 180))
        self._state = AssistantState.OFF
        self._state_started = monotonic()
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(33)
        self._frame_timer.timeout.connect(self.update)

    def set_state(self, state):
        if state == self._state:
            return
        self._state = state
        self._state_started = monotonic()
        if state in self.ACTIVE and self.isVisible():
            self._frame_timer.start()
        else:
            self._frame_timer.stop()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self._state in self.ACTIVE:
            self._frame_timer.start()

    def hideEvent(self, event):
        self._frame_timer.stop()
        super().hideEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._last_x = event.pos().x()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self._last_x is not None:
            x = event.pos().x()
            self._longitude -= (x - self._last_x) * 0.012
            self._last_x = x
            self.update()

    def mouseReleaseEvent(self, event):
        self._last_x = None
        self.setCursor(Qt.OpenHandCursor)

    def set_events(self, coordinates):
        self._event_points = [(lon * pi / 180, sin(lat * pi / 180), cos(lat * pi / 180))
                              for lon, lat in coordinates]
        self.update()

    def _draw_globe(self, painter):
        ocean = QRadialGradient(QPointF(-24, -30), 116)
        ocean.setColorAt(0, QColor("#35222d"))
        ocean.setColorAt(0.65, QColor("#241620"))
        ocean.setColorAt(1, QColor("#0e090f"))
        painter.setPen(QPen(QColor("#8f464a"), 1.1))
        painter.setBrush(ocean)
        painter.drawEllipse(QPointF(), 84, 84)
        sin_center, cos_center = self._projection
        land = [[], [], []]
        for lon, sin_lat, cos_lat in _land_points():
            angle = lon - self._longitude
            depth = sin_center * sin_lat + cos_center * cos_lat * cos(angle)
            if depth > 0:
                x = 84 * cos_lat * sin(angle)
                y = -84 * (cos_center * sin_lat - sin_center * cos_lat * cos(angle))
                land[min(2, int(depth * 3))].append(QPointF(x, y))
        for points, color in zip(land, ("#7b4049", "#bb5f60", "#ff8e7b")):
            painter.setPen(QPen(QColor(color), 1.75, Qt.SolidLine, Qt.RoundCap))
            if points:
                painter.drawPoints(points)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#ffd1a7"), 1.5))
        for lon, sin_lat, cos_lat in self._event_points:
            angle = lon - self._longitude
            if sin_center * sin_lat + cos_center * cos_lat * cos(angle) > 0:
                x = 84 * cos_lat * sin(angle)
                y = -84 * (cos_center * sin_lat - sin_center * cos_lat * cos(angle))
                painter.drawEllipse(QPointF(x, y), 3, 3)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        painter.translate(width / 2, height / 2)
        radius = min(width / 4.1, height * 0.43)
        painter.scale(radius / 100, radius / 100)
        elapsed = monotonic() - self._state_started
        caption_text, accent = self.ACTIVE.get(self._state, ("Global overview", "#ffab9b"))

        glow = QRadialGradient(QPointF(0, 0), 165)
        center_color = QColor(accent).darker(235) if self._state in self.ACTIVE else QColor("#5b2630")
        glow.setColorAt(0, center_color)
        glow.setColorAt(0.58, QColor("#2e1720"))
        glow.setColorAt(1, QColor(20, 14, 19, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(), 165, 165)
        painter.setBrush(Qt.NoBrush)

        # Corner guides and a sparse grid stay behind the brighter circular instrument.
        painter.setPen(QPen(QColor(211, 89, 100, 37), 0.8))
        for position in range(-160, 161, 20):
            painter.drawLine(QPointF(position, -105), QPointF(position, 105))
        for position in range(-100, 101, 20):
            painter.drawLine(QPointF(-175, position), QPointF(175, position))

        self._draw_globe(painter)
        for ring, color in [(104, "#502b35"), (91, "#783e45")]:
            painter.setPen(QPen(QColor(color), 0.9))
            painter.drawEllipse(QPointF(), ring, ring)

        for offset in range(0, 360, 5):
            angle = offset * pi / 180
            inner = 94 if offset % 30 == 0 else 98
            painter.setPen(QPen(QColor("#d47f7e" if offset % 30 == 0 else "#70404c"), 1.0))
            painter.drawLine(
                QPointF(inner * cos(angle), inner * sin(angle)),
                QPointF(105 * cos(angle), 105 * sin(angle)),
            )

        for start, sweep, color in [(22, 49, "#f66062"), (124, 34, "#eba976"), (202, 55, "#f66062"), (306, 25, "#905661")]:
            painter.setPen(QPen(QColor(color), 2.6, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(QRectF(-90, -90, 180, 180), start * 16, sweep * 16)

        painter.save()
        painter.rotate(-24)
        painter.setPen(QPen(QColor(214, 89, 104, 119), 0.9))
        painter.drawEllipse(QRectF(-142, -38, 284, 76))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ffb8a6"))
        painter.drawEllipse(QPointF(139, 8), 3, 3)
        painter.setBrush(QColor("#eba976"))
        painter.drawEllipse(QPointF(-127, -17), 2.7, 2.7)
        painter.restore()

        if self._state in self.ACTIVE:
            self._draw_activity(painter, elapsed, QColor(accent))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(25, 12, 17, 225))
        painter.drawRoundedRect(QRectF(-80, -23, 160, 52), 8, 8)
        wordmark = QFont("Segoe UI")
        wordmark.setPixelSize(30)
        wordmark.setBold(True)
        wordmark.setLetterSpacing(QFont.AbsoluteSpacing, 3.3)
        painter.setFont(wordmark)
        painter.setPen(QColor("#fff0ed"))
        painter.drawText(QRectF(-84, -24, 168, 42), Qt.AlignCenter, "ULTRON")
        caption = QFont("Consolas")
        caption.setPixelSize(9)
        caption.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        painter.setFont(caption)
        painter.setPen(QColor(accent))
        painter.drawText(QRectF(-70, 14, 140, 17), Qt.AlignCenter, caption_text.upper())

    def _draw_activity(self, painter, elapsed, color):
        painter.setPen(QPen(color, 2.5, Qt.SolidLine, Qt.RoundCap))
        if self._state == AssistantState.THINKING:
            start = int(-elapsed * 175) % 360
            painter.drawArc(QRectF(-109, -109, 218, 218), start * 16, 68 * 16)
            return

        for offset in (0, 0.5):
            phase = (elapsed * 0.95 + offset) % 1
            radius = 91 + 20 * phase
            pulse = QColor(color)
            pulse.setAlpha(round(190 * (1 - phase)))
            painter.setPen(QPen(pulse, 1.8))
            painter.drawEllipse(QPointF(), radius, radius)

        if self._state == AssistantState.SPEAKING:
            painter.setPen(QPen(color, 2.4, Qt.SolidLine, Qt.RoundCap))
            for index in range(-7, 8):
                envelope = 1 - abs(index) / 8
                height = 2 + 11 * envelope * (0.35 + 0.65 * abs(sin(elapsed * 8 + index * 0.78)))
                x = index * 6
                painter.drawLine(QPointF(x, 49 - height), QPointF(x, 49 + height))


class CommandCenter(QWidget):
    market_pairs = ("BTC-USD", "ETH-USD", "SOL-USD")

    def __init__(self, voice_panel, open_view, add_task, toggle_services, fetch_events, fetch_quotes):
        super().__init__()
        self.setObjectName("commandHome")
        self._meters = {}
        self._services = {}
        self._task_rows = []
        self._routine_rows = []
        self._event_rows = []
        self._market_rows = {}
        self._event_summary = "Earthquake feed has not been refreshed."

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("commandContent")
        content.setMinimumHeight(655)
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(label("Intelligence overview", "commandTitle"))
        heading.addStretch()
        self.workspace = label("", "commandMuted")
        heading.addWidget(self.workspace)
        layout.addLayout(heading)

        main = QHBoxLayout()
        main.setSpacing(10)
        left, center, right = (QVBoxLayout() for _ in range(3))
        for column, size in ((left, 4), (center, 8), (right, 4)):
            column.setSpacing(10)
            main.addLayout(column, size)

        objectives, tasks_layout = self._card("Objectives")
        self._task_rows = self._rows(tasks_layout, 4)
        tasks_layout.addStretch()
        task_actions = QHBoxLayout()
        task_actions.addWidget(button("+ New task", "commandLink", add_task))
        task_actions.addStretch()
        task_actions.addWidget(button("Automation →", "commandLink", lambda: open_view("Automation")))
        tasks_layout.addLayout(task_actions)
        left.addWidget(objectives, 1)

        services, services_layout = self._card("System status")
        for key, name in (("kernel", "Kernel"), ("model", "Local model"), ("voice", "Voice assistant"),
                          ("image", "Image backend"), ("tools", "Registered tools")):
            row = QHBoxLayout()
            row.addWidget(label(name, "commandText"))
            row.addStretch()
            value = label("", "commandMuted")
            row.addWidget(value)
            services_layout.addLayout(row, 1)
            self._services[key] = value
        self.services_button = button("Start services", "commandAction", toggle_services)
        services_layout.addWidget(self.services_button)
        left.addWidget(services, 1)

        hero, hero_layout = self._card("Global intelligence", "coreCard")
        self.core_status = label("Initializing", "commandCoreStatus")
        hero_layout.itemAt(0).layout().addWidget(self.core_status)
        self.core = CoreGraphic()
        hero_layout.addWidget(self.core, 1)
        voice_panel.state_changed.connect(self.set_voice_state)
        center.addWidget(hero, 1)

        events, event_layout = self._card("Live events")
        self.events_button = button("Refresh", "commandLink", fetch_events)
        event_layout.itemAt(0).layout().addWidget(self.events_button)
        self.event_status = label("USGS earthquakes M2.5+ · past 24 hours", "commandMuted")
        event_layout.addWidget(self.event_status)
        self._event_rows = self._rows(event_layout, 2)
        self._set_rows(self._event_rows, ["Refresh to load events."])
        event_layout.addWidget(button("Open monitor →", "commandLink", lambda: open_view("Monitor")))
        center.addWidget(events)

        markets, market_layout = self._card("Market watch")
        self.market_button = button("Refresh", "commandLink", fetch_quotes)
        market_layout.itemAt(0).layout().addWidget(self.market_button)
        for pair in self.market_pairs:
            row = QHBoxLayout()
            row.addWidget(label(pair, "commandText"))
            row.addStretch()
            quote = label("—", "commandMuted")
            row.addWidget(quote)
            market_layout.addLayout(row, 1)
            self._market_rows[pair] = quote
        self.market_status = label("CoinGecko snapshot · USD", "commandMuted")
        market_layout.addWidget(self.market_status)
        right.addWidget(markets, 1)

        resources, resources_layout = self._card("System monitor")
        for name in ("CPU", "Memory", "Disk"):
            gauge = QVBoxLayout()
            gauge.setSpacing(6)
            metric = QHBoxLayout()
            metric.addWidget(label(name, "commandText"))
            metric.addStretch()
            value = label("", "commandMuted")
            metric.addWidget(value)
            gauge.addLayout(metric)
            meter = QProgressBar()
            meter.setObjectName("commandMeter")
            meter.setAccessibleName(name + " usage")
            meter.setTextVisible(False)
            meter.setFixedHeight(5)
            gauge.addWidget(meter)
            resources_layout.addLayout(gauge, 1)
            self._meters[name] = (value, meter)
        right.addWidget(resources, 1)
        layout.addLayout(main, 3)

        lower = QHBoxLayout()
        lower.setSpacing(10)
        activity, activity_layout = self._card("Voice activity")
        activity.setMaximumHeight(155)
        voice_panel.log.setMaximumHeight(88)
        activity_layout.addWidget(voice_panel, 1)
        lower.addWidget(activity, 2)
        routines, routine_layout = self._card("Next routines")
        routines.setMaximumHeight(155)
        routine_layout.itemAt(0).layout().addWidget(button("Briefing", "commandLink", self._show_briefing))
        self._routine_rows = self._rows(routine_layout, 3)
        routine_layout.addStretch()
        lower.addWidget(routines, 1)
        layout.addLayout(lower)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(2500)
        self.refresh()
        self.set_voice_state(voice_assistant.state)

    @staticmethod
    def _rows(layout, count):
        rows = []
        for _ in range(count):
            item = label("", "commandText")
            item.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            layout.addWidget(item)
            rows.append(item)
        return rows

    @staticmethod
    def _set_rows(rows, values):
        for index, item in enumerate(rows):
            title = values[index] if index < len(values) else ""
            item.setToolTip(title)
            item.setText(item.fontMetrics().elidedText(title, Qt.ElideRight, max(100, item.width() - 5)))

    def show_events(self, features):
        self.events_button.setEnabled(True)
        captions, coordinates = [], []
        for feature in features:
            details = feature.get("properties") or {}
            place = details.get("place")
            if place:
                captions.append(f"M{details.get('mag', '—')}  {place}")
            point = (feature.get("geometry") or {}).get("coordinates") or []
            if len(point) >= 2 and all(isinstance(value, (int, float)) for value in point[:2]):
                coordinates.append(point[:2])
        self.core.set_events(coordinates)
        self.event_status.setText(f"USGS · checked {datetime.now():%H:%M}")
        self._event_summary = captions[0] if captions else "No magnitude 2.5+ USGS earthquakes in the past 24 hours."
        self._set_rows(self._event_rows, captions or [self._event_summary])

    def show_quotes(self, quotes):
        self.market_button.setEnabled(True)
        for pair, cell in self._market_rows.items():
            if pair in quotes:
                price, change = quotes[pair]
                cell.setText(f"${price:,.2f}  {change:+.2f}%")
                cell.setStyleSheet(f"color: {'#83d7b0' if change >= 0 else '#ff8f89'}")
            else:
                cell.setText("Unavailable")
                cell.setStyleSheet("")
        self.market_status.setText(f"CoinGecko · checked {datetime.now():%H:%M}" if quotes else "CoinGecko quotes unavailable")

    def _show_briefing(self):
        self.refresh()
        resources = ", ".join(f"{name} {caption.text()}" for name, (caption, _) in self._meters.items())
        briefing = "\n\n".join((self.workspace.text(), f"System resources: {resources}",
                                  "Objectives: " + "; ".join(self._objective_summary),
                                  "Next routines: " + "; ".join(self._routine_summary),
                                  "Live events: " + self._event_summary))
        dialog = QMessageBox(self)
        dialog.setWindowTitle("ULTRON briefing")
        dialog.setTextFormat(Qt.PlainText)
        dialog.setText(briefing)
        dialog.exec()

    @staticmethod
    def _card(title, role="commandCard"):
        panel = QFrame()
        panel.setObjectName(role)
        body = QVBoxLayout(panel)
        body.setContentsMargins(15, 13, 15, 13)
        body.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(label(title, "commandSection"))
        header.addStretch()
        body.addLayout(header)
        return panel, body

    def refresh(self):
        if not self.isVisible() and self._services["kernel"].text():
            return
        workspace = workspace_manager.get_current_workspace()
        self.workspace.setText(f"Workspace  /  {workspace.name[:38]}" if workspace else "No workspace selected")
        ready = kernel._running
        self._show_core_status(voice_assistant.state)
        image = image_generator.get_status()
        values = {
            "kernel": "Ready" if ready else "Offline",
            "model": "Loaded" if model_manager.is_loaded() else "Idle",
            "voice": voice_assistant.state.value.title(),
            "image": "Diffusion" if image.get("diffusion_loaded") else "Procedural available",
            "tools": str(len(tool_registry.get_all())),
        }
        for key, value in values.items():
            self._services[key].setText(value)
        percentages = (psutil.cpu_percent(), psutil.virtual_memory().percent, psutil.disk_usage(Path.home().anchor).percent)
        for (caption, meter), usage in zip(self._meters.values(), percentages):
            caption.setText(f"{usage:.0f}%")
            meter.setValue(round(usage))

        try:
            tasks = []
            for status in (TaskStatus.OVERDUE, TaskStatus.IN_PROGRESS, TaskStatus.PENDING):
                if len(tasks) < 4:
                    tasks += task_manager.list_tasks(limit=4 - len(tasks), status=status)
            self._objective_summary = [
                f"{task.title} · {task.status.value.replace('_', ' ').title()}" for task in tasks
            ] or ["No open tasks"]
            scheduled = task_manager.list_reminders(fired=False, upcoming_only=True)[:3]
            self._routine_summary = [
                f"{reminder.trigger_at:%d %b %H:%M}  {reminder.message}" for reminder in scheduled
            ] or ["No upcoming reminders"]
        except sqlite3.Error:
            self._objective_summary = ["Tasks unavailable"]
            self._routine_summary = ["Reminders unavailable"]
        self._set_rows(self._task_rows, self._objective_summary)
        self._set_rows(self._routine_rows, self._routine_summary)

    def set_voice_state(self, state, details=None):
        self.core.set_state(state)
        self._show_core_status(state)

    def _show_core_status(self, state):
        activity = self.core.ACTIVE.get(state)
        if activity:
            self.core_status.setText(f"●  {activity[0]}")
        else:
            self.core_status.setText("●  Kernel ready" if kernel._running else "○  Kernel offline")


class CommandShell(QWidget):
    def __init__(self, pages, start_voice, stop_voice, arm_voice, submit_prompt):
        super().__init__()
        self.setObjectName("commandShell")
        self.setStyleSheet(STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        rail = QFrame()
        rail.setObjectName("commandRail")
        rail.setFixedWidth(178)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(13, 18, 13, 14)
        rail_layout.setSpacing(5)
        brand = QHBoxLayout()
        mark = label("◈", "commandMark")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(34, 34)
        brand.addWidget(mark)
        brand.addWidget(label("ULTRON", "commandBrand"), 1)
        rail_layout.addLayout(brand)
        rail_layout.addSpacing(19)
        layout.addWidget(rail)

        content = QVBoxLayout()
        content.setSpacing(0)
        layout.addLayout(content, 1)
        header = QFrame()
        header.setObjectName("commandHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 10, 22, 10)
        self.page_name = label("", "commandPageName")
        header_layout.addWidget(self.page_name)
        header_layout.addStretch()
        self.date = label("", "commandMuted")
        self.clock = label("", "commandClock")
        header_layout.addWidget(self.date)
        header_layout.addSpacing(7)
        header_layout.addWidget(self.clock)
        content.addWidget(header)

        self.pages = QStackedWidget()
        self.pages.setObjectName("commandPages")
        self._buttons = {}
        group = QButtonGroup(self)
        for icon, name, page in pages:
            self.pages.addWidget(page)
            caption = "Overview" if name == "Command center" else name
            tab = button(f"{icon}   {caption}", "commandNav", lambda checked=False, current=name: self.open_view(current))
            tab.setAccessibleName(name)
            tab.setCheckable(True)
            group.addButton(tab)
            rail_layout.addWidget(tab)
            self._buttons[name] = (tab, page)
        rail_layout.addStretch()
        rail_layout.addWidget(label("Windows desktop", "commandMuted"))
        content.addWidget(self.pages, 1)

        dock = QFrame()
        dock.setObjectName("commandDock")
        dock_layout = QHBoxLayout(dock)
        dock_layout.setContentsMargins(22, 9, 22, 9)
        self.voice_status = label("Voice off", "commandText")
        dock_layout.addWidget(self.voice_status)
        self.prompt = QLineEdit()
        self.prompt.setObjectName("commandPrompt")
        self.prompt.setPlaceholderText("Ask ULTRON or run a command")
        dock_layout.addWidget(self.prompt, 1)

        def send_prompt():
            text = self.prompt.text().strip()
            if text and submit_prompt(text):
                self.prompt.clear()

        self.prompt.returnPressed.connect(send_prompt)
        dock_layout.addWidget(button("Send", "commandAction", send_prompt))
        dock_layout.addWidget(button("Arm “Hey ULTRON”", "commandAction", arm_voice))
        talk = QPushButton("Hold to speak")
        talk.setObjectName("commandTalk")
        talk.setToolTip("Hold to record, then release to transcribe")
        talk.setCursor(Qt.PointingHandCursor)
        self._dock_recording = False

        def press():
            self._dock_recording = start_voice()

        def release():
            if self._dock_recording:
                self._dock_recording = False
                stop_voice()

        talk.pressed.connect(press)
        talk.released.connect(release)
        dock_layout.addWidget(talk)
        content.addWidget(dock)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()
        self.open_view(pages[0][1])

    def open_view(self, name):
        tab, page = self._buttons[name]
        self.pages.setCurrentWidget(page)
        tab.setChecked(True)
        self.page_name.setText("Overview" if name == "Command center" else name)
        if isinstance(page, CommandCenter):
            page.refresh()

    def _update_clock(self):
        now = datetime.now()
        self.date.setText(now.strftime("%a, %d %b %Y"))
        self.clock.setText(now.strftime("%H:%M:%S"))
