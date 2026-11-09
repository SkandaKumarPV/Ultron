#!/usr/bin/env python3
"""
ULTRON Control Center - Unified GUI
One icon. One interface. All features.

Tabs: Chat | Voice | Automation | Monitor | Permissions | Modules | Settings
"""
import sys
import os
import json
import time
import html
import asyncio
import threading
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

# Qt
try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QTextEdit, QLineEdit, QComboBox, QCheckBox,
        QScrollArea, QFrame, QMenuBar, QMenu, QStatusBar, QSystemTrayIcon,
        QPlainTextEdit, QGroupBox, QSpinBox, QListWidget, QListWidgetItem,
        QTableWidget, QTableWidgetItem, QHeaderView, QTextBrowser, QSlider,
        QSplitter, QFileDialog, QMessageBox, QSizePolicy, QFormLayout,
        QInputDialog, QStyle, QStyleFactory, QProgressBar, QProgressDialog,
        QRadioButton, QButtonGroup, QSpacerItem
    )
    from PySide6.QtCore import Qt, QTimer, Signal, QThread, QObject, QSize, QEvent
    from PySide6.QtGui import QFont, QColor, QAction, QKeySequence, QIcon, QShortcut
    HAS_QT = True
except ImportError:
    try:
        from PyQt5.QtWidgets import (
            QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
            QLabel, QPushButton, QTextEdit, QLineEdit, QComboBox, QCheckBox,
            QScrollArea, QFrame, QMenuBar, QMenu, QStatusBar, QSystemTrayIcon,
            QPlainTextEdit, QGroupBox, QSpinBox, QListWidget, QListWidgetItem,
            QTableWidget, QTableWidgetItem, QHeaderView, QTextBrowser, QSlider,
            QSplitter, QFileDialog, QMessageBox, QSizePolicy, QFormLayout,
            QInputDialog, QStyle, QProgressBar, QProgressDialog, QRadioButton,
            QButtonGroup, QSpacerItem, QShortcut
        )
        from PyQt5.QtCore import Qt, QTimer, Signal, QThread, QObject, QSize, QEvent
        from PyQt5.QtGui import QFont, QColor, QAction, QKeySequence, QIcon
        HAS_QT = True
    except ImportError:
        HAS_QT = False

# ULTRON core
from ultron.config import config
from ultron.kernel import kernel
from ultron.model.loader import model_manager
from ultron.workspace.selector import workspace_manager
from ultron.tools.registry import tool_registry

# Voice assistant (Alexa-style): wake word + STT + TTS + PC control
from ultron.voice.assistant import voice_assistant, AssistantState
from ultron.voice.wake_word import wake_word_detector, MIC_BLOCK_MESSAGE

# Frontend dashboard (embedded web view)
try:
    from ultron.gui.home_tab import HomeTab
    HAS_HOME_TAB = True
except ImportError:
    HAS_HOME_TAB = False
    HomeTab = None

DARK = """
QMainWindow, QWidget { background: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI'; font-size: 10pt; }
QTabWidget::pane { border: 1px solid #313244; background: #1e1e2e; }
QTabBar::tab { background: #313244; color: #a6adc8; padding: 10px 18px; margin-right: 2px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background: #1e1e2e; color: #cdd6f4; border-bottom: 2px solid #89b4fa; }
QPushButton { background: #313244; color: #cdd6f4; border: 1px solid #45475a; padding: 8px 14px; border-radius: 6px; }
QPushButton:hover { background: #45475a; border-color: #89b4fa; }
QPushButton:pressed { background: #585b70; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTextBrowser { background: #181825; color: #cdd6f4; border: 1px solid #313244; padding: 6px; border-radius: 4px; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #89b4fa; }
QListWidget, QTableWidget { background: #181825; color: #cdd6f4; border: 1px solid #313244; alternate-background-color: #1e1e2e; }
QHeaderView::section { background: #313244; color: #cdd6f4; padding: 6px; border: 1px solid #45475a; }
QGroupBox { border: 1px solid #313244; border-radius: 6px; margin-top: 12px; padding-top: 8px; font-weight: 600; }
QGroupBox::title { color: #89b4fa; left: 10px; padding: 0 4px; }
QStatusBar { background: #181825; color: #a6adc8; }
QMenuBar { background: #181825; color: #cdd6f4; }
QMenu { background: #1e1e2e; color: #cdd6f4; border: 1px solid #313244; }
QMenu::item:selected { background: #313244; }
QScrollBar:vertical { background: #181825; width: 10px; }
QScrollBar::handle:vertical { background: #585b70; border-radius: 5px; min-height: 30px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #45475a; border-radius: 3px; }
QCheckBox::indicator:checked { background: #89b4fa; }
"""

# ==================================================================
# WORKERS
# ==================================================================

