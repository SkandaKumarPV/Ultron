# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ULTRON Control Center"""

block_cipher = None

import os
import glob

# Collect PySide6 binaries/datas
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

pyside6_binaries = collect_dynamic_libs('PySide6')
pyside6_datas = collect_data_files('PySide6')

a = Analysis(
    ['ultron_control_center.py'],
    pathex=['C:\\Ultron'],
    binaries=pyside6_binaries,
    datas=[
        *pyside6_datas,
        ('ultron/config.py', 'ultron'),
        ('.ultron/config/config.yaml', '.ultron/config'),
        ('.ultron/config/permissions.json', '.ultron/config'),
    ],
    hiddenimports=[
        'ultron',
        'ultron.config',
        'ultron.kernel',
        'ultron.model.loader',
        'ultron.workspace.selector',
        'ultron.workspace.analyzer',
        'ultron.workspace.retriever',
        'ultron.workspace.embedder',
        'ultron.workspace.parser',
        'ultron.workspace.chunker',
        'ultron.security.audit',
        'ultron.security.permissions',
        'ultron.tasks.manager',
        'ultron.tools.registry',
        'ultron.tools.pc_control',
        'ultron.tools.workspace_tools',
        'ultron.tools.voice_tools',
        'ultron.tools.task_tools',
        'ultron.tools.workspace_automation',
        'ultron.tools.advanced_automation',
        'ultron.super_monitor',
        'ultron.super_monitor.monitor',
        'ultron.super_monitor.news_aggregator',
        'ultron.super_monitor.weather_monitor',
        'ultron.super_monitor.disaster_monitor',
        'ultron.super_monitor.event_timeline',
        'ultron.super_monitor.evidence_organizer',
        'ultron.super_monitor.source_freshness',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc', 'test', 
        'torch', 'scipy', 'sklearn', 'numpy', 'pandas',
        'transformers', 'llama', 'llama_cpp',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ULTRON_Control_Center',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ULTRON_Control_Center',
)