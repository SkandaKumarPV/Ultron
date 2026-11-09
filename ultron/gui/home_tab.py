"""
ULTRON Home Tab - Embeds the frontend dashboard
"""
import sys
import os
from pathlib import Path

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
from PySide6.QtCore import QUrl, QTimer, Qt

# Try to import QWebEngineView (may not be available in all PySide6 installations)
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False
    QWebEngineView = None


class HomeTab(QWidget):
    """Home tab with embedded ULTRON dashboard frontend"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.frontend_url = None
        self.web_view = None
        self._build()
        self._start_frontend_server()
    
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        if HAS_WEBENGINE:
            # Web view for frontend
            self.web_view = QWebEngineView()
            self.web_view.setContextMenuPolicy(Qt.NoContextMenu)
            layout.addWidget(self.web_view)
        else:
            # Fallback: placeholder widget
            self.web_view = None
            self.placeholder = QLabel("WebEngine not available. Click button below to launch in browser.")
            self.placeholder.setAlignment(Qt.AlignCenter)
            self.placeholder.setStyleSheet("font-size: 16px; color: #89b4fa; padding: 40px;")
            layout.addWidget(self.placeholder)
            
            self.launch_btn = QPushButton("Launch Frontend in Browser")
            self.launch_btn.setStyleSheet("""
                background: #4a90e2; 
                color: white; 
                padding: 15px; 
                font-size: 16px; 
                border-radius: 8px;
            """)
            self.launch_btn.clicked.connect(self._launch_in_browser)
            layout.addWidget(self.launch_btn)
        
        # Loading indicator
        self.loading_label = QLabel("Loading ULTRON Dashboard...")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet("""
            font-size: 18px; 
            color: #89b4fa; 
            background: #1e1e2e;
            padding: 40px;
        """)
        self.loading_label.hide()
        layout.addWidget(self.loading_label)
    
    def _start_frontend_server(self):
        """Start the frontend server in a separate thread"""
        import threading
        import subprocess
        
        # Find the frontend server script
        frontend_dir = Path(__file__).parent.parent.parent / "frontend" / "ultron_dashboard"
        server_script = frontend_dir / "server.py"
        
        if server_script.exists():
            # Start server in background
            self.server_process = subprocess.Popen([
                sys.executable, str(server_script), "8080"
            ], cwd=str(frontend_dir))
            
            # Wait for server to start then load
            QTimer.singleShot(2000, self._load_frontend)
        else:
            # Fallback: load directly from file
            index_path = frontend_dir / "index.html"
            if index_path.exists():
                self.web_view.load(QUrl.fromLocalFile(str(index_path)))
            else:
                self._show_fallback()
    
    def _load_frontend(self):
        """Load the frontend in the web view"""
        if not HAS_WEBENGINE or self.web_view is None:
            self._show_fallback()
            return
            
        try:
            self.web_view.load(QUrl("http://localhost:8080"))
            self.web_view.loadFinished.connect(self._on_load_finished)
        except Exception as e:
            print(f"Failed to load frontend: {e}")
            self._show_fallback()
    
    def _launch_in_browser(self):
        """Launch frontend in default browser"""
        import webbrowser
        import subprocess
        
        frontend_dir = Path(__file__).parent.parent.parent / "frontend" / "ultron_dashboard"
        server_script = frontend_dir / "server.py"
        
        if server_script.exists():
            proc = subprocess.Popen([sys.executable, str(server_script), "8080"], cwd=str(frontend_dir))
            import time
            time.sleep(2)
            webbrowser.open("http://localhost:8080")
            print("Frontend launched at http://localhost:8080")
    
    def _on_load_finished(self, ok):
        if ok:
            self.loading_label.hide()
            self.web_view.show()
        else:
            self._show_fallback()
    
    def _show_fallback(self):
        """Show fallback UI if frontend fails to load"""
        self.web_view.hide()
        self.loading_label.setText("Frontend not available. Using fallback UI.")
        self.loading_label.show()
        
        # Create a simple fallback UI
        from PySide6.QtWidgets import QVBoxLayout, QPushButton, QLabel
        fallback = QWidget()
        fallback_layout = QVBoxLayout(fallback)
        
        title = QLabel("ULTRON SYSTEM")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 32px; font-weight: 700; color: #89b4fa; margin: 40px;")
        fallback_layout.addWidget(title)
        
        status = QLabel("SYSTEM ON")
        status.setAlignment(Qt.AlignCenter)
        status.setStyleSheet("font-size: 24px; color: #50c878; font-weight: bold;")
        fallback_layout.addWidget(status)
        
        btn = QPushButton("LAUNCH FRONTEND SERVER")
        btn.setStyleSheet("""
            background: #4a90e2; 
            color: white; 
            padding: 15px; 
            font-size: 16px; 
            border-radius: 8px;
        """)
        btn.clicked.connect(self._start_frontend_server)
        fallback_layout.addWidget(btn)
        
        self.layout().addWidget(fallback)
    
    def closeEvent(self, event):
        if hasattr(self, 'server_process'):
            self.server_process.terminate()
        super().closeEvent(event)


# Standalone frontend launcher
def launch_frontend_standalone():
    """Launch the frontend in the default browser"""
    import webbrowser
    import subprocess
    import sys
    from pathlib import Path
    
    frontend_dir = Path(__file__).parent.parent.parent / "frontend" / "ultron_dashboard"
    server_script = frontend_dir / "server.py"
    
    if server_script.exists():
        proc = subprocess.Popen([sys.executable, str(server_script), "8080"], cwd=str(frontend_dir))
        import time
        time.sleep(2)
        webbrowser.open("http://localhost:8080")
        print("Frontend launched at http://localhost:8080")
        print("Press Ctrl+C to stop the server")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()


if __name__ == "__main__":
    launch_frontend_standalone()