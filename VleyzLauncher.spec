# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

# UCRT рядом с exe: на Windows 7 без обновления KB2999226 иначе не грузится python38.dll
UCRT = sorted(str(path) for path in Path('vendor/ucrt64').glob('*.dll'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[(name, '.') for name in UCRT],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='VleyzLauncher',
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
    icon=['assets/vleyz.ico'],
    version='tools/version_info.txt',
)
