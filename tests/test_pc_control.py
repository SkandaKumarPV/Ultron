"""Tests for Windows application launching."""
import json
from unittest.mock import patch

from ultron.tools.pc_control import LaunchAppTool


def test_canonical_app_name_maps_google_chrome():
    assert LaunchAppTool._canonical_app_name("Google Chrome") == "chrome.exe"


def test_app_launch_honors_the_saved_permission(tmp_path):
    settings = tmp_path / "permissions.json"
    with patch("ultron.tools.pc_control.config.resolve_path", return_value=settings):
        settings.write_text(json.dumps({"apps": {"chrome.exe": False}}), encoding="utf-8")
        assert "Save Permissions" in LaunchAppTool.permission_error("Google Chrome")
        settings.write_text(json.dumps({"apps": {"chrome.exe": True}}), encoding="utf-8")
        assert LaunchAppTool.permission_error("Google Chrome") is None
