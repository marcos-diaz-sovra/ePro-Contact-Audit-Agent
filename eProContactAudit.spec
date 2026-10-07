# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files

pw_datas, pw_binaries, pw_hidden = collect_all("playwright")
docx_datas = collect_data_files("docx")

datas = [
    ("config/states.yaml", "config"),
    *pw_datas,
    *docx_datas,
]
binaries = list(pw_binaries)
hiddenimports = list(pw_hidden) + [
    "epro",
    "epro.paths",
    "epro.config",
    "epro.pipeline",
    "epro.browser",
    "epro.attachments",
    "epro.contacts",
    "epro.match",
    "epro.report",
    "epro.models",
    "epro.urls",
    "epro.checkpoint",
    "epro.update",
    "yaml",
    "openpyxl",
    "pdfplumber",
    "pdfminer",
    "pdfminer.high_level",
    "docx",
    "anthropic",
    "pandas",
    "bs4",
    "requests",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="eProContactAudit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="eProContactAudit",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="eProContactAudit.app",
        bundle_identifier="com.mdfcommerce.eprocontactaudit",
    )
