"""Where the app's folders are. Works the same when run from source and when packaged as a Windows .exe."""
import os
import shutil
import sys
from pathlib import Path


def base():
    """Folder that holds data/, config/, fonts/, KEEP_INSIDE_HOSPITAL/ and output/.
    Packaged: the folder of WF-DeID.exe. From source: the project folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def tesseract_cmd():
    """Bundled Tesseract first (portable build), then the standard Windows install, then PATH."""
    b = base()
    for c in (b / "tesseract" / "tesseract.exe", b / "tesseract" / "tesseract",
              Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"), Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")):
        if c.exists():
            if c.parent.name == "tesseract" and (c.parent / "tessdata").exists():
                os.environ.setdefault("TESSDATA_PREFIX", str(c.parent / "tessdata"))
            return str(c)
    return shutil.which("tesseract")
