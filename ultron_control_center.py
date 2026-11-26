#!/usr/bin/env python3
"""
ULTRON Control Center desktop application.
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
from math import isfinite
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parent))

# Qt
try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QTextEdit, QLineEdit, QComboBox, QCheckBox,
        QScrollArea, QFrame, QMenuBar, QMenu, QStatusBar, QSystemTrayIcon,
        QPlainTextEdit, QGroupBox, QSpinBox, QListWidget, QListWidgetItem,
        QTableWidget, QTableWidgetItem, QHeaderView, QTextBrowser, QSlider,
        QSplitter, QFileDialog, QMessageBox, QSizePolicy, QFormLayout,
        QInputDialog, QStyle, QStyleFactory, QProgressBar, QProgressDialog,
        QRadioButton, QButtonGroup, QSpacerItem
    )
    from PySide6.QtCore import Qt, QTimer, Signal, QThread, QObject, QSize, QEvent, QSettings
    from PySide6.QtGui import QFont, QColor, QAction, QKeySequence, QIcon, QShortcut
    HAS_QT = True
except ImportError:
    try:
        from PyQt5.QtWidgets import (
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QLabel, QPushButton, QTextEdit, QLineEdit, QComboBox, QCheckBox,
            QScrollArea, QFrame, QMenuBar, QMenu, QStatusBar, QSystemTrayIcon,
            QPlainTextEdit, QGroupBox, QSpinBox, QListWidget, QListWidgetItem,
            QTableWidget, QTableWidgetItem, QHeaderView, QTextBrowser, QSlider,
            QSplitter, QFileDialog, QMessageBox, QSizePolicy, QFormLayout,
            QInputDialog, QStyle, QProgressBar, QProgressDialog, QRadioButton,
            QButtonGroup, QSpacerItem, QShortcut
        )
        from PyQt5.QtCore import Qt, QTimer, pyqtSignal as Signal, QThread, QObject, QSize, QEvent, QSettings
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

from ultron.gui.command_center import CommandCenter, CommandShell, label

DARK = """
QMainWindow, QWidget { background: #181316; color: #f3e6e7; font-family: 'Segoe UI'; font-size: 10pt; }
QPushButton { background: #452b33; color: #f3e6e7; border: 1px solid #62404a; padding: 8px 14px; border-radius: 6px; }
QPushButton:hover { background: #62404a; border-color: #ffaba0; }
QPushButton:pressed { background: #85535d; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTextBrowser { background: #130f12; color: #f3e6e7; border: 1px solid #452b33; padding: 6px; border-radius: 4px; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #ffaba0; }
QListWidget, QTableWidget { background: #130f12; color: #f3e6e7; border: 1px solid #452b33; alternate-background-color: #181316; }
QHeaderView::section { background: #452b33; color: #f3e6e7; padding: 6px; border: 1px solid #62404a; }
QGroupBox { border: 1px solid #452b33; border-radius: 6px; margin-top: 12px; padding-top: 8px; font-weight: 600; }
QGroupBox::title { color: #ffaba0; left: 10px; padding: 0 4px; }
QStatusBar { background: #130f12; color: #c3adb2; }
QMenuBar { background: #130f12; color: #f3e6e7; }
QMenu { background: #181316; color: #f3e6e7; border: 1px solid #452b33; }
QMenu::item:selected { background: #452b33; }
QScrollBar:vertical { background: #130f12; width: 10px; }
QScrollBar::handle:vertical { background: #85535d; border-radius: 5px; min-height: 30px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #62404a; border-radius: 3px; }
QCheckBox::indicator:checked { background: #ffaba0; }
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

    state_changed = Signal(object, dict)
    transcript_received = Signal(str)
    response_received = Signal(str)

    STATE_TEXT = {
        AssistantState.OFF: "Voice off",
        AssistantState.DORMANT: "Voice ready",
        AssistantState.ARMED: "Waiting for “Hey ULTRON”",
        AssistantState.CAPTURING: "Recording",
        AssistantState.TRANSCRIBING: "Transcribing",
        AssistantState.THINKING: "Processing command",
        AssistantState.SPEAKING: "Speaking",
        AssistantState.ERROR: "Voice needs attention",
    }

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.log = QTextBrowser()
        self.log.setObjectName("commandTranscript")
        self.log.setPlaceholderText("Voice transcripts and replies appear here.")
        self.log.setMinimumHeight(64)
        layout.addWidget(self.log, 1)

        controls = QHBoxLayout()
        self.hint_lbl = label("", "commandMuted")
        self.hint_lbl.setWordWrap(True)
        controls.addWidget(self.hint_lbl, 1)
        self.listen_btn = QPushButton("Enable voice")
        self.listen_btn.setObjectName("commandLink")
        self.listen_btn.clicked.connect(self.toggle_power)
        controls.addWidget(self.listen_btn)
        layout.addLayout(controls)

        self.state_changed.connect(self._sync_state)
        self.transcript_received.connect(lambda text: self._append("You", text))
        self.response_received.connect(lambda text: self._append("ULTRON", text))
        voice_assistant.on_state_change = self.state_changed.emit
        voice_assistant.on_transcript = self.transcript_received.emit
        voice_assistant.on_response = self.response_received.emit
        self._sync_state()

    def toggle_power(self):
        if voice_assistant.enabled:
            voice_assistant.stop()
        else:
            voice_assistant.start()
        self._sync_state()

    def arm_listen(self):
        voice_assistant.arm()
        self._sync_state()

    def _append(self, speaker, text):
        stamp = time.strftime("%H:%M")
        self.log.append(
            f'<p><b style="color:#ffaba0;">{speaker}</b> '
            f'<span style="color:#9ab0c6;">{stamp}</span><br>{html.escape(text)}</p>'
        )

    def _sync_state(self, state=None, data=None):
        current = voice_assistant.state
        error = voice_assistant.error if current == AssistantState.ERROR else None
        self.hint_lbl.setText(error or self.STATE_TEXT[current])
        self.listen_btn.setText("Disable voice" if voice_assistant.enabled else "Enable voice")


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
        self.ws_label.setStyleSheet("color: #c3adb2;")
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
        self.load_btn = QPushButton("Load & Index")
        self.load_btn.clicked.connect(self._load_ws)
        ws_row.addWidget(self.load_btn)
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
        self.messages.setStyleSheet("background: #130f12; border: 1px solid #452b33; padding: 10px;")
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
        self.load_btn.setEnabled(False)
        self.status.setText("Building local search index...")
        def work():
            folder = Path(p).expanduser().resolve()
            if not folder.is_dir():
                return {"success": False, "error": "Select a folder that exists."}
            ws = workspace_manager.create_workspace(folder.name or str(folder), str(folder), read_only=True)
            from ultron.workspace.analyzer import workspace_analyzer
            workspace_manager.select_workspace(ws.workspace_id)
            return {"workspace": ws.name, **workspace_analyzer.index_workspace(ws.workspace_id)}
        w = WorkerThread(work)
        def done(res):
            self.load_btn.setEnabled(True)
            if res.get("workspace"):
                self.ws_label.setText(f"Workspace: {res['workspace']}")
            if not res.get("success"):
                if res.get("workspace"):
                    self.use_workspace.setChecked(False)
                    self.use_workspace.setEnabled(False)
                self.status.setText("Indexing failed")
                QMessageBox.warning(
                    self,
                    "Indexing failed",
                    res.get("error", "The folder could not be indexed."),
                )
                return
            self.status.setText(f"Indexed: {res.get('chunks_indexed', 0)} chunks")
            self.use_workspace.setEnabled(True)
            self.use_workspace.setChecked(True)
        def failed(error):
            self.load_btn.setEnabled(True)
            self.status.setText("Indexing failed")
            QMessageBox.critical(self, "Index error", error.splitlines()[0])
        w.result.connect(done)
        w.error.connect(failed)
        w.start()
        self._index_thread = w

    def _append(self, role, text):
        ts = time.strftime("%H:%M:%S")
        if role == "user":
            self.messages.append(f'<p style="color:#ffaba0;"><b>You</b> <span style="color:#6c7086">({ts})</span></p>')
        else:
            self.messages.append(f'<p style="color:#a6e3a1;"><b>ULTRON</b> <span style="color:#6c7086">({ts})</span></p>')
        self.messages.append(f'<p style="margin:0 0 12px 0;">{html.escape(text).replace(chr(10), "<br>")}</p>')

    def _send(self):
        for thread in (getattr(self, "_gen_thread", None), getattr(self, "_img_thread", None)):
            if thread and thread.isRunning():
                return
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
        if lowered.removeprefix("please ").startswith(img_triggers):
            prompt = self._strip_image_trigger(text)
            self._generate_image(prompt)
            return

        use_workspace = self.use_workspace.isChecked()
        workspace_id = workspace_manager.get_current_workspace_id()
        def work():
            from ultron.kernel import UltronRequest, RequestType
            request_type = RequestType.TEXT_COMMAND
            if use_workspace and workspace_id:
                request_type = RequestType.WORKSPACE_QUERY
            r = UltronRequest(
                request_id=f"req_{int(time.time()*1000)}",
                request_type=request_type,
                input_text=text,
                workspace_id=workspace_id,
            )
            return kernel.process(r)
        w = WorkerThread(work)
        def done(resp):
            self._append("assistant", resp.response_text)
            route = resp.metadata.get("route")
            self.status.setText(route.title() if route else "Ready" if resp.success else "Request failed")
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
                    f'<img src="{p}" style="max-width:520px;border-radius:8px;border:1px solid #452b33;">'
                    f'<br><a href="{p}" style="color:#ffaba0;">Open full size</a></p>')
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
        self.backend_status.setStyleSheet("color: #c3adb2;")
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
            "background: #ffaba0; color: #181316; font-weight: 800; font-size: 12pt; border-radius: 6px;")
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
        self.preview.setStyleSheet("background: #130f12; border: 1px solid #452b33; border-radius: 6px; color: #6c7086;")
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
        self.status.setStyleSheet("color: #c3adb2;")
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
                "background: #fab387; color: #181316; font-weight: 800; font-size: 12pt; border-radius: 6px;")
            self.progress.show()
            self.progress.setRange(0, 0)  # indeterminate
            self._pulse_val = 0
            self._pulse_timer.start(600)
        else:
            self.gen_btn.setText("✨ Generate")
            self.gen_btn.setStyleSheet(
                "background: #ffaba0; color: #181316; font-weight: 800; font-size: 12pt; border-radius: 6px;")
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
    def __init__(self):
        super().__init__()
        self._ptt_active = False
        self._mic_settings = QSettings("ULTRON", "Control Center")
        name = self._mic_settings.value("microphone/name", "", type=str)
        host = self._mic_settings.value("microphone/host", "", type=str)
        voice_assistant.input_device = (name, host) if name and host else None
        self._listed_microphones = False
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
        self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #c3adb2;")
        stl.addWidget(self.status_lbl)
        init_btn = QPushButton("▶ Start Voice Engine")
        init_btn.clicked.connect(self._initialize_voice)
        stl.addWidget(init_btn)
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone"))
        self.input_devices = QComboBox()
        self.input_devices.addItem("Windows default input")
        self.input_devices.currentIndexChanged.connect(self._select_microphone)
        mic_row.addWidget(self.input_devices, 1)
        self.refresh_mics = QPushButton("Refresh inputs")
        self.refresh_mics.clicked.connect(self._load_microphones)
        mic_row.addWidget(self.refresh_mics)
        self.test_mic = QPushButton("Test microphone")
        self.test_mic.clicked.connect(self._test_microphone)
        mic_row.addWidget(self.test_mic)
        stl.addLayout(mic_row)
        self.mic_status = QLabel("Choose an input and use Test microphone to check whether it picks up speech.")
        self.mic_status.setWordWrap(True)
        stl.addWidget(self.mic_status)
        lay.addWidget(st)

        ctrl = QGroupBox("Controls")
        cl = QVBoxLayout(ctrl)
        self.ptt = QPushButton("🎤 Hold to Speak")
        self.ptt.setMinimumHeight(60)
        self.ptt.setStyleSheet("font-size: 13pt; font-weight: 700; background: #fab387; border-radius: 8px;")
        self.ptt.pressed.connect(self._ptt_on)
        self.ptt.released.connect(self._ptt_off)
        cl.addWidget(self.ptt)

        wake_button = QPushButton("Arm 'Hey ULTRON'")
        wake_button.clicked.connect(self._arm_wake)
        cl.addWidget(wake_button)
        lay.addWidget(ctrl)

        sets = QGroupBox("Voice Settings")
        sl = QFormLayout(sets)
        self.tts_voice = QComboBox()
        self.tts_voice.addItems(["en_US-lessac-medium", "en_US-ryan-high", "System David"])
        sl.addRow("TTS Voice:", self.tts_voice)
        from ultron.voice.stt import provider_name
        sl.addRow("Speech recognition", QLabel(provider_name()))
        sl.addRow("", QLabel("Wake listening sends detected speech to the selected provider to check for “Hey ULTRON”."))
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
        self.status_lbl.setText("🟡 Checking voice setup...")
        self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f9e2af;")

        def work():
            return None if voice_assistant.start() else voice_assistant.error or "Voice input is unavailable."

        w = WorkerThread(work)
        def done(error):
            if error:
                self.status_lbl.setText("🔴 " + error)
                self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f38ba8;")
            else:
                self.status_lbl.setText("🟢 Listening for 'Hey ULTRON'.")
                self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #a6e3a1;")
        def err(e):
            self.status_lbl.setText("🔴 Error: " + e[:120])
            self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f38ba8;")
        w.result.connect(done)
        w.error.connect(err)
        w.start()
        self._init_thread = w

    # ---------------- push to talk ----------------
    def _ptt_on(self):
        self._ptt_active = voice_assistant.start_push_to_talk(speak_reply=self.speak_replies.isChecked())
        if self._ptt_active:
            self.status_lbl.setText("🎤 Listening...")
            self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #fab387;")
        else:
            self.status_lbl.setText("🔴 " + (voice_assistant.error or
                                    "Could not start recording. Check the voice engine and microphone."))

    def _ptt_off(self):
        if not self._ptt_active:
            return
        self._ptt_active = False
        self.status_lbl.setText("🟡 Transcribing...")
        def work():
            return voice_assistant.stop_push_to_talk()

        w = WorkerThread(work)
        def done(text):
            self.status_lbl.setText("🟢 Heard: " + text[:60] if text else voice_assistant.error or "No speech detected")
            self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #c3adb2;")
        def err(error):
            self.status_lbl.setText("🔴 " + error.splitlines()[0])
            self.status_lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: #f38ba8;")
        w.result.connect(done)
        w.error.connect(err)
        w.start()
        self._ptt_thread = w

    def _arm_wake(self):
        if voice_assistant.arm():
            self.status_lbl.setText("Say 'Hey ULTRON' followed by your command. See Home for the reply.")
        else:
            self.status_lbl.setText(voice_assistant.error or "Voice assistant could not start.")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._listed_microphones:
            self._listed_microphones = True
            self._load_microphones()

    def _load_microphones(self):
        self.refresh_mics.setEnabled(False)

        def done(choices):
            selected = voice_assistant.input_device
            self.input_devices.blockSignals(True)
            self.input_devices.clear()
            self.input_devices.addItem("Windows default input")
            for name, host in choices:
                self.input_devices.addItem(f"{name} — {host}", [name, host])
            index = self.input_devices.findData(list(selected)) if selected else 0
            if index < 0:
                self.input_devices.addItem(f"{selected[0]} — unavailable", list(selected))
                index = self.input_devices.count() - 1
            self.input_devices.setCurrentIndex(index)
            self.input_devices.blockSignals(False)
            self.refresh_mics.setEnabled(True)

        def failed(error):
            self.refresh_mics.setEnabled(True)
            self.mic_status.setText(error.splitlines()[0])

        self._microphone_list_worker = WorkerThread(voice_assistant.microphone_choices)
        self._microphone_list_worker.result.connect(done)
        self._microphone_list_worker.error.connect(failed)
        self._microphone_list_worker.start()

    def _select_microphone(self):
        selected = self.input_devices.currentData()
        if voice_assistant.state == AssistantState.ARMED:
            voice_assistant.disarm()
        voice_assistant.input_device = tuple(selected) if selected else None
        name, host = selected if selected else ("", "")
        self._mic_settings.setValue("microphone/name", name)
        self._mic_settings.setValue("microphone/host", host)
        self.mic_status.setText("Input selected. Test the microphone or use Hold to Speak.")

    def _test_microphone(self):
        if voice_assistant.state == AssistantState.ARMED:
            voice_assistant.disarm()
        self.test_mic.setEnabled(False)
        self.input_devices.setEnabled(False)
        self.mic_status.setText("Speak now. This test stays on your computer.")

        def show_result(message):
            self.mic_status.setText(message)
            self.input_devices.setEnabled(True)
            self.test_mic.setEnabled(True)

        def done(result):
            samples, peak = result
            if not samples:
                message = "This input sent no audio. Try the built-in Microphone Array or another input."
            elif peak < 0.01:
                message = "Windows sent audio, but the signal was very quiet. Check the microphone mute and input volume."
            else:
                message = f"Microphone picked up sound (peak {peak:.0%}). Try Hold to Speak."
            show_result(message)

        self._microphone_test_worker = WorkerThread(voice_assistant.test_microphone)
        self._microphone_test_worker.result.connect(done)
        self._microphone_test_worker.error.connect(lambda error: show_result(error.splitlines()[0]))
        self._microphone_test_worker.start()

    # ---------------- tts ----------------
    def _speak(self):
        txt = self.test_input.text().strip() or "ULTRON voice test"
        def work():
            return voice_assistant.speak(txt, block=True)
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

        t = QLabel("Automation workflows")
        t.setStyleSheet("font-size: 15pt; font-weight: 700;")
        lay.addWidget(t)

        # quick natural language
        ql = QGroupBox("Quick Command")
        qv = QVBoxLayout(ql)
        qv.addWidget(QLabel("Run a task, ask about an indexed folder, or explicitly use Chrome."))
        row = QHBoxLayout()
        self.quick = QLineEdit()
        self.quick.setPlaceholderText("Open Chrome and ask ChatGPT what ROS is")
        row.addWidget(self.quick)
        self.run_button = QPushButton("Run workflow")
        self.run_button.clicked.connect(self._run_quick)
        self.quick.returnPressed.connect(self._run_quick)
        row.addWidget(self.run_button)
        qv.addLayout(row)
        lay.addWidget(ql)

        examples = QGroupBox("Workflow presets")
        examples_layout = QVBoxLayout(examples)
        examples_layout.addWidget(QLabel(
            "Choose an example to load it into Quick Command, then review and execute it."
        ))
        example_buttons = [
            ("Create task", "Create a high priority task to review project documentation"),
            ("List tasks", "List my tasks"),
            ("Describe folder", "Tell me about this folder"),
            ("Ask ChatGPT", "Open Chrome and ask ChatGPT what ROS is"),
            ("Search the web", "Search the web for ROS documentation"),
        ]
        examples_row = QHBoxLayout()
        for label, command in example_buttons:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=command: self._load_example(value))
            examples_row.addWidget(button)
        examples_layout.addLayout(examples_row)
        lay.addWidget(examples)

        self.workflow_result = QPlainTextEdit()
        self.workflow_result.setReadOnly(True)
        self.workflow_result.setPlaceholderText("Workflow output appears here.")
        self.workflow_result.setMaximumHeight(150)
        lay.addWidget(self.workflow_result)

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
        if not text or not self.run_button.isEnabled():
            return
        self.run_button.setEnabled(False)
        self.workflow_result.setPlainText("Running workflow…")

        def work():
            from ultron.kernel import UltronRequest, RequestType
            kernel.initialize(start_voice=False)
            request = UltronRequest(
                request_id=f"req_{int(time.time()*1000)}",
                request_type=RequestType.TEXT_COMMAND,
                input_text=text,
                workspace_id=workspace_manager.get_current_workspace_id(),
            )
            return kernel.process(request)

        def display(message):
            self.workflow_result.setPlainText(message)
            self.run_button.setEnabled(True)

        def done(response):
            route = response.metadata.get("route")
            display(f"{route.title()}\n\n{response.response_text}" if route else response.response_text)
            self._refresh_tasks()

        self._quick_thread = WorkerThread(work)
        self._quick_thread.result.connect(done)
        self._quick_thread.error.connect(lambda error: display(error.splitlines()[0]))
        self._quick_thread.start()

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
    earthquakes_loaded = Signal(object)
    earthquake_failed = Signal(str)

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
        self.status.setStyleSheet("color: #c3adb2;")
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
        self.output.setStyleSheet("background: #130f12; border: 1px solid #452b33; padding: 8px;")
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
            "<h3 style='color:#ffaba0'>Live News Headlines</h3>",
            "<p style='color:#c3adb2'>Updated just now from public RSS feeds.</p>",
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
                    f"<a href='{url}' style='color:#f3e6e7'><b>{title}</b></a><br>"
                    f"<small style='color:#ffaba0'>{source} | {timestamp}</small><br>"
                    f"<span style='color:#c3adb2'>{summary}</span></li>"
                )
            parts.append("</ol>")
        self.output.setHtml("".join(parts))
        self.status.setText(f"Loaded {len(evs)} live headlines")

    def _disasters(self):
        if getattr(self, "_q", None) and self._q.isRunning():
            return
        self.status.setText("Fetching earthquake data (USGS)...")

        def work():
            url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
            with urlopen(url, timeout=10) as response:
                return json.load(response).get("features", [])

        def failed(error):
            self.status.setText("Earthquake feed unavailable")
            self.earthquake_failed.emit(error)

        self._q = WorkerThread(work)
        self._q.result.connect(self._render_eq)
        self._q.error.connect(failed)
        self._q.start()

    def _render_eq(self, features):
        self.earthquakes_loaded.emit(features)
        if not features:
            self.output.setHtml("<i>No magnitude 2.5+ earthquakes reported in the past 24 hours.</i>")
            self.status.setText("No significant events")
            return
        parts = ["<h3>Earthquakes of magnitude 2.5+ (past 24 hours)</h3><ul>"]
        for feature in features[:15]:
            event = feature.get("properties") or {}
            place = html.escape(str(event.get("place") or "Unknown location"))
            magnitude = html.escape(str(event.get("mag") or "—"))
            parts.append(f"<li>M{magnitude} · {place}</li>")
        self.output.setHtml("".join(parts) + "</ul>")
        self.status.setText(f"{len(features)} earthquake events")

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
        html = "<h3 style='color:#ffaba0'>🌤 NOAA/NWS Forecast</h3>"
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

        info = QLabel("Select the apps ULTRON may open, then save. Changes take effect on the next command.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #c3adb2;")
        lay.addWidget(info)

        # apps
        ag = QGroupBox("Applications ULTRON may open")
        av = QVBoxLayout(ag)
        self.app_checks = {}
        perms = load_perms()
        for exe, friendly in APPS:
            row = QHBoxLayout()
            c = QCheckBox(friendly)
            c.setChecked(perms.get("apps", {}).get(exe) is True)
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
        self.setMinimumSize(1050, 700)
        self.resize(1450, 900)

        self.voice_panel = VoiceAssistantPanel()
        self.automation = AutomationTab()
        self.chat = ChatTab()
        self.monitor = MonitorTab()
        self._services_enabled = False
        self.home = CommandCenter(self.voice_panel, self.open_view, self._add_task, self._toggle_services,
                                  self._refresh_events, self._refresh_markets)
        self.monitor.earthquakes_loaded.connect(self.home.show_events)
        self.monitor.earthquake_failed.connect(self._show_event_error)
        pages = (
            ("◈", "Command center", self.home),
            ("◫", "Chat", self.chat),
            ("◉", "Voice", VoiceTab()),
            ("▧", "Image", ImageTab()),
            ("◇", "Automation", self.automation),
            ("▥", "Monitor", self.monitor),
            ("⊙", "Permissions", PermissionsTab()),
            ("▦", "Modules", ModulesTab()),
            ("⚙", "Settings", SettingsTab()),
        )
        self.shell = CommandShell(pages, self._start_hold_voice, self._stop_hold_voice,
                                  self._on_voice_hotkey, self._send_from_overview)
        self.setCentralWidget(self.shell)
        self.voice_panel.state_changed.connect(self._update_voice_status)
        self._update_voice_status()

        # Alt+` is handled as a real press/release push-to-talk key.
        self._holding_voice_shortcut = False
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
        tm.addAction("Arm 'Hey ULTRON' (Alt+Shift+V)", self._on_voice_hotkey)
        tm.addAction("Quit", self._quit)
        self.tray.setContextMenu(tm)
        self.tray.show()

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("ULTRON ready — press Alt+` to talk to the voice assistant")

        # init kernel in background
        QTimer.singleShot(200, self._init_kernel)

    def _send_from_overview(self, text):
        for thread in (getattr(self.chat, "_gen_thread", None), getattr(self.chat, "_img_thread", None)):
            if thread and thread.isRunning():
                self.statusBar().showMessage("Wait for the current chat request to finish.", 5000)
                return False
        self.open_view("Chat")
        self.chat.submit_external_text(text)
        return True

    def _refresh_events(self):
        self.home.events_button.setEnabled(False)
        self.home.event_status.setText("Refreshing USGS events…")
        self.monitor._disasters()

    def _show_event_error(self, error):
        self.home.events_button.setEnabled(True)
        self.home.event_status.setText("USGS refresh failed")

    def _refresh_markets(self):
        self.home.market_button.setEnabled(False)
        self.home.market_status.setText("Refreshing CoinGecko…")

        def work():
            url = (
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
            )
            with urlopen(url, timeout=8) as response:
                data = json.load(response)
            quotes = {}
            for pair, asset in zip(self.home.market_pairs, ("bitcoin", "ethereum", "solana")):
                try:
                    price, change = float(data[asset]["usd"]), float(data[asset]["usd_24h_change"])
                    if price > 0 and isfinite(price) and isfinite(change):
                        quotes[pair] = (price, change)
                except (ValueError, KeyError, TypeError):
                    continue
            return quotes

        self._market_worker = WorkerThread(work)
        self._market_worker.result.connect(self.home.show_quotes)
        self._market_worker.error.connect(lambda _: self.home.show_quotes({}))
        self._market_worker.start()

    def open_view(self, name):
        self.shell.open_view(name)

    def _add_task(self):
        self.automation._add_task()
        self.home.refresh()

    def _toggle_services(self):
        enable = not self._services_enabled
        control = self.home.services_button
        control.setEnabled(False)
        control.setText("Starting…" if enable else "Stopping…")

        def work():
            if enable:
                from ultron.image import image_generator
                kernel.initialize(start_voice=False)
                model_manager.initialize()
                model_manager.load()
                image_generator.initialize()
                voice_assistant.start()
            else:
                voice_assistant.stop()
                kernel.shutdown()

        def done():
            control.setEnabled(True)
            control.setText("Stop services" if self._services_enabled else "Start services")
            self.home.refresh()

        def success(_):
            self._services_enabled = enable
            done()

        def failed(error):
            done()
            self.statusBar().showMessage(error.splitlines()[0], 8000)

        self._service_worker = WorkerThread(work)
        self._service_worker.result.connect(success)
        self._service_worker.error.connect(failed)
        self._service_worker.start()

    def _update_voice_status(self, state=None, data=None):
        self.shell.voice_status.setText(self.voice_panel.STATE_TEXT[voice_assistant.state])
        self.home.refresh()

    def _on_voice_hotkey(self):
        """Arm hands-free wake-word mode for tray and the alternate shortcut."""
        self.voice_panel.arm_listen()
        if voice_assistant.state == AssistantState.ARMED:
            self.statusBar().showMessage("Voice assistant armed. Say 'Hey ULTRON' followed by your command.")
        else:
            self.statusBar().showMessage(voice_assistant.error or "Could not arm voice input.")

    def _start_hold_voice(self):
        if not voice_assistant.start_push_to_talk():
            message = "Voice input is already busy." if voice_assistant.enabled else "Voice input could not start."
            self.statusBar().showMessage(voice_assistant.error or message)
            return False
        self.voice_panel._sync_state()
        self.statusBar().showMessage("Recording. Release to transcribe.")
        return True

    def _stop_hold_voice(self):
        def work():
            return voice_assistant.stop_push_to_talk()

        def done(text):
            self.voice_panel._sync_state()
            if text:
                self.statusBar().showMessage(f"Heard: {text[:100]}")
            else:
                self.statusBar().showMessage(voice_assistant.error or "No speech detected")

        w = WorkerThread(work)
        w.result.connect(done)
        w.error.connect(lambda error: self.statusBar().showMessage(f"Voice error: {error[:120]}"))
        w.start()
        self._voice_hold_thread = w

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and not event.isAutoRepeat():
            grave_key = event.key() in (Qt.Key_QuoteLeft, Qt.Key_AsciiTilde)
            if event.type() == QEvent.KeyPress and grave_key and event.modifiers() & Qt.AltModifier:
                self._holding_voice_shortcut = self._start_hold_voice()
                return True
            if event.type() == QEvent.KeyRelease and grave_key and self._holding_voice_shortcut:
                self._holding_voice_shortcut = False
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
        self.tray.showMessage("ULTRON", "Running in the system tray",
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
