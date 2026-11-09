"""Run ImageTab generation without displaying the desktop window."""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication

from ultron_control_center import ImageTab


app = QApplication([])
tab = ImageTab()
tab.prompt.setPlainText("a small blue square")
tab.backend_combo.setCurrentText("diffusion")
tab._generate()

deadline = time.time() + 120
while tab._gen_thread.isRunning() and time.time() < deadline:
    app.processEvents()
    time.sleep(0.1)
app.processEvents()

print(f"thread_running={tab._gen_thread.isRunning()}")
print(f"status={tab.status.text()}")
preview = tab.preview.pixmap()
print(f"preview_ready={preview is not None and not preview.isNull()}")
