"""Tests for Windows application launching."""
from ultron.tools.pc_control import LaunchAppTool


def test_canonical_app_name_maps_google_chrome():
    assert LaunchAppTool._canonical_app_name("Google Chrome") == "chrome.exe"
