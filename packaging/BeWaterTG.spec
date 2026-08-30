# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_root = Path(SPECPATH).parent
datas = [
    (str(project_root / "templates"), "templates"),
    (str(project_root / "static"), "static"),
    (str(project_root / ".env.example"), "."),
    (str(project_root / "messages.txt"), "."),
    (str(project_root / "messages_hzbjhspa.txt"), "."),
]

# 说明：
# - 依赖 telethon 1.44.0 使用纯 Python 的 pyaes + rsa，不依赖 cryptography/cryptg，
#   因此无需为 telethon.cryptg / cryptography 声明 hiddenimports；静态分析即可覆盖。
# - waitress 为纯 Python 包，无需额外 hiddenimport。
a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BeWaterTG", console=True)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="BeWaterTG",
    # 显式固定 onedir 内部目录，稳定资源目录解析（templates/static 位于 _internal 下）。
    contents_directory="_internal",
)
