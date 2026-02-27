# -*- mode: python ; coding: utf-8 -*-

import os
import sys

block_cipher = None

# Resource files to include
datas = [
    ('style.css', '.'),
    ('data/danbooru.csv', 'data'),
    ('data/workflow.json', 'data'),
    ('assets', 'assets'),
    ('pages', 'pages'),
    ('widgets', 'widgets'),
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'gi',
        'gi.repository.Gtk',
        'gi.repository.Adw',
        'gi.repository.Gdk',
        'gi.repository.GdkPixbuf',
        'gi.repository.GtkSource',
        'gi.repository.Pango',
        'requests',
        'websocket',
    ],
    hookspath=[],
    hooksconfig={
        "gi": {
            "themes": ["Adwaita"],
            "icons": ["Adwaita"],
            "languages": ["en_US"],
            "module-versions": {
                "Gtk": "4.0",
                "Gtk4LayerShell": "1.0",
                "Gdk": "4.0",
                "GdkPixbuf": "2.0",
                "Pango": "1.0",
                "Gio": "2.0",
                "GLib": "2.0",
                "GObject": "2.0",
            },
        },
    },
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='cozyapp',
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
    icon='assets/com.mudknight.cozyapp.png'
)
