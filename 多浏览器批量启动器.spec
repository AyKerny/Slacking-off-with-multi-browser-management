# -*- mode: python ; coding: utf-8 -*-

# 精简打包体积：排除本程序完全用不到、但会被静态依赖分析拖进来的大模块。
# 其中 hashlib/_ssl 会带来 3.4MB 的 libcrypto-3.dll（已改用 random 生成任务 id，不再需要）。
EXCLUDES = [
    # OpenSSL / hashlib
    'hashlib', '_hashlib', 'ssl', '_ssl',
    # 明确用不到的大模块
    'unicodedata', 'lzma', '_lzma', 'bz2', '_bz2',
    'sqlite3', '_sqlite3', 'decimal', '_decimal',
    'asyncio', 'concurrent', 'multiprocessing',
    'unittest', 'doctest', 'pydoc', 'lib2to3', 'distutils',
    'setuptools', 'pip', 'curses', 'tkinter.test', 'test',
]


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
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
    name='mua',
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
)
