"""Tests for Windows installation integration."""
import sys
import types
from pathlib import Path

from ultron.packaging.installer import InstallConfig, WindowsInstaller


def test_create_shortcuts_creates_desktop_and_start_menu_shortcuts(monkeypatch, tmp_path):
    desktop = tmp_path / "Desktop"
    programs = tmp_path / "Programs"
    desktop.mkdir()
    programs.mkdir()
    created = []

    class FakeShortcut:
        def __init__(self, path):
            self.path = path
            created.append(self)

        def save(self):
            pass

    winshell = types.ModuleType("winshell")
    winshell.desktop = lambda: str(desktop)
    winshell.programs = lambda: str(programs)
    monkeypatch.setitem(sys.modules, "winshell", winshell)

    win32com = types.ModuleType("win32com")
    win32com_client = types.ModuleType("win32com.client")
    win32com_client.Dispatch = lambda _: types.SimpleNamespace(
        CreateShortCut=FakeShortcut
    )
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", win32com_client)

    install_dir = tmp_path / "install"
    installer = WindowsInstaller(InstallConfig(install_dir=install_dir))
    installer.create_shortcuts()

    assert {Path(shortcut.path) for shortcut in created} == {
        desktop / "ULTRON.lnk",
        programs / "ULTRON" / "ULTRON.lnk",
    }
    assert all(
        shortcut.Arguments == f'"{install_dir / "ultron_control_center.py"}"'
        for shortcut in created
    )