class WorkerThread(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            if asyncio.iscoroutinefunction(self.fn):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                res = loop.run_until_complete(self.fn(*self.args, **self.kwargs))
            else:
                res = self.fn(*self.args, **self.kwargs)
            self.result.emit(res)
        except Exception as e:
            import traceback
            self.error.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ==================================================================
# PERMISSIONS MANAGER (GUI-driven, stored in config)
# ==================================================================

PERM_FILE = Path(__file__).parent / ".ultron" / "config" / "permissions.json"

APPS = [
    ("notepad.exe", "Notepad"),
    ("code.exe", "VS Code"),
    ("chrome.exe", "Google Chrome"),
    ("explorer.exe", "File Explorer"),
    ("cmd.exe", "Command Prompt"),
    ("powershell.exe", "PowerShell"),
    ("wt.exe", "Windows Terminal"),
    ("excel.xlwaddin", "Excel"),
    ("python.exe", "Python"),
]

FOLDERS = [
    "Documents",
    "Desktop",
    "Downloads",
    "Pictures",
    "Music",
    "Videos",
]


def load_perms():
    if PERM_FILE.exists():
        try:
            return json.loads(PERM_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"apps": {}, "folders": {}, "features": {}}


def save_perms(data):
    PERM_FILE.parent.mkdir(parents=True, exist_ok=True)
    PERM_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ==================================================================
# TAB: HOME (master power switch + system status)
# ==================================================================

class VoiceAssistantPanel(QWidget):
    """Alexa-style voice assistant: state ring, transcript and reply."""

    STATE_TEXT = {
        AssistantState.OFF: ("🔴 Off", "#f38ba8", "Assistant engine is off"),
        AssistantState.DORMANT: ("🟢 On — idle", "#a6e3a1", "Ready. Say 'Hey ULTRON' or press Alt+`"),
        AssistantState.ARMED: ("🎤 Listening...", "#fab387", "Waiting for the wake word 'ULTRON'"),
        AssistantState.CAPTURING: ("🎙 Recording command", "#f9e2af", "Speak your command now"),
        AssistantState.THINKING: ("🟡 Thinking...", "#f9e2af", "Running your command"),
        AssistantState.SPEAKING: ("🔊 Speaking", "#89b4fa", "Speaking the reply"),
        AssistantState.ERROR: ("⛔ Error", "#f38ba8", "Something went wrong"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # big state button
        self.power_btn = QPushButton("🎤")
        self.power_btn.setFixedSize(132, 132)
        self.power_btn.setStyleSheet(
            "font-size: 42pt; border-radius: 66px; background: #313244; border: 2px solid #45475a;")
        self.power_btn.clicked.connect(self.toggle_power)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.power_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.state_lbl = QLabel("🔴 Off")
        self.state_lbl.setStyleSheet("font-size: 14pt; font-weight: 700; color: #f38ba8;")
        self.state_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.state_lbl)

        self.hint_lbl = QLabel("Assistant engine is off")
        self.hint_lbl.setStyleSheet("color: #a6adc8;")
        self.hint_lbl.setAlignment(Qt.AlignCenter)
        self.hint_lbl.setWordWrap(True)
        lay.addWidget(self.hint_lbl)

        self.listen_btn = QPushButton("▶ Start Voice Assistant")
        self.listen_btn.clicked.connect(self.toggle_power)
        lay.addWidget(self.listen_btn)

        # transcript / reply log
        self.log = QTextBrowser()
        self.log.setStyleSheet("background: #181825; border: 1px solid #313244; padding: 8px;")
        self.log.setMaximumHeight(150)
        lay.addWidget(self.log)

        # hook the assistant callbacks
        voice_assistant.on_state_change = self._on_state
        voice_assistant.on_transcript = self._on_transcript
        voice_assistant.on_response = self._on_response
        self._sync_state()

    # ---------------- power ----------------
    def toggle_power(self):
        if voice_assistant.enabled:
            voice_assistant.stop()
        else:
            ok = voice_assistant.start()
            if not ok:
                QMessageBox.warning(self, "Voice engine failed",
                                    (voice_assistant.error or "unknown")[:400])
        self._sync_state()

    def arm_listen(self):
        """Alt+` pressed: arm the wake word (auto-starts the engine)."""
        if not voice_assistant.enabled:
            if not voice_assistant.start():
                self._show_mic_block(voice_assistant.error)
                self._sync_state()
                return
        voice_assistant.arm()
        self._sync_state()

    # ---------------- state ----------------
    def _on_state(self, state: AssistantState, data: dict):
        self._sync_state()
        if state == AssistantState.ERROR:
            self._show_mic_block(data.get("error") or voice_assistant.error)

    def _on_transcript(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.log.append(f'<p style="color:#89b4fa;margin:0;"><b>You</b> <span style="color:#6c7086">({ts})</span><br>{html.escape(text)}</p>')

    def _on_response(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.log.append(f'<p style="color:#a6e3a1;margin:0 0 10px 0;"><b>ULTRON</b> <span style="color:#6c7086">({ts})</span><br>{html.escape(text)}</p>')

    def _sync_state(self):
        st = voice_assistant.state
        icon, color, hint = self.STATE_TEXT.get(st, ("⚪", "#a6adc8", ""))
        self.state_lbl.setText(icon)
        self.state_lbl.setStyleSheet(f"font-size: 14pt; font-weight: 700; color: {color};")
        self.hint_lbl.setText(hint)
        if st == AssistantState.OFF:
            self.power_btn.setText("🎤")
            self.power_btn.setStyleSheet(
                "font-size: 42pt; border-radius: 66px; background: #313244; border: 2px solid #45475a;")
            self.listen_btn.setText("▶ Start Voice Assistant")
        else:
            self.power_btn.setText("⏹")
            self.power_btn.setStyleSheet(
                f"font-size: 30pt; border-radius: 66px; background: {color}; border: 2px solid {color}; color: #1e1e2e;")
            self.listen_btn.setText("⏹ Stop Voice Assistant")

    def _show_mic_block(self, detail: str = ""):
        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Warning)
        mb.setWindowTitle("Microphone blocked")
        mb.setText("Windows is blocking microphone access.\n\n"
                   "Settings > Privacy & security > Microphone > "
                   "'Let desktop apps access the microphone'.\n\n" + (detail or ""))
        b = mb.addButton("Open Windows Mic Settings", QMessageBox.AcceptRole)
        mb.addButton("Close", QMessageBox.RejectRole)
        mb.exec()
        if mb.clickedButton() is b:
            wake_word_detector.open_windows_mic_settings()


class HomeTab(QWidget):
    """ULTRON home: one master switch, live system status, voice assistant."""

    def __init__(self, on_master_change=None):
        super().__init__()
        self.on_master_change = on_master_change
        self._master_on = False
        self._build()
        QTimer.singleShot(400, self._refresh_status)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(18)

        # header
        hdr = QHBoxLayout()
        logo = QLabel("🔴")
        logo.setStyleSheet("font-size: 26pt;")
        hdr.addWidget(logo)
        title = QLabel("ULTRON")
        title.setStyleSheet("font-size: 24pt; font-weight: 800; color: #89b4fa;")
        hdr.addWidget(title)
        hdr.addStretch()
        self.master_lbl = QLabel("SYSTEM OFF")
        self.master_lbl.setStyleSheet("font-size: 11pt; font-weight: 700; color: #f38ba8;")
        hdr.addWidget(self.master_lbl)
        lay.addLayout(hdr)

        # master power button
        self.master_btn = QPushButton("⏻   TURN ULTRON ON")
        self.master_btn.setMinimumHeight(78)
        self.master_btn.setStyleSheet(
            "font-size: 17pt; font-weight: 800; background: #313244; border: 2px solid #45475a; border-radius: 10px;")
        self.master_btn.clicked.connect(self._toggle_master)
        lay.addWidget(self.master_btn)

        hint = QLabel("One switch starts everything: kernel, local LLM, voice assistant "
                      "(mic + wake word 'Hey ULTRON'), image generation and all tools. "
                      "Press Alt+` anywhere to arm voice listening.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #a6adc8;")
        lay.addWidget(hint)

        # status cards
        cards = QHBoxLayout()
        self.cards = {}
        for key, label, icon in [
            ("kernel", "Kernel", "🧠"),
            ("model", "LLM Model", "💾"),
            ("voice", "Voice", "🎤"),
            ("image", "Image", "🎨"),
            ("tools", "Tools", "🛠"),
        ]:
            c = QGroupBox("")
            cl = QVBoxLayout(c)
            cl.setContentsMargins(10, 10, 10, 10)
            top = QLabel(f"{icon} {label}")
            top.setStyleSheet("font-weight: 700;")
            val = QLabel("…")
            val.setStyleSheet("color: #a6adc8;")
            cl.addWidget(top)
            cl.addWidget(val)
            cards.addWidget(c)
            self.cards[key] = val
        lay.addLayout(cards)

        # voice assistant panel
        vg = QGroupBox("🎤 Voice Assistant (Alt+`)")
        vl = QVBoxLayout(vg)
        self.voice_panel = VoiceAssistantPanel(self)
        vl.addWidget(self.voice_panel)
        lay.addWidget(vg)

        lay.addStretch()

    # ---------------- master switch ----------------
    def _toggle_master(self):
        if self._master_on:
            self._master_off()
        else:
            self._master_on_seq()

    def _master_on_seq(self):
        self._master_on = True
        self.master_btn.setText("⏻   ULTRON IS RUNNING — click to turn off")
        self.master_btn.setStyleSheet(
            "font-size: 15pt; font-weight: 800; background: #a6e3a1; color: #11111b;"
            "border: 2px solid #a6e3a1; border-radius: 10px;")
        self.master_lbl.setText("SYSTEM ON")
        self.master_lbl.setStyleSheet("font-size: 11pt; font-weight: 700; color: #a6e3a1;")

        # 1) kernel
        def work():
            kernel.initialize()
            model_manager.initialize()
            model_manager.load()
            from ultron.voice.assistant import voice_assistant
            voice_assistant.start()
            from ultron.image import image_generator
            image_generator.initialize()
            return True

        w = WorkerThread(work)
        w.result.connect(lambda _: self._refresh_status())
        w.error.connect(lambda e: QMessageBox.warning(self, "Startup issue", e[:300]))
        w.start()
        self._master_thread = w

        if self.on_master_change:
            try:
                self.on_master_change(True)
            except Exception:
                pass
        # 2) start the voice assistant engine (earns its "alexa-like" behaviour)
        QTimer.singleShot(100, self.voice_panel.toggle_power)

    def _master_off(self):
        self._master_on = False
        self.master_btn.setText("⏻   TURN ULTRON ON")
        self.master_btn.setStyleSheet(
            "font-size: 17pt; font-weight: 800; background: #313244; border: 2px solid #45475a; border-radius: 10px;")
        self.master_lbl.setText("SYSTEM OFF")
        self.master_lbl.setStyleSheet("font-size: 11pt; font-weight: 700; color: #f38ba8;")

        def work():
            try:
                voice_assistant.stop()
            except Exception:
                pass
            try:
                kernel.shutdown()
            except Exception:
                pass
            return True

        w = WorkerThread(work)
        w.result.connect(lambda _: self._refresh_status())
        w.start()
        self._master_thread = w

        if self.on_master_change:
            try:
                self.on_master_change(False)
            except Exception:
                pass

    def _refresh_status(self):
        def val(key, text, color="#a6adc8"):
            w = self.cards.get(key)
            if w:
                w.setText(text)
                w.setStyleSheet(f"color: {color};")

        if getattr(kernel, "_running", False):
            val("kernel", "ready ✅", "#a6e3a1")
        else:
            val("kernel", "off", "#f38ba8")
        try:
            if model_manager.is_loaded():
                val("model", "loaded ✅", "#a6e3a1")
            else:
                val("model", "not loaded", "#f9e2af")
        except Exception:
            val("model", "not loaded", "#f9e2af")

        if voice_assistant.enabled:
            val("voice", f"on ✅ ({voice_assistant.state.value})", "#a6e3a1")
        else:
            val("voice", "off", "#f38ba8")

        try:
            from ultron.image import image_generator
            st = image_generator.get_status()
            if st.get("diffusion_loaded"):
                val("image", "diffusion ✅", "#a6e3a1")
            else:
                val("image", "procedural ✅", "#a6e3a1")
        except Exception:
            val("image", "unknown", "#f9e2af")

        try:
            n = len(tool_registry.get_all())
            val("tools", f"{n} tools", "#a6e3a1")
        except Exception:
            val("tools", "unknown", "#f9e2af")


# ==================================================================
# TAB 1: CHAT
# ==================================================================

class ChatTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()
        self._model_ready = False

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        ttl = QLabel("💬 ULTRON Chat")
        ttl.setStyleSheet("font-size: 15pt; font-weight: 700;")
        header.addWidget(ttl)
        header.addStretch()
        self.ws_label = QLabel("Workspace: none")
        self.ws_label.setStyleSheet("color: #a6adc8;")
        header.addWidget(self.ws_label)
        lay.addLayout(header)

        # pick workspace
        ws_row = QHBoxLayout()
        ws_row.addWidget(QLabel("Folder:"))
        self.ws_path = QLineEdit()
        self.ws_path.setPlaceholderText("Select a folder to ask questions about...")
        ws_row.addWidget(self.ws_path)
        btn = QPushButton("Browse")
        btn.clicked.connect(self._browse_ws)
        ws_row.addWidget(btn)
        load_btn = QPushButton("Load & Index")
        load_btn.clicked.connect(self._load_ws)
        ws_row.addWidget(load_btn)
        lay.addLayout(ws_row)
        self.use_workspace = QCheckBox("Use selected folder context for chat")
        self.use_workspace.setEnabled(False)
        self.use_workspace.setToolTip(
            "Answer questions using indexed files from the selected folder."
        )
        lay.addWidget(self.use_workspace)

        # messages
        self.messages = QTextBrowser()
        self.messages.setOpenExternalLinks(True)
        self.messages.setStyleSheet("background: #181825; border: 1px solid #313244; padding: 10px;")
        lay.addWidget(self.messages, 1)
        self._append("assistant", "Hi! I'm ULTRON. Ask me anything about your workspace files, or type a command like 'open notepad'.")

        # input
        ir = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask about your files, or give a command... (Enter=send, Shift+Enter=newline)")
        self.input.setFixedHeight(70)
        self.input.installEventFilter(self)
        ir.addWidget(self.input, 1)
        send = QPushButton("Send")
        send.setFixedSize(70, 70)
        send.clicked.connect(self._send)
        ir.addWidget(send)
        lay.addLayout(ir)

        self.status = QLabel("Model loads when you send the first request.")
        lay.addWidget(self.status)

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == ev.Type.KeyPress:
            if ev.key() == Qt.Key.Key_Return and not (ev.modifiers() & Qt.ShiftModifier):
                self._send()
                return True
        return super().eventFilter(obj, ev)

    def _browse_ws(self):
        d = QFileDialog.getExistingDirectory(self, "Select workspace folder")
        if d:
            self.ws_path.setText(d)

    def _load_ws(self):
        p = self.ws_path.text().strip()
        if not p:
            QMessageBox.warning(self, "No folder", "Pick a folder first.")
            return
        self.status.setText("Indexing workspace...")
        def work():
            ws = workspace_manager.create_workspace("ws", p, read_only=True)
            workspace_manager.select_workspace(ws.workspace_id)
            from ultron.workspace.analyzer import workspace_analyzer
            return workspace_analyzer.index_workspace(ws.workspace_id)
        w = WorkerThread(work)
        def done(res):
            if not res.get("success"):
                self.use_workspace.setEnabled(False)
                self.status.setText("Indexing failed")
                QMessageBox.warning(
                    self,
                    "Indexing failed",
                    res.get("error", "The folder could not be indexed."),
                )
                return
            self.ws_label.setText(f"Workspace: {Path(p).name}")
            self.status.setText(f"Indexed: {res.get('chunks_indexed', 0)} chunks")
            self.use_workspace.setEnabled(True)
            self.use_workspace.setChecked(True)
            QTimer.singleShot(1500, lambda: self.status.setText("Ready."))
        w.result.connect(done)
        w.error.connect(lambda e: (self.status.setText("Error: " + e), QMessageBox.critical(self, "Index error", e[:500])))
        w.start()
        self._index_thread = w

    def _append(self, role, text):
        ts = time.strftime("%H:%M:%S")
        if role == "user":
            self.messages.append(f'<p style="color:#89b4fa;"><b>You</b> <span style="color:#6c7086">({ts})</span></p>')
        else:
            self.messages.append(f'<p style="color:#a6e3a1;"><b>ULTRON</b> <span style="color:#6c7086">({ts})</span></p>')
        self.messages.append(f'<p style="margin:0 0 12px 0;">{text.replace(chr(10), "<br>")}</p>')

    def _send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.submit_external_text(text)

    def submit_external_text(self, text: str):
        """Handle text from the input box, voice, or wake word."""
        self._append("user", text)
        self.status.setText("Thinking...")
        self._run_request(text)

    def _run_request(self, text: str):
        if not kernel._running:
            self.status.setText("Starting ULTRON...")
            kernel.initialize(start_voice=False)

        # Image intent: "draw me ...", "generate an image of ...", "picture of ..."
        lowered = text.lower()
        img_triggers = ("draw", "generate an image", "generate image", "picture of",
                        "image of", "create an image", "make a picture", "render")
        if any(t in lowered for t in img_triggers):
            prompt = self._strip_image_trigger(text)
            self._generate_image(prompt)
            return

        def work():
            from ultron.kernel import UltronRequest, RequestType
            request_type = RequestType.TEXT_COMMAND
            if self.use_workspace.isChecked() and workspace_manager.get_current_workspace_id():
                request_type = RequestType.WORKSPACE_QUERY
            r = UltronRequest(
                request_id=f"req_{int(time.time()*1000)}",
                request_type=request_type,
                input_text=text,
                workspace_id=workspace_manager.get_current_workspace_id(),
            )
            return kernel.process(r)
        w = WorkerThread(work)
        def done(resp):
            self._append("assistant", resp.response_text)
            self.status.setText("Ready.")
        w.result.connect(done)
        w.error.connect(lambda e: (self._append("assistant", "Error: " + e), self.status.setText("Error")))
        w.start()
        self._gen_thread = w

    @staticmethod
    def _strip_image_trigger(text: str) -> str:
        import re
        t = text.strip()
        for pat in (r"^\s*(please\s+)?(draw|generate|create|make|render)\s+(me\s+)?"
                    r"(an?\s+)?(image|picture|photo|drawing|artwork|art)?\s*(of|for)?\s*",
                    r"^\s*(please\s+)?show\s+me\s+(an?\s+)?(image|picture)\s*(of|for)?\s*"):
            t = re.sub(pat, "", t, flags=re.IGNORECASE)
            if t != text.strip():
                break
        return t.strip(" .,-:!") or text

    def _generate_image(self, prompt: str):
        self.status.setText("🎨 Generating image...")

        def work():
            from ultron.image import image_generator
            return image_generator.generate(
                prompt=prompt, width=512, height=512, style="none", backend="auto",
            )

        w = WorkerThread(work)
        def done(res):
            if res.success and res.path:
                ts = time.strftime("%H:%M:%S")
                self.messages.append(
                    f'<p style="color:#a6e3a1;"><b>ULTRON</b> <span style="color:#6c7086">({ts})</span></p>')
                self.messages.append(
                    f'<p style="margin:0 0 6px 0;">🎨 {prompt} '
                    f'<span style="color:#6c7086;">({res.backend}, seed {res.seed}, '
                    f'{res.generation_time_ms/1000:.1f}s)</span></p>')
                p = Path(res.path).resolve().as_uri()
                self.messages.append(
                    f'<p style="margin:0 0 12px 0;">'
                    f'<img src="{p}" style="max-width:520px;border-radius:8px;border:1px solid #313244;">'
                    f'<br><a href="{p}" style="color:#89b4fa;">Open full size</a></p>')
                if res.error:
                    self.messages.append(f'<p style="margin:0 0 12px 0;color:#fab387;">⚠ {res.error}</p>')
                self.status.setText("Ready.")
            else:
                self._append("assistant", "Image generation failed: " + (res.error or "unknown"))
                self.status.setText("Ready.")
        w.result.connect(done)
        w.error.connect(lambda e: (self._append("assistant", "Error: " + e), self.status.setText("Error")))
        w.start()
        self._img_thread = w


# ==================================================================
# TAB: IMAGE GENERATION
# ==================================================================

class ImageTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()
        QTimer.singleShot(300, self._refresh_status)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        t = QLabel("🎨 Image Generation")
        t.setStyleSheet("font-size: 15pt; font-weight: 700;")
        lay.addWidget(t)

        self.backend_status = QLabel("Checking backend...")
        self.backend_status.setStyleSheet("color: #a6adc8;")
        lay.addWidget(self.backend_status)

        # prompt
        pg = QGroupBox("Prompt")
        pl = QVBoxLayout(pg)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Describe the image you want... e.g. 'a lone robot on a neon-lit rooftop'")
        self.prompt.setFixedHeight(72)
        pl.addWidget(self.prompt)
        lay.addWidget(pg)

        # options row
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Style:"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(["none", "photorealistic", "anime", "digital art",
                                   "oil painting", "watercolor", "cyberpunk", "sketch"])
        opts.addWidget(self.style_combo)
        opts.addWidget(QLabel("Backend:"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["auto", "procedural", "diffusion"])
        opts.addWidget(self.backend_combo)
        opts.addWidget(QLabel("Seed:"))
        self.seed_input = QLineEdit()
        self.seed_input.setPlaceholderText("blank = random")
        self.seed_input.setFixedWidth(110)
        opts.addWidget(self.seed_input)
        opts.addStretch()
        lay.addLayout(opts)

        # generate
        row = QHBoxLayout()
        self.gen_btn = QPushButton("✨ Generate")
        self.gen_btn.setStyleSheet(
            "background: #89b4fa; color: #1e1e2e; font-weight: 800; font-size: 12pt; border-radius: 6px;")
        self.gen_btn.setMinimumHeight(48)
        self.gen_btn.setCursor(Qt.PointingHandCursor)
        self.gen_btn.clicked.connect(self._generate)
        row.addWidget(self.gen_btn, 3)
        open_btn = QPushButton("📂 Open Images Folder")
        open_btn.setMinimumHeight(48)
        open_btn.clicked.connect(self._open_folder)
        row.addWidget(open_btn, 1)
        lay.addLayout(row)

        # busy progress (clearly shows a click happened)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        lay.addWidget(self.progress)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse)
        self._pulse_val = 0

        # preview + history
        split = QSplitter(Qt.Horizontal)
        self.preview = QLabel("Your image appears here")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background: #181825; border: 1px solid #313244; border-radius: 6px; color: #6c7086;")
        self.preview.setMinimumWidth(400)
        self.history = QListWidget()
        self.history.setMaximumWidth(340)
        self.history.itemDoubleClicked.connect(self._show_history_item)
        split.addWidget(self.preview)
        split.addWidget(self.history)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #a6adc8;")
        lay.addWidget(self.status)

    # ------------------------------------------------------------------
    def _get_seed(self) -> int:
        txt = self.seed_input.text().strip()
        try:
            return int(txt)
        except Exception:
            import random
            return random.randint(0, 2**31 - 1)

    def _generate(self):
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "Empty prompt", "Describe what you want to see.")
            return
        self._t0 = time.time()
        self._set_busy(True, "✨ Generating… (clicked)")
        self.status.setText("Generating... (using GPU when available)")

        style = self.style_combo.currentText()
        backend = self.backend_combo.currentText()
        seed = self._get_seed()

        def work():
            from ultron.image import image_generator
            return image_generator.generate(prompt=prompt, width=512, height=512,
                                            style=style, backend=backend, seed=seed)

        w = WorkerThread(work)
        def done(res):
            self._set_busy(False)
            if res.success and res.path:
                self._set_preview(res.path)
                self.status.setText(
                    f"✅ Done in {res.generation_time_ms/1000:.1f}s — backend: {res.backend}, seed {res.seed}"
                    + (f"  (⚠ fallback: {res.error})" if res.error else ""))
                self.history.insertItem(0, f"{prompt[:38]}  [{res.backend}]")
                self.history.item(0).setData(Qt.UserRole, res.path)
                self.gen_btn.setText("✅ Done — Generate again")
            else:
                self.status.setText("Failed: " + (res.error or "unknown error"))
                self._refresh_status()
        w.result.connect(done)
        w.error.connect(lambda e: (self._set_busy(False), self.status.setText("Error: " + e[:200])))
        w.start()
        self._gen_thread = w

    def _set_busy(self, busy: bool, text: str = ""):
        """Give unmistakable feedback that the click registered."""
        self.gen_btn.setEnabled(not busy)
        self.prompt.setEnabled(not busy)
        if busy:
            self.gen_btn.setText(text or "✨ Generating…")
            self.gen_btn.setStyleSheet(
                "background: #fab387; color: #1e1e2e; font-weight: 800; font-size: 12pt; border-radius: 6px;")
            self.progress.show()
            self.progress.setRange(0, 0)  # indeterminate
            self._pulse_val = 0
            self._pulse_timer.start(600)
        else:
            self.gen_btn.setText("✨ Generate")
            self.gen_btn.setStyleSheet(
                "background: #89b4fa; color: #1e1e2e; font-weight: 800; font-size: 12pt; border-radius: 6px;")
            self.progress.hide()
            self.progress.setRange(0, 1)
            self._pulse_timer.stop()

    def _pulse(self):
        base = "✨ Generating…"
        dots = int(self._pulse_val) % 4
        self._pulse_val += 1
        self.gen_btn.setText(base + "." * dots)
        if self.status.text().startswith("Generating"):
            import time as _t
            self.status.setText(
                f"Generating… ({int(_t.time() - self._t0)}s elapsed — GPU acceleration when available)")

    def _set_preview(self, path: str):
        try:
            from PySide6.QtGui import QPixmap
            pm = QPixmap(path)
            if pm.isNull():
                self.preview.setText("Could not render image")
                return
            self.preview.setPixmap(pm.scaled(560, 560, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self.preview.setText(path)

    def _show_history_item(self, item):
        path = item.data(Qt.UserRole)
        if path:
            self._set_preview(path)

    def _open_folder(self):
        try:
            from ultron.image import image_generator
            d = image_generator.open_output_folder()
            self.status.setText("Opened: " + d)
        except Exception as e:
            self.status.setText(str(e))

    def _refresh_status(self):
        try:
            from ultron.image import image_generator
            st = image_generator.get_status()
            if st.get("diffusion_loaded"):
                txt = (f"✅ Diffusion ready ({st.get('diffusion_model')}, "
                       f"{st.get('device', 'unknown')}) + procedural fallback")
                color = "#a6e3a1"
            elif st.get("diffusion_error"):
                txt = (f"⚙ Procedural active. Diffusion: {st.get('diffusion_error')[:90]} — "
                       "grant permission in Permissions tab or set backend to procedural")
                color = "#f9e2af"
            else:
                txt = "⚙ Procedural active. Diffusion model loads on first use."
                color = "#f9e2af"
            self.backend_status.setText(txt)
            self.backend_status.setStyleSheet(f"color: {color};")
        except Exception as e:
            self.backend_status.setText("Status error: " + str(e)[:100])


# ==================================================================
# TAB 2: VOICE
# ==================================================================

class VoiceTab(QWidget):
    def __init__(self, chat_tab=None):
        super().__init__()
        self.chat_tab = chat_tab
        self._wake = None
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(14)

        title_lbl = QLabel("🎤 Voice Assistant")
        title_lbl.setStyleSheet("font-size: 15pt; font-weight: 700;")
        lay.addWidget(title_lbl)

        st = QGroupBox("Status")
        stl = QVBoxLayout(st)
        self.status_lbl = QLabel("🔴 not initialized")
        self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #a6adc8;")
        stl.addWidget(self.status_lbl)
        init_btn = QPushButton("▶ Start Voice Engine")
        init_btn.clicked.connect(self._initialize_voice)
        stl.addWidget(init_btn)
        diag_btn = QPushButton("🔍 Diagnose Microphone (Windows permission)")
        diag_btn.clicked.connect(self._diagnose_mic)
        stl.addWidget(diag_btn)
        lay.addWidget(st)

        ctrl = QGroupBox("Controls")
        cl = QVBoxLayout(ctrl)
        self.ptt = QPushButton("🎤 Hold to Speak")
        self.ptt.setMinimumHeight(60)
        self.ptt.setStyleSheet("font-size: 13pt; font-weight: 700; background: #fab387; border-radius: 8px;")
        self.ptt.pressed.connect(self._ptt_on)
        self.ptt.released.connect(self._ptt_off)
        cl.addWidget(self.ptt)

        self.wake_check = QCheckBox("Always listen for 'Hey ULTRON' (hands-free)")
        self.wake_check.setEnabled(False)
        self.wake_check.toggled.connect(self._toggle_wake)
        cl.addWidget(self.wake_check)
        lay.addWidget(ctrl)

        sets = QGroupBox("Voice Settings")
        sl = QFormLayout(sets)
        self.tts_voice = QComboBox()
        self.tts_voice.addItems(["en_US-lessac-medium", "en_US-ryan-high", "System David"])
        sl.addRow("TTS Voice:", self.tts_voice)
        self.stt_model = QComboBox()
        self.stt_model.addItems(["base.en (fast)", "tiny.en (faster)"])
        sl.addRow("STT Model:", self.stt_model)
        self.speak_replies = QCheckBox("Speak replies out loud")
        self.speak_replies.setChecked(True)
        sl.addRow("", self.speak_replies)
        lay.addWidget(sets)

        test = QGroupBox("Test")
        tl = QVBoxLayout(test)
        self.test_input = QLineEdit()
        self.test_input.setPlaceholderText("Type text for ULTRON to speak...")
        tl.addWidget(self.test_input)
        b = QPushButton("🔊 Speak")
        b.clicked.connect(self._speak)
        tl.addWidget(b)
        lay.addWidget(test)

        lay.addStretch()

    # ---------------- init ----------------
    def _initialize_voice(self):
        self.status_lbl.setText("🟡 Loading STT + TTS...")
        self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f9e2af;")

        def work():
            from ultron.voice.pipeline import voice_pipeline
            voice_pipeline.initialize()
            voice_pipeline.start(self._on_voice_text)
            # wake word model (optional, loaded lazily)
            try:
                from ultron.voice.wake_word import wake_word_detector
                wake_word_detector.initialize()
            except Exception:
                pass
            return voice_pipeline.is_ready()

        w = WorkerThread(work)
        def done(ok):
            if ok:
                self.status_lbl.setText("🟢 Ready — hold 'Hold to Speak'")
                self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #a6e3a1;")
                self.wake_check.setEnabled(True)
            else:
                self.status_lbl.setText("🔴 STT model failed to load")
                self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f38ba8;")
        def err(e):
            self.status_lbl.setText("🔴 Error: " + e[:120])
            self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f38ba8;")
        w.result.connect(done)
        w.error.connect(err)
        w.start()
        self._init_thread = w

    def _on_voice_text(self, text: str):
        """Voice transcripts land in the Chat tab so everything is one system."""
        if self.chat_tab is not None:
            try:
                self.chat_tab.submit_external_text(text)
            except Exception:
                pass

    # ---------------- push to talk ----------------
    def _ptt_on(self):
        self.status_lbl.setText("🎤 Listening...")
        self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #fab387;")
        try:
            from ultron.voice.pipeline import voice_pipeline
            if not voice_pipeline.is_ready():
                QMessageBox.warning(self, "Voice not ready",
                                    "Start the Voice Engine first (top of this tab).")
                return
            voice_pipeline.push_to_talk_start()
        except Exception as e:
            QMessageBox.critical(self, "Mic error", str(e)[:300])

    def _ptt_off(self):
        self.status_lbl.setText("🟡 Transcribing...")
        self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f9e2af;")

        def work():
            from ultron.voice.pipeline import voice_pipeline
            return voice_pipeline.push_to_talk_stop()

        w = WorkerThread(work)
        def done(text):
            if text:
                self.status_lbl.setText("🟢 Heard: " + text[:60])
                self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #a6e3a1;")
                try:
                    from ultron.voice.assistant import voice_assistant
                    voice_assistant.start()
                    voice_assistant.handle_transcript(text, speak_reply=self.speak_replies.isChecked())
                except Exception as e:
                    self.status_lbl.setText("🔴 Command error: " + str(e)[:80])
            else:
                self.status_lbl.setText("🔴 Idle — nothing heard")
                self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #a6adc8;")
        def err(e):
            self.status_lbl.setText("🔴 " + e[:100])
            if "mic" in e.lower() or "input" in e.lower():
                self._show_mic_block()
        w.result.connect(done)
        w.error.connect(err)
        w.start()
        self._ptt_thread = w

    # ---------------- wake word ----------------
    def _toggle_wake(self, on: bool):
        from ultron.voice.wake_word import wake_word_detector
        if on:
            if not wake_word_detector.available and not wake_word_detector.initialize():
                self._show_mic_block(wake_word_detector.error)
                self.wake_check.setChecked(False)
                return
            def on_wake():
                self.status_lbl.setText("🟡 Wake word! Listening...")
                self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f9e2af;")
                try:
                    from ultron.voice.pipeline import voice_pipeline
                    voice_pipeline.push_to_talk_start()
                except Exception:
                    pass
            wake_word_detector.start(on_wake)
            self.status_lbl.setText("🟢 Hands-free: say 'Hey ULTRON'")
            self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #a6e3a1;")
        else:
            wake_word_detector.stop()
            self.status_lbl.setText("🔴 Hands-free off")

    # ---------------- diagnostics ----------------
    def _diagnose_mic(self):
        try:
            from ultron.voice.wake_word import wake_word_detector
            info = wake_word_detector.diagnose_microphone()
        except Exception as e:
            QMessageBox.critical(self, "Diagnostics failed", str(e)[:300])
            return
        devs = "\n".join(f"  • {d}" for d in info.get("devices", [])) or "  (none found)"
        msg = f"PyAudio available: {info.get('available')}\nInput devices:\n{devs}\n"
        if info.get("blocked"):
            msg += "\n⛔ " + info.get("message", "Access blocked")
            mb = QMessageBox(self)
            mb.setIcon(QMessageBox.Warning)
            mb.setWindowTitle("Microphone blocked by Windows")
            mb.setText(msg)
            open_btn = mb.addButton("Open Windows Mic Settings", QMessageBox.AcceptRole)
            mb.addButton("Close", QMessageBox.RejectRole)
            mb.exec()
            if mb.clickedButton() is open_btn:
                try:
                    from ultron.voice.wake_word import wake_word_detector
                    wake_word_detector.open_windows_mic_settings()
                except Exception:
                    pass
        else:
            QMessageBox.information(self, "Microphone OK", msg)

    def _show_mic_block(self, detail: str = ""):
        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Warning)
        mb.setWindowTitle("Microphone blocked")
        mb.setText("Windows is blocking microphone access.\n\n"
                   "Settings > Privacy & security > Microphone > "
                   "'Let desktop apps access the microphone'.\n\n"
                   + (detail or ""))
        b = mb.addButton("Open Windows Mic Settings", QMessageBox.AcceptRole)
        mb.addButton("Close", QMessageBox.RejectRole)
        mb.exec()
        if mb.clickedButton() is b:
            try:
                from ultron.voice.wake_word import wake_word_detector
                wake_word_detector.open_windows_mic_settings()
            except Exception:
                pass

    # ---------------- tts ----------------
    def _speak(self):
        txt = self.test_input.text().strip() or "ULTRON voice test"
        def work():
            from ultron.voice.pipeline import voice_pipeline
            return voice_pipeline.speak(txt)
        w = WorkerThread(work)
        w.error.connect(lambda e: QMessageBox.critical(self, "TTS error", e[:300]))
        w.start()
        self._speak_thread = w


# ==================================================================
# TAB 3: AUTOMATION
# ==================================================================

class AutomationTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        t = QLabel("🤖 Automation — workflows & schedules")
        t.setStyleSheet("font-size: 15pt; font-weight: 700;")
        lay.addWidget(t)

        # quick natural language
        ql = QGroupBox("Quick Command")
        qv = QVBoxLayout(ql)
        qv.addWidget(QLabel("Type naturally, e.g.: 'study schedule from today 5-6pm'"))
        row = QHBoxLayout()
        self.quick = QLineEdit()
        self.quick.setPlaceholderText("study schedule from today 5-6pm")
        row.addWidget(self.quick)
        run = QPushButton("Execute")
        run.clicked.connect(self._run_quick)
        row.addWidget(run)
        qv.addLayout(row)
        lay.addWidget(ql)

        examples = QGroupBox("Example Automations (non-calendar)")
        examples_layout = QVBoxLayout(examples)
        examples_layout.addWidget(QLabel(
            "Choose an example to load it into Quick Command, then review and execute it."
        ))
        example_buttons = [
            ("Create task", "Create a high priority task to review project documentation"),
            ("Set reminder", "Remind me in 30 minutes to check the build"),
            ("Run tests", "Run the tests in the current workspace"),
            ("Check Git", "Show the current Git status"),
            ("Analyze code", "Analyze the main Python files in the current workspace"),
            ("Explain file", "Explain what the selected workspace code file does"),
        ]
        examples_row = QHBoxLayout()
        for label, command in example_buttons:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=command: self._load_example(value))
            examples_row.addWidget(button)
        examples_layout.addLayout(examples_row)
        lay.addWidget(examples)

        # tasks list
        tl = QGroupBox("Tasks / Reminders")
        tv = QVBoxLayout(tl)
        self.tasks_table = QTableWidget(0, 3)
        self.tasks_table.setHorizontalHeaderLabels(["Task", "Priority", "Due"])
        self.tasks_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        tv.addWidget(self.tasks_table)
        brow = QHBoxLayout()
        add = QPushButton("+ Add Task")
        add.clicked.connect(self._add_task)
        brow.addWidget(add)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_tasks)
        brow.addWidget(refresh)
        tv.addLayout(brow)
        lay.addWidget(tl, 1)

        self._refresh_tasks()

    def _load_example(self, command):
        self.quick.setText(command)
        self.quick.setFocus()

    def _run_quick(self):
        text = self.quick.text().strip()
        if not text:
            return
        def work():
            from ultron.kernel import UltronRequest, RequestType
            r = UltronRequest(
                request_id=f"req_{int(time.time()*1000)}",
                request_type=RequestType.TEXT_COMMAND,
                input_text=text,
                workspace_id=workspace_manager.get_current_workspace_id(),
            )
            return kernel.process(r)
        w = WorkerThread(work)
        w.result.connect(lambda resp: QMessageBox.information(self, "Executed", resp.response_text[:300]))
        w.error.connect(lambda e: QMessageBox.critical(self, "Error", e[:300]))
        w.start()
        self._quick_thread = w

    def _refresh_tasks(self):
        try:
            from ultron.tasks.manager import task_manager
            tasks = task_manager.list_tasks(limit=50)
            self.tasks_table.setRowCount(len(tasks))
            for i, t in enumerate(tasks):
                self.tasks_table.setItem(i, 0, QTableWidgetItem(t.title))
                self.tasks_table.setItem(i, 1, QTableWidgetItem(t.priority.name.title()))
                due = t.due_date.strftime("%Y-%m-%d %H:%M") if t.due_date else "-"
                self.tasks_table.setItem(i, 2, QTableWidgetItem(due))
        except Exception:
            pass

    def _add_task(self):
        text, ok = QInputDialog.getText(self, "New Task", "Task description:")
        if ok and text:
            try:
                from ultron.tasks.manager import task_manager
                task_manager.create_task(title=text)
                self._refresh_tasks()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))


