"""ULTRON Packaging and Installation"""
import os
import sys
import subprocess
import shutil
import json
import winreg
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import urllib.request
import zipfile
import tempfile

from ultron.config import config


@dataclass
class InstallConfig:
    install_dir: Path
    start_menu: bool = True
    desktop_shortcut: bool = True
    auto_start: bool = False
    python_path: Optional[Path] = None
    create_venv: bool = True
    install_deps: bool = True


class WindowsInstaller:
    """Windows installer for ULTRON"""

    def __init__(self, config: InstallConfig):
        self.config = config
        self.install_dir = Path(config.install_dir).resolve()
        self.python_path = config.python_path or Path(sys.executable)

    def check_prerequisites(self) -> Tuple[bool, List[str]]:
        """Check system prerequisites"""
        issues = []

        # Check Python version
        if sys.version_info < (3, 8):
            issues.append(f"Python 3.8+ required, found {sys.version}")

        # Check Windows version
        import platform
        if platform.system() != "Windows":
            issues.append("ULTRON requires Windows")

        # Check disk space
        import shutil
        free_space = shutil.disk_usage(self.install_dir.drive or "C:").free
        if free_space < 2 * 1024 * 1024 * 1024:  # 2 GB
            issues.append(f"Insufficient disk space: {free_space / (1024**3):.1f} GB free")

        # Check for git
        if not shutil.which("git"):
            issues.append("Git not found in PATH")

        return len(issues) == 0, issues

    def create_directories(self):
        """Create installation directories"""
        dirs = [
            self.install_dir,
            self.install_dir / "logs",
            self.install_dir / "data",
            self.install_dir / "models",
            self.install_dir / "config",
            self.install_dir / "scripts",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    def setup_python_environment(self):
        """Set up Python virtual environment"""
        venv_dir = self.install_dir / ".venv"

        if self.config.create_venv and not venv_dir.exists():
            import venv
            venv.create(venv_dir, with_pip=True)

        # Determine python executable
        if self.config.create_venv:
            python_exe = venv_dir / "Scripts" / "python.exe"
        else:
            python_exe = self.python_path

        return python_exe

    def install_dependencies(self, python_exe: Path):
        """Install Python dependencies"""
        requirements = self.install_dir / "requirements.txt"
        
        if not requirements.exists():
            # Create default requirements
            requirements.write_text("""# ULTRON Dependencies
pyyaml>=6.0
psutil>=5.9
llama-cpp-python==0.2.72
sentence-transformers>=2.2.0
chromadb>=0.4.0
pypdf>=3.0
python-docx>=1.1.0
openpyxl>=3.1.0
pandas>=2.0.0
faster-whisper>=1.0
piper-tts>=1.0
pyaudio>=0.2.14
accelerate>=0.26.0
diffusers>=0.30.0
""")

        # Upgrade pip
        subprocess.run([str(self.python_path), "-m", "pip", "install", "--upgrade", "pip"], check=True)

        # Install dependencies
        subprocess.run([str(python_exe), "-m", "pip", "install", "-r", str(requirements)], check=True)

    def create_shortcuts(self):
        """Create Start Menu and Desktop shortcuts"""
        if not self.config.start_menu and not self.config.desktop_shortcut:
            return

        import winshell
        from win32com.client import Dispatch

        shell = Dispatch('WScript.Shell')
        python_exe = self.install_dir / ".venv" / "Scripts" / "python.exe"
        main_script = self.install_dir / "ultron_control_center.py"
        shortcut_paths = []
        if self.config.desktop_shortcut:
            desktop = winshell.desktop()
            shortcut_paths.append(Path(desktop) / "ULTRON.lnk")

        if self.config.start_menu:
            start_menu = Path(winshell.programs())
            ultron_folder = start_menu / "ULTRON"
            ultron_folder.mkdir(exist_ok=True)
            shortcut_paths.append(ultron_folder / "ULTRON.lnk")

        for shortcut_path in shortcut_paths:
            shortcut = shell.CreateShortCut(str(shortcut_path))
            shortcut.Targetpath = str(python_exe)
            shortcut.Arguments = f'"{main_script}"'
            shortcut.WorkingDirectory = str(self.install_dir)
            shortcut.IconLocation = str(self.install_dir / "assets" / "ultron.ico")
            shortcut.save()

    def setup_auto_start(self):
        """Add to Windows startup"""
        if not self.config.auto_start:
            return

        import winshell
        startup = Path(winshell.startup())
        shortcut_path = startup / "ULTRON.lnk"

        import winshell
        from win32com.client import Dispatch
        shell = Dispatch('WScript.Shell')
        
        python_exe = self.install_dir / ".venv" / "Scripts" / "python.exe"
        main_script = self.install_dir / "ultron_control_center.py"

        shortcut = shell.CreateShortCut(str(shortcut_path))
        shortcut.Targetpath = str(python_exe)
        shortcut.Arguments = f'"{main_script}"'
        shortcut.WorkingDirectory = str(self.install_dir)
        shortcut.save()

    def register_uninstaller(self):
        """Register uninstaller in Windows"""
        uninstall_dir = self.install_dir / "uninstall"
        uninstall_dir.mkdir(exist_ok=True)

        # Create uninstaller script
        uninstall_script = uninstall_dir / "uninstall.py"
        uninstall_script.write_text(f'''#!/usr/bin/env python3
"""ULTRON Uninstaller"""
import shutil
import winreg
from pathlib import Path

def uninstall():
    install_dir = Path(r"{self.install_dir}")
    
    # Remove start menu shortcuts
    import winshell
    from win32com.client import Dispatch
    
    # Desktop shortcut
    desktop = Path(winshell.desktop()) / "ULTRON.lnk"
    if desktop.exists():
        desktop.unlink()
    
    # Start menu
    start_menu = Path(winshell.programs()) / "ULTRON"
    if start_menu.exists():
        shutil.rmtree(start_menu)
    
    # Startup
    startup = Path(winshell.startup()) / "ULTRON.lnk"
    if startup.exists():
        startup.unlink()
    
    # Registry uninstall entry
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall", 0, winreg.KEY_WRITE)
        winreg.DeleteKey(key, "ULTRON")
    except:
        pass
    
    # Remove installation directory
    print(f"Removing {{install_dir}}...")
    shutil.rmtree(install_dir, ignore_errors=True)
    print("Uninstall complete!")

if __name__ == "__main__":
    uninstall()
''')

    def create_config(self):
        """Create default configuration"""
        config_dir = self.install_dir / "config"
        config_dir.mkdir(exist_ok=True)

        config_file = config_dir / "config.yaml"
        if not config_file.exists():
            default_config = {
                "ultron": {
                    "name": "ULTRON",
                    "version": "0.1.0",
                    "data_dir": ".ultron",
                    "log_level": "INFO"
                },
                "resources": {
                    "max_vram_gb": 3.5,
                    "max_ram_gb": 12,
                    "max_context_tokens": 4096,
                },
                "model": {
                    "name": "qwen2.5-1.5b-instruct-q4_k_m",
                    "repo_id": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
                    "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
                    "path": "models/qwen2.5-1.5b-instruct-q4_k_m.gguf",
                    "n_gpu_layers": 0,
                    "n_ctx": 4096,
                    "n_batch": 512,
                    "n_threads": 8,
                },
                "voice": {
                    "enabled": True,
                    "push_to_talk": True,
                    "stt": {"engine": "faster-whisper", "model": "base.en"},
                    "tts": {"engine": "piper", "voice": "en_US-lessac-medium"},
                },
                "workspace": {
                    "default_path": None,
                    "read_only_default": True,
                },
                "permissions": {
                    "levels": ["READ", "SEARCH", "CREATE", "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"],
                    "default_level": "READ",
                    "require_confirmation": ["MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"]
                },
                "security": {
                    "audit_log_enabled": True,
                    "audit_log_path": "logs/audit.log",
                },
            }
            import yaml
            with open(config_dir / "config.yaml", "w") as f:
                yaml.dump(default_config, f, default_flow_style=False)

    def run_installation(self, progress_callback=None) -> Tuple[bool, str]:
        """Run complete installation"""
        try:
            steps = [
                ("Checking prerequisites", self.check_prerequisites),
                ("Creating directories", self.create_directories),
                ("Setting up Python environment", lambda: self.setup_python_environment()),
                ("Installing dependencies", lambda: self.install_dependencies(self.setup_python_environment())),
                ("Creating configuration", self.create_config),
                ("Creating shortcuts", self.create_shortcuts),
                ("Setting up auto-start", self.setup_auto_start),
                ("Registering uninstaller", self.register_uninstaller),
            ]

            for i, (step_name, step_func) in enumerate(steps):
                if progress_callback:
                    progress_callback(i / len(steps), step_name)
                
                if isinstance(step_func, tuple):
                    success, msg = step_func()
                    if not success:
                        return False, f"Failed at {step_name}: {msg}"
                else:
                    step_func()

            if progress_callback:
                progress_callback(1.0, "Installation complete")

            return True, "Installation successful!"

        except Exception as e:
            return False, f"Installation failed: {e}"


def create_installer_package(output_dir: Path = None) -> Path:
    """Create installer package"""
    output_dir = output_dir or Path.cwd() / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create installer script
    installer_script = output_dir / "install_ultron.py"
    
    installer_content = '''#!/usr/bin/env python3
"""ULTRON Installer"""
import sys
from pathlib import Path

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ULTRON Installer")
    parser.add_argument("--install-dir", default=r"C:\\Ultron", help="Installation directory")
    parser.add_argument("--no-start-menu", action="store_true")
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--auto-start", action="store_true")
    args = parser.parse_args()

    install_dir = Path(args.install_dir).resolve()
    
    from ultron.packaging.installer import WindowsInstaller, InstallConfig
    
    config = InstallConfig(
        install_dir=install_dir,
        start_menu=not args.no_start_menu,
        desktop_shortcut=not args.no_desktop,
        auto_start=args.auto_start,
    )
    
    installer = WindowsInstaller(config)
    
    def progress(pct, msg):
        print(f"[{pct*100:.0f}%] {msg}")
    
    success, msg = installer.run_installation(progress)
    
    if success:
        print(f"\\n{msg}")
        print(f"Installation complete at: {install_dir}")
        sys.exit(0)
    else:
        print(f"\\n{msg}")
        sys.exit(1)

if __name__ == "__main__":
    main()
'''
    
    installer_script.write_text(installer_content)
    return installer_script


def create_portable_package(output_dir: Path = None) -> Path:
    """Create portable zip package"""
    output_dir = output_dir or Path.cwd() / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)

    package_dir = output_dir / "ultron-portable"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    # Copy source
    src = Path(__file__).parent.parent
    for item in src.iterdir():
        if item.name not in (".git", "__pycache__", ".venv", "dist", "build", "logs", ".ultron"):
            if item.is_dir():
                shutil.copytree(item, package_dir / item.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(item, package_dir)

    # Create zip
    zip_path = output_dir / "ultron-portable.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in package_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(package_dir))

    shutil.rmtree(package_dir)
    return zip_path


# Example usage
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ULTRON Packaging Tools")
    parser.add_argument("command", choices=["install", "package", "portable"])
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    if args.command == "install":
        installer_script = create_installer_package(args.output_dir)
        print(f"Installer created: {installer_script}")
    elif args.command == "package":
        zip_path = create_portable_package(args.output_dir)
        print(f"Portable package created: {zip_path}")
    elif args.command == "portable":
        zip_path = create_portable_package(args.output_dir)
        print(f"Portable package created: {zip_path}")