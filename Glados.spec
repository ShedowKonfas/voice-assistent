# -*- mode: python ; coding: utf-8 -*-
"""Сборка onefile-exe без всего venv: только нужные ресурсы."""

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('micro.png', '.'),
        ('micro.ico', '.'),
        ('mute.png', '.'),
        ('unmute.png', '.'),
        ('mix.png', '.'),
        ('restart.png', '.'),
        ('teor.pdf', '.'),
        ('config.json', '.'),
    ],
    hiddenimports=[
        'pystray._win32',
        'pyttsx3.drivers',
        'pyttsx3.drivers.sapi5',
        'comtypes.gen',
        'pygame',
        'edge_tts',
        'gtts',
        'speech_recognition',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['auto_py_to_exe', 'tkinter', 'unittest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Glados',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['micro.ico'],
)