# ==================================================================
# TAB 4: MONITOR (kept light — core feeds, resilient to blocked deps)
# ==================================================================

class MonitorTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        mt = QLabel("🌍 Super Monitor — global intelligence")
        mt.setStyleSheet("font-size: 15pt; font-weight: 700;")
        lay.addWidget(mt)
        self.status = QLabel("Ready. Fetch live news/environment below.")
        self.status.setStyleSheet("color: #a6adc8;")
        lay.addWidget(self.status)

        btns = QHBoxLayout()
        for label, fn in [
            ("📰 Refresh News", self._news),
            ("⚠️ Disasters (USGS)", self._disasters),
            ("🌤 Weather", self._weather),
        ]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            btns.addWidget(b)
        lay.addLayout(btns)

        self.output = QTextBrowser()
        self.output.setStyleSheet("background: #181825; border: 1px solid #313244; padding: 8px;")
        lay.addWidget(self.output, 1)

    def _news(self):
        self.status.setText("Fetching live headlines from multiple sources...")
        def work():
            from ultron.super_monitor.news_aggregator import NewsAggregator
            sources = [
                "https://feeds.bbci.co.uk/news/world/rss.xml",
                "https://feeds.bbci.co.uk/news/technology/rss.xml",
                "https://feeds.bbci.co.uk/news/business/rss.xml",
                "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
                "https://feeds.apnews.com/rss/apf-topnews",
                "https://feeds.npr.org/1001/rss.xml",
                "https://www.theguardian.com/world/rss",
                "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            ]
            agg = NewsAggregator(sources, max_events_per_source=20)
            evs = agg.fetch_all()
            if asyncio.iscoroutine(evs):
                evs = asyncio.run(evs)
            return evs
        w = WorkerThread(work)
        w.result.connect(self._render_news)
        w.error.connect(lambda e: self.status.setText("News error: " + e[:150]))
        w.start()
        self._n = w

    def _render_news(self, evs):
        evs = sorted(evs, key=lambda event: event.published, reverse=True)
        parts = [
            "<h3 style='color:#89b4fa'>Live News Headlines</h3>",
            "<p style='color:#a6adc8'>Updated just now from public RSS feeds.</p>",
        ]
        if not evs:
            parts.append("<i>No headlines were returned. Try Refresh News again.</i>")
        else:
            parts.append("<ol>")
            for event in evs[:30]:
                title = html.escape(getattr(event, "title", str(event)))
                summary = html.escape(getattr(event, "summary", "")[:240])
                source = html.escape(getattr(event, "source", "Unknown source"))
                url = html.escape(getattr(event, "url", "#"), quote=True)
                published = getattr(event, "published", None)
                timestamp = published.strftime("%Y-%m-%d %H:%M UTC") if published else "Time unavailable"
                parts.append(
                    f"<li style='margin-bottom:10px'>"
                    f"<a href='{url}' style='color:#cdd6f4'><b>{title}</b></a><br>"
                    f"<small style='color:#89b4fa'>{source} | {timestamp}</small><br>"
                    f"<span style='color:#a6adc8'>{summary}</span></li>"
                )
            parts.append("</ol>")
        self.output.setHtml("".join(parts))
        self.status.setText(f"Loaded {len(evs)} live headlines")

    def _disasters(self):
        self.status.setText("Fetching earthquake data (USGS)...")
        def work():
            import requests
            r = requests.get(
                "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.geojson",
                timeout=20,
            )
            return r.json().get("features", [])
        w = WorkerThread(work)
        w.result.connect(self._render_eq)
        w.error.connect(lambda e: self.status.setText("Quake error: " + e[:150]))
        w.start()
        self._q = w

    def _render_eq(self, feats):
        if not feats:
            self.output.setHtml("<i>No significant earthquakes in the last hour.</i>")
            self.status.setText("No significant events")
            return
        html = "<h3 style='color:#fab387'>⚠️ Significant Earthquakes (last hour)</h3><ul>"
        for f in feats[:15]:
            p = f["properties"]
            html += f"<li>M{p['mag']} — {p['place']} <span style='color:#6c7086'>({datetime.fromtimestamp(p['time']/1000):%H:%M UTC})</span></li>"
        html += "</ul>"
        self.output.setHtml(html)
        self.status.setText(f"{len(feats)} significant events")

    def _weather(self):
        lat, ok1 = QInputDialog.getDouble(self, "Latitude", "Lat:", 40.7128)
        lon, ok2 = QInputDialog.getDouble(self, "Longitude", "Lon:", -74.0060)
        if not (ok1 and ok2):
            return
        self.status.setText("Fetching weather (NOAA/NWS)...")
        def work():
            import requests
            pts = requests.get(f"https://api.weather.gov/points/{lat},{lon}", timeout=20).json()
            fc = requests.get(pts["properties"]["forecast"], timeout=20).json()
            return fc["properties"]["periods"][:6]
        w = WorkerThread(work)
        w.result.connect(self._render_weather)
        w.error.connect(lambda e: self.status.setText("Weather error: " + e[:150]))
        w.start()
        self._w = w

    def _render_weather(self, periods):
        html = "<h3 style='color:#89b4fa'>🌤 NOAA/NWS Forecast</h3>"
        for p in periods:
            html += f"<p><b>{p['name']}</b>: {p['temperature']}°{p['temperatureUnit']} — {p['shortForecast']}</p>"
        self.output.setHtml(html)
        self.status.setText("Forecast loaded")


# ==================================================================
# TAB 5: PERMISSIONS (GUI-driven — user grants here, not Windows)
# ==================================================================

class PermissionsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        pt = QLabel("🔐 Permissions — grant ULTRON access to apps & folders")
        pt.setStyleSheet("font-size: 15pt; font-weight: 700;")
        lay.addWidget(pt)

        info = QLabel("Windows blocks unsigned apps by default. Instead of bypassing Windows, grant ULTRON permission here. Nothing runs until you allow it.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #a6adc8;")
        lay.addWidget(info)

        # apps
        ag = QGroupBox("Applications ULTRON may open")
        av = QVBoxLayout(ag)
        self.app_checks = {}
        perms = load_perms()
        for exe, friendly in APPS:
            row = QHBoxLayout()
            c = QCheckBox(friendly)
            c.setChecked(not perms["apps"].get(exe, False))  # default denied
            self.app_checks[exe] = c
            row.addWidget(c)
            row.addStretch()
            av.addLayout(row)
        lay.addWidget(ag, 1)

        # folders
        fg = QGroupBox("Folders ULTRON may access")
        fv = QVBoxLayout(fg)
        self.folder_checks = {}
        import os
        root = Path.home()
        for f in FOLDERS:
            row = QHBoxLayout()
            c = QCheckBox(f)
            c.setChecked(bool(perms["folders"].get(f, False)))
            self.folder_checks[f] = c
            row.addWidget(c)
            row.addStretch()
            fv.addLayout(row)
        lay.addWidget(fg, 1)

        # special: geopandas / spatial
        sg = QGroupBox("Optional Features (may need Windows permission)")
        sv = QVBoxLayout(sg)
        self.feat_checks = {}
        for key, label in [
            ("spatial", "Spatial layers / maps (geopandas)"),
            ("speech", "Microphone / speech recognition"),
            ("news", "News feed fetching"),
        ]:
            c = QCheckBox(label)
            c.setChecked(bool(perms["features"].get(key, key == "news")))
            self.feat_checks[key] = c
            sv.addWidget(c)
        lay.addWidget(sg)

        save_btn = QPushButton("💾 Save Permissions")
        save_btn.setStyleSheet("background: #a6e3a1; font-weight: 700;")
        save_btn.clicked.connect(self._save)
        lay.addWidget(save_btn)

    def _save(self):
        data = {
            "apps": {e: c.isChecked() for e, c in self.app_checks.items()},
            "folders": {f: c.isChecked() for f, c in self.folder_checks.items()},
            "features": {k: c.isChecked() for k, c in self.feat_checks.items()},
        }
        save_perms(data)
        QMessageBox.information(self, "Saved", "Permissions saved. They take effect on next command.")


# ==================================================================
# TAB 6: MODULES
# ==================================================================

class ModulesTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        ft = QLabel("🔧 Feature Modules — turn capabilities on/off")
        ft.setStyleSheet("font-size: 15pt; font-weight: 700;")
        lay.addWidget(ft)

        perms = load_perms()
        groups = {
            "Core": [
                ("voice", "🎤 Voice Assistant (TTS/STT, wake word)"),
                ("workspace_ai", "💬 Workspace AI (RAG, search, explain)"),
                ("pc_control", "🖥 PC Control (open apps, files)"),
                ("tasks", "📋 Tasks, reminders, schedules"),
            ],
            "Super Monitor": [
                ("news_monitor", "📰 News aggregation"),
                ("weather_monitor", "🌤 Weather / NOAA"),
                ("disaster_monitor", "⚠️ Disasters / USGS"),
                ("spatial_layers", "🗺 Spatial layers (needs geopandas)"),
                ("timeline", "📅 Event timeline"),
            ],
            "Advanced": [
                ("debug_assist", "🐛 Debug assistant"),
                ("code_gen", "✏ Code generation / docs"),
                ("security_scan", "🔒 Security scanner"),
                ("git", "🔧 Git integration"),
            ],
        }

        self.checks = {}
        for g, mods in groups.items():
            gb = QGroupBox(g)
            gl = QVBoxLayout(gb)
            for key, label in mods:
                c = QCheckBox(label)
                default_on = key not in ("spatial_layers",)
                c.setChecked(bool(perms.get("features", {}).get(key, default_on)))
                self.checks[key] = c
                gl.addWidget(c)
            lay.addWidget(gb)

        lay.addStretch()


# ==================================================================
# TAB 7: SETTINGS
# ==================================================================

class SettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        st_lbl = QLabel("⚙ Settings")
        st_lbl.setStyleSheet("font-size: 15pt; font-weight: 700;")
        lay.addWidget(st_lbl)

        f = QFormLayout()
        self.model = QComboBox()
        self.model.addItems(["qwen2.5-1.5b-instruct-q4_k_m (active)"])
        f.addRow("Active Model:", self.model)

        self.ctx = QSpinBox()
        self.ctx.setRange(1024, 32768)
        self.ctx.setValue(int(config.config.model.n_ctx))
        self.ctx.setSuffix(" tokens")
        f.addRow("Context:", self.ctx)

        self.threads = QSpinBox()
        self.threads.setRange(1, 16)
        self.threads.setValue(int(config.config.model.n_threads))
        f.addRow("CPU Threads:", self.threads)

        self.vram = QSpinBox()
        self.vram.setRange(512, 4000)
        self.vram.setValue(1024)
        f.addRow("GPU Layers (VRAM MB):", self.vram)
        lay.addLayout(f)

        paths = QGroupBox("Paths")
        pl = QFormLayout(paths)
        for label, val in [
            ("Data", str(config.resolve_path(""))),
            ("Models", str(config.resolve_path("models"))),
            ("Logs", str(config.resolve_path("logs"))),
        ]:
            pl.addRow(label, QLabel(val))
        lay.addWidget(paths)

        st = QGroupBox("Status")
        sl = QVBoxLayout(st)
        self.model_status = QLabel("Model: not loaded")
        sl.addWidget(self.model_status)
        load_btn = QPushButton("Load Model Now")
        load_btn.clicked.connect(self._load_model)
        sl.addWidget(load_btn)
        lay.addWidget(st)

        lay.addStretch()

        self._refresh_status()
        QTimer(self).singleShot(1000, self._refresh_status)

    def _refresh_status(self):
        if model_manager.is_loaded():
            self.model_status.setText("Model: loaded ✅")
            self.model_status.setStyleSheet("color: #a6e3a1;")
        else:
            self.model_status.setText("Model: not loaded")
            self.model_status.setStyleSheet("color: #fab387;")

    def _load_model(self):
        def work():
            model_manager.initialize()
            return model_manager.load()
        w = WorkerThread(work)
        w.result.connect(lambda ok: self._refresh_status())
        w.error.connect(lambda e: QMessageBox.critical(self, "Error", e[:200]))
        w.start()
        self._m = w


# ==================================================================
# MAIN WINDOW
# ==================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ULTRON Control Center")
        self.setMinimumSize(1150, 780)
        self.resize(1320, 860)

        chat = ChatTab()
        self.home = HomeTab()
        tabs = QTabWidget()
        tabs.addTab(self.home, "🏠 Home")
        tabs.addTab(chat, "💬 Chat")
        tabs.addTab(VoiceTab(chat), "🎤 Voice")
        tabs.addTab(ImageTab(), "🎨 Image")
        tabs.addTab(AutomationTab(), "🤖 Automation")
        tabs.addTab(MonitorTab(), "🌍 Monitor")
        tabs.addTab(PermissionsTab(), "🔐 Permissions")
        tabs.addTab(ModulesTab(), "🔧 Modules")
        tabs.addTab(SettingsTab(), "⚙ Settings")
        self.tabs = tabs
        self.setCentralWidget(tabs)

        # Alt+` is handled as a real press/release push-to-talk key.
        try:
            self._hotkey_hint = QShortcut(QKeySequence("Alt+Shift+V"), self, self._on_voice_hotkey,
                                          context=Qt.ApplicationShortcut)
        except TypeError:
            # PyQt5 signature: (key, parent, member, ambiguousMember, context)
            self._hotkey_hint = QShortcut(QKeySequence("Alt+Shift+V"), self, self._on_voice_hotkey,
                                          None, Qt.ApplicationShortcut)
        QApplication.instance().installEventFilter(self)

        # tray
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        self.tray.setToolTip("ULTRON Control Center")
        tm = QMenu()
        tm.addAction("Show", self.showNormal)
        tm.addAction("Voice mode (Alt+`)", self._on_voice_hotkey)
        tm.addAction("Quit", self._quit)
        self.tray.setContextMenu(tm)
        self.tray.show()

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("ULTRON ready — press Alt+` to talk to the voice assistant")

        # init kernel in background
        QTimer.singleShot(200, self._init_kernel)

    def _on_voice_hotkey(self):
        """Arm hands-free wake-word mode for tray and the alternate shortcut."""
        panel = getattr(self.home, "voice_panel", None)
        if panel is None:
            return
        panel.arm_listen()
        self.statusBar().showMessage("Voice assistant armed — say 'Hey ULTRON' + command")

    def _start_hold_voice(self):
        panel = getattr(self.home, "voice_panel", None)
        if panel is None:
            return
        if not voice_assistant.start_push_to_talk():
            self.statusBar().showMessage("Voice assistant could not start recording")
            return
        panel._sync_state()
        st = voice_assistant.state.value
        self.statusBar().showMessage(f"Voice assistant: {st} — release Alt+` when finished")
        self.tray.showMessage("ULTRON", "Listening — release Alt+` when finished",
                              QSystemTrayIcon.Information, 2500)

    def _stop_hold_voice(self):
        panel = getattr(self.home, "voice_panel", None)
        if panel is None:
            return

        def work():
            return voice_assistant.stop_push_to_talk()

        def done(text):
            panel._sync_state()
            if text:
                self.statusBar().showMessage(f"Heard: {text[:100]}")
            else:
                self.statusBar().showMessage("No speech detected")

        w = WorkerThread(work)
        w.result.connect(done)
        w.error.connect(lambda error: self.statusBar().showMessage(f"Voice error: {error[:120]}"))
        w.start()
        self._voice_hold_thread = w

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease):
            text = event.text()
            alt_down = bool(event.modifiers() & Qt.AltModifier)
            if alt_down and text in ("`", "~"):
                if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
                    self._start_hold_voice()
                    return True
                if event.type() == QEvent.KeyRelease and not event.isAutoRepeat():
                    self._stop_hold_voice()
                    return True
        return super().eventFilter(watched, event)

    def _init_kernel(self):
        def work():
            # Keep launch responsive; voice and the LLM start from explicit
            # user actions instead of competing with the initial GUI render.
            kernel.initialize(start_voice=False)
            return True
        w = WorkerThread(work)
        w.result.connect(lambda _: self.statusBar().showMessage(
            "Kernel ready — ULTRON online | Alt+` = voice"))
        w.start()
        self._k = w

    def _quit(self):
        voice_assistant.stop()
        kernel.shutdown()
        QApplication.quit()

    def closeEvent(self, e):
        self.hide()
        self.tray.showMessage("ULTRON", "Running in background — Alt+` still works",
                              QSystemTrayIcon.Information, 2500)
        e.ignore()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ULTRON Control Center")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(DARK)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()