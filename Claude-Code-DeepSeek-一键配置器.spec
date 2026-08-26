# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['D:\\claude\\Claude-Code-DeepSeek-Configurator-V2.9.3\\main.py'],
    pathex=[],
    binaries=[],
    datas=[('D:\\claude\\Claude-Code-DeepSeek-Configurator-V2.9.3\\assets', 'assets')],
    hiddenimports=[],
    hookspath=['D:\\claude\\Claude-Code-DeepSeek-Configurator-V2.9.3\\hooks'],
    hooksconfig={},
    runtime_hooks=['D:\\claude\\Claude-Code-DeepSeek-Configurator-V2.9.3\\hooks\\runtime_tkinter.py'],
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
    name='Claude-Code-DeepSeek-一键配置器',
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
    icon=['D:\\claude\\Claude-Code-DeepSeek-Configurator-V2.9.3\\assets\\app_icon.ico'],
)
