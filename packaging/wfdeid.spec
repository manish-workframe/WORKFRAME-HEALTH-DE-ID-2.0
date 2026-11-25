# PyInstaller build for the portable Windows app (run by GitHub Actions: .github/workflows/build-windows.yml)
# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
hidden, datas = ["selftest", "tkinter", "tkinter.filedialog"], []
for m in ["wf_deid", "pydicom", "pylibjpeg", "libjpeg", "pikepdf", "reportlab", "pdfplumber", "pdfminer", "docx", "jsonschema",
          "jsonschema_specifications", "openpyxl", "xlrd", "waitress", "pytesseract", "PIL"]:
    try:
        hidden += collect_submodules(m)
    except Exception:
        pass
for m in ["pdfminer", "jsonschema_specifications", "docx", "pydicom", "reportlab", "pikepdf"]:
    try:
        datas += collect_data_files(m)
    except Exception:
        pass
for d in ["pydicom", "pylibjpeg", "pylibjpeg-libjpeg", "pikepdf", "reportlab", "jsonschema", "jsonschema-specifications", "python-docx",
          "pdfplumber", "pdfminer.six", "openpyxl", "pandas", "numpy", "cryptography", "Flask", "waitress", "pytesseract", "xlrd", "pillow", "PyYAML"]:
    try:
        datas += copy_metadata(d)
    except Exception:
        pass

a = Analysis([os.path.join(ROOT, "app.py")], pathex=[ROOT], hiddenimports=hidden, datas=datas,
             excludes=["matplotlib", "IPython", "PyQt5", "PyQt6", "PySide2", "PySide6", "tests", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WF-DeID", console=True,
          icon=os.path.join(ROOT, "static", "app_icon.ico"), version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="WF-DeID")
