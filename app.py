"""Workframe Health · Clinical Data De-identification (local application).

Runs only on this computer at http://127.0.0.1:8765. Nothing is uploaded anywhere.
Start:  WF-DeID.exe   (portable Windows build)   or   python app.py   (from source)
"""
import csv
import datetime as dt
import importlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import traceback
import webbrowser
from importlib import metadata
from pathlib import Path
from threading import Timer

APP_VERSION = "2.1"
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- helper modes (before Flask is imported)
def _pick(kind):
    """Native Windows/Mac file or folder dialog, run in its own process."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if kind == "folder":
        r = filedialog.askdirectory(title="Choose a folder", mustexist=True)
        out = [r] if r else []
    else:
        types = {"export": [("Hospital export", "*.xlsx *.xls *.xlsm *.csv *.pdf"), ("All files", "*.*")],
                 "reports": [("Radiology reports", "*.xlsx *.xls *.csv *.docx *.pdf *.txt"), ("All files", "*.*")],
                 "labs": [("Laboratory export", "*.xlsx *.xls *.csv"), ("All files", "*.*")]}.get(kind, [("All files", "*.*")])
        r = filedialog.askopenfilenames(title="Choose file(s)", filetypes=types)
        out = list(r)
    root.destroy()
    print(json.dumps([str(Path(p)) for p in out]))


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--pick":
    _pick(sys.argv[2])
    sys.exit(0)
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--selftest":
    import selftest
    sys.exit(selftest.main())

from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_file, send_from_directory, url_for  # noqa: E402
from markupsafe import escape  # noqa: E402

from wf_deid import pipeline, spec, xray  # noqa: E402
from wf_deid.paths import base, tesseract_cmd  # noqa: E402

APP_DIR = base()
PRIVATE = APP_DIR / "KEEP_INSIDE_HOSPITAL"
DEFAULT_OUT = APP_DIR / "output"
VAULT = PRIVATE / "id_vault.enc"
CONFIRMED = PRIVATE / "confirmed_xray_links.csv"
STATIC = APP_DIR / "static"
PORT = 8765
app = Flask(__name__)
LAST = {}

# ---------------------------------------------------------------- design
CSS = """
@font-face{font-family:Inter;src:url(/static/Inter-Regular.otf);font-weight:400}
@font-face{font-family:Inter;src:url(/static/Inter-Medium.otf);font-weight:500}
@font-face{font-family:Inter;src:url(/static/Inter-SemiBold.otf);font-weight:600}
@font-face{font-family:Inter;src:url(/static/Inter-Bold.otf);font-weight:700}
:root{--ink:#0B0D13;--ink2:#1B1E26;--paper:#F6F6F2;--card:#FFFFFF;--line:#E3E3DC;--line2:#D2D2CA;--muted:#6B6E76;--soft:#9A9CA3;
--lime:#BFF749;--limeDeep:#5E7F00;--limeWash:#F3FCDD;--ok:#1F7A4D;--okWash:#E7F4EC;--warn:#9A6B00;--warnWash:#FBF3DF;--bad:#B3261E;--badWash:#FBE9E7}
*{box-sizing:border-box}html{-webkit-font-smoothing:antialiased}
body{margin:0;font:14px/1.6 Inter,"Segoe UI",system-ui,sans-serif;color:var(--ink);background:var(--paper)}
a{color:inherit}
header.top{background:var(--ink);color:#F2F2EE}
.bar{max-width:1240px;margin:0 auto;padding:0 36px;height:68px;display:flex;align-items:center;gap:22px}
.bar img.wm{height:17px;display:block}
.bar .div{width:1px;height:22px;background:#2C2F37}
.bar .prod{font-size:13px;letter-spacing:.02em;color:#C9CBC6}.bar .prod b{color:#F2F2EE;font-weight:600}
.bar .right{margin-left:auto;display:flex;gap:18px;align-items:center;font-size:12px;color:#9EA1A8}
.dot{width:7px;height:7px;border-radius:50%;background:var(--lime);display:inline-block;margin-right:7px;box-shadow:0 0 0 3px rgba(191,247,73,.15)}
.sub{background:var(--ink2);color:#B9BCC2;font-size:12px}
.sub .bar{height:38px;gap:26px}.sub a{text-decoration:none;color:#B9BCC2}.sub a.on{color:#F2F2EE}.sub a.on:after{content:"";display:block;height:2px;background:var(--lime);margin-top:9px}
main{max-width:1240px;margin:0 auto;padding:38px 36px 80px}
.eyebrow{font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);display:flex;align-items:center;gap:9px}
.eyebrow:before{content:"";width:9px;height:9px;background:var(--lime);border:1px solid var(--ink)}
h1{font-size:34px;line-height:1.15;font-weight:700;letter-spacing:-.025em;margin:12px 0 10px}
h2{font-size:18px;font-weight:600;letter-spacing:-.01em;margin:0}
h3{font-size:14px;font-weight:600;margin:0}
.lead{font-size:15px;color:#3A3D45;max-width:760px;margin:0}
.grid{display:grid;grid-template-columns:1fr 360px;gap:28px;margin-top:30px;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px}
.card .hd{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px}
.card .bd{padding:22px 24px}
.muted{color:var(--muted)}.small{font-size:12px}
.step{display:grid;grid-template-columns:44px 1fr;gap:16px;padding:22px 24px;border-bottom:1px solid var(--line)}
.step:last-child{border-bottom:0}
.num{font:600 12px/1 Inter;color:var(--ink);border:1px solid var(--ink);width:32px;height:32px;display:flex;align-items:center;justify-content:center;border-radius:50%}
.req,.opt{font-size:10.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:3px 8px;border-radius:3px;margin-left:8px;vertical-align:2px}
.req{background:var(--ink);color:var(--lime)}.opt{background:#EEEEE8;color:var(--muted)}
.help{color:var(--muted);font-size:12.5px;margin:3px 0 12px}
.field{display:flex;gap:8px;margin-top:8px}
input[type=text],input[type=password],textarea{width:100%;padding:10px 12px;border:1px solid var(--line2);border-radius:4px;font:13px/1.45 Inter,"Segoe UI",sans-serif;background:#FCFCFA;color:var(--ink)}
input:focus,textarea:focus{outline:2px solid var(--lime);outline-offset:0;border-color:var(--ink)}
textarea{min-height:46px;resize:vertical}
label.l{display:block;font-size:12px;font-weight:600;margin-top:12px;color:#2A2D34}
.btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--ink);background:var(--ink);color:#fff;padding:10px 18px;border-radius:4px;
font:600 13px Inter,"Segoe UI",sans-serif;cursor:pointer;text-decoration:none;white-space:nowrap}
.btn:hover{background:#000}.btn.lime{background:var(--lime);color:var(--ink);border-color:var(--ink)}.btn.lime:hover{background:#B2EC35}
.btn.ghost{background:#fff;color:var(--ink);border-color:var(--line2)}.btn.ghost:hover{border-color:var(--ink)}
.btn.sm{padding:8px 13px;font-size:12px}
.runbar{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:20px 24px;background:#FBFBF8;border-top:1px solid var(--line);border-radius:0 0 6px 6px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:600;text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);background:#FBFBF8;position:sticky;top:0}
td{padding:10px 12px;border-bottom:1px solid #EEEEE8;vertical-align:top}
tr:last-child td{border-bottom:0}
.scroll{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:4px;background:#fff}
.pill{display:inline-block;font-size:10.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;padding:3px 9px;border-radius:20px;white-space:nowrap}
.p-ok{background:var(--okWash);color:var(--ok)}.p-warn{background:var(--warnWash);color:var(--warn)}.p-bad{background:var(--badWash);color:var(--bad)}.p-info{background:#EEF0F5;color:#3D4A66}
.side .card{margin-bottom:18px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;font-size:12.5px}
.kv dt{color:var(--muted)}.kv dd{margin:0;font-weight:500}
ol.flow{margin:0;padding:0;list-style:none;counter-reset:f}
ol.flow li{counter-increment:f;position:relative;padding:0 0 14px 34px;font-size:12.5px;color:#3A3D45}
ol.flow li:before{content:counter(f,decimal-leading-zero);position:absolute;left:0;top:0;font:600 11px Inter;color:var(--limeDeep)}
ol.flow b{color:var(--ink);display:block;font-size:13px}
.notice{border-left:3px solid var(--lime);background:var(--limeWash);padding:12px 16px;font-size:12.5px;border-radius:0 4px 4px 0}
.status{display:flex;align-items:center;gap:16px;padding:20px 24px;border-radius:6px;margin-top:22px;border:1px solid}
.status .t{font-size:17px;font-weight:600}
.s-ok{background:var(--okWash);border-color:#BFE0CD;color:var(--ok)}.s-warn{background:var(--warnWash);border-color:#EED9A6;color:var(--warn)}.s-bad{background:var(--badWash);border-color:#F1C3BE;color:var(--bad)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:6px;overflow:hidden;margin-top:14px}
.metrics div{background:#fff;padding:16px 18px}.metrics b{display:block;font-size:24px;font-weight:600;letter-spacing:-.02em}.metrics span{font-size:12px;color:var(--muted)}
section.block{margin-top:34px}section.block>.eyebrow{margin-bottom:10px}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.thumbs figure{margin:0;background:#111;border-radius:4px;overflow:hidden}.thumbs img{width:100%;display:block}
.thumbs figcaption{color:#aaa;font-size:10.5px;padding:6px 8px;word-break:break-all}
.err{background:#fff;border:1px solid #F1C3BE;border-left:3px solid var(--bad);padding:16px 18px;white-space:pre-wrap;font:12px/1.5 Consolas,monospace;margin-top:18px;border-radius:4px}
footer{border-top:1px solid var(--line);color:var(--soft);font-size:11.5px}
footer .bar{height:56px}
#busy{display:none;position:fixed;inset:0;background:rgba(11,13,19,.72);z-index:9;align-items:center;justify-content:center}
#busy div{background:#fff;padding:28px 34px;border-radius:6px;max-width:420px;text-align:left}
.spin{width:22px;height:22px;border:3px solid #E5E6E0;border-top-color:var(--ink);border-radius:50%;animation:s 1s linear infinite;margin-bottom:14px}
@keyframes s{to{transform:rotate(360deg)}}
"""

SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Workframe Health · De-identification</title><link rel="icon" href="/static/app_icon.png"><style>{{ css|safe }}</style></head><body>
<header class="top"><div class="bar"><img class="wm" src="/static/workframe_wordmark_light.svg" alt="Workframe"><span class="div"></span>
<span class="prod"><b>Health</b> &nbsp;·&nbsp; Clinical Data De-identification</span>
<span class="right"><span><span class="dot"></span>Running locally · offline</span><span>Version {{ ver }}</span></span></div></header>
<nav class="sub"><div class="bar"><a href="/" class="{{ 'on' if tab=='prepare' else '' }}">Prepare a batch</a>
<a href="/result" class="{{ 'on' if tab=='result' else '' }}">Latest results</a><a href="/system" class="{{ 'on' if tab=='system' else '' }}">System readiness</a>
<a href="/about" class="{{ 'on' if tab=='about' else '' }}">Data handling</a></div></nav>
<main>{{ body|safe }}</main>
<footer><div class="bar"><span>© Workframe Ventures · Health</span><span style="margin-left:auto">Identifiable data is processed on this computer only. Share only the TRANSFER archive.</span></div></footer>
<div id="busy"><div><div class="spin"></div><h3>Processing the batch</h3><p class="muted small" style="margin:6px 0 0">Reading records, linking imaging and reports,
de-identifying, building the dataset and running the pre-transfer checks. Large batches can take several minutes. Keep this window open.</p></div></div>
<script>
async function pick(kind, target, multi){
  const r = await fetch('/browse?kind='+kind); const j = await r.json();
  if(j.error){alert(j.error);return}
  if(!j.paths.length) return;
  const el=document.getElementById(target);
  if(multi && el.value.trim()){el.value = el.value.trim()+'\\n'+j.paths.join('\\n')} else {el.value = j.paths.join('\\n')}
}
document.querySelectorAll('form.run').forEach(f=>f.addEventListener('submit',()=>{document.getElementById('busy').style.display='flex'}));
</script></body></html>"""


def page(body, tab="prepare"):
    return render_template_string(SHELL, body=body, css=CSS, ver=APP_VERSION, tab=tab)


def back(title, msg, tab="prepare"):
    return page(f'<div class="eyebrow">Action needed</div><h1>{escape(title)}</h1><div class="err">{escape(msg)}</div>'
                f'<p style="margin-top:22px"><a class="btn" href="/">Return to batch preparation</a></p>', tab)


# ---------------------------------------------------------------- system readiness
def _ver(dist):
    try:
        return metadata.version(dist)
    except Exception:
        return ""


def system_checks(out_dir=None):
    rows = []

    def add(component, purpose, state, detail):
        rows.append({"component": component, "purpose": purpose, "state": state, "detail": detail})

    frozen = getattr(sys, "frozen", False)
    add("Application runtime", "Executes the de-identification pipeline locally", "Operational",
        f"Workframe De-ID {APP_VERSION} · Python {platform.python_version()} · {'portable build' if frozen else 'source installation'} · {platform.system()} {platform.release()}")
    ok = True
    for mod in ("pandas", "openpyxl"):
        try:
            importlib.import_module(mod)
        except ImportError:
            ok = False
    add("Record readers", "Reads hospital exports in Excel (.xlsx/.xls), CSV and PDF printouts", "Operational" if ok else "Unavailable",
        f"pandas {_ver('pandas')} · openpyxl {_ver('openpyxl')} · xlrd {_ver('xlrd') or 'not installed (.xls disabled)'} · pdfplumber {_ver('pdfplumber') or 'not installed'}")
    try:
        importlib.import_module("docx")
        rd = True
    except ImportError:
        rd = False
    add("Radiology report readers", "Reads report lists (Excel/CSV) and report documents (Word .docx, PDF)",
        "Operational" if rd and _ver("pdfplumber") else "Advisory", f"python-docx {_ver('python-docx') or 'not installed'} · pdfplumber {_ver('pdfplumber') or 'not installed'}")
    add("DICOM imaging engine", "Reads, de-identifies and re-writes X-ray images (DICOM)", "Operational" if xray.available() else "Unavailable",
        f"pydicom {_ver('pydicom')}" + (" · JPEG decoders: pylibjpeg " + _ver("pylibjpeg") if _ver("pylibjpeg") else "") if xray.available()
        else "Not installed. Batches without images still run; imaging files cannot be processed.")
    cmd = tesseract_cmd()
    tver = ""
    if cmd:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = cmd
            tver = str(pytesseract.get_tesseract_version()).split()[0]
        except Exception:
            tver = ""
    add("Burned-in text detection (OCR)", "Finds and masks names or numbers printed inside images", "Operational" if tver else "Advisory",
        f"Tesseract {tver} ({'bundled' if 'WF-DeID' in str(cmd) or str(APP_DIR) in str(cmd) else 'system'})" if tver
        else "Tesseract not found. Imaging will still be de-identified in headers; every preview must then be checked by eye.")
    ok = all(_ver(d) for d in ("reportlab", "pikepdf"))
    add("Clinical document output", "Writes de-identified discharge summaries as archival PDF/A", "Operational" if ok else "Unavailable",
        f"reportlab {_ver('reportlab')} · pikepdf {_ver('pikepdf')}")
    add("Identity vault encryption", "Protects the mapping that keeps patient IDs stable across batches", "Operational" if _ver("cryptography") else "Unavailable",
        f"cryptography {_ver('cryptography')} · scrypt key derivation · Fernet (AES-128-CBC + HMAC-SHA256)")
    add("Schema validation", "Validates every released file against schema.json before transfer", "Operational" if _ver("jsonschema") else "Unavailable",
        f"jsonschema {_ver('jsonschema')}")
    try:
        from wf_deid.coding import Coder
        c = Coder()
        add("Clinical coding tables", "ICD-10, WHO ATC, LOINC/UCUM, specialty and procedure mappings", "Operational",
            f"{len(c.icd)} diagnosis rules · {len(c.drugs)} medicine brands · {len(c.loinc)} test codes · {len(c.spec)} specialties · {len(c.proc)} procedures")
    except Exception as e:
        add("Clinical coding tables", "ICD-10, WHO ATC, LOINC/UCUM, specialty and procedure mappings", "Unavailable", f"Could not load: {e}")
    if VAULT.exists():
        when = dt.datetime.fromtimestamp(VAULT.stat().st_mtime).strftime("%d %b %Y, %H:%M")
        add("Identity vault", "Hospital-held key for consistent anonymised IDs", "Operational", f"Present · last updated {when} · {VAULT}")
    else:
        add("Identity vault", "Hospital-held key for consistent anonymised IDs", "Information", "Not yet created. The hospital administrator sets its password on the first run.")
    target = Path(out_dir) if out_dir else DEFAULT_OUT
    probe = target if target.exists() else target.anchor or APP_DIR
    try:
        free = shutil.disk_usage(str(probe)).free / 1e9
        add("Storage", "Space for the released dataset, images and archive", "Operational" if free > 5 else "Advisory",
            f"{free:,.1f} GB free on {Path(str(probe)).anchor or probe}" + ("" if free > 5 else " · at least 5 GB recommended"))
    except Exception:
        add("Storage", "Space for the released dataset, images and archive", "Advisory", "Free space could not be measured")
    add("Network", "Not required", "Information", "All processing is local. No data is sent from this application.")
    return rows


def pill(state):
    cls = {"Operational": "p-ok", "PASS": "p-ok", "Advisory": "p-warn", "WARN": "p-warn", "MANUAL": "p-warn", "Information": "p-info",
           "N/A": "p-info", "Unavailable": "p-bad", "FAIL": "p-bad"}.get(state, "p-info")
    return f'<span class="pill {cls}">{escape(state)}</span>'


def readiness_table(rows):
    tr = "".join(f"<tr><td><b>{escape(r['component'])}</b><div class='muted small'>{escape(r['purpose'])}</div></td>"
                 f"<td>{pill(r['state'])}</td><td class='small'>{escape(r['detail'])}</td></tr>" for r in rows)
    return f"<table><tr><th style='width:34%'>Component</th><th style='width:12%'>Status</th><th>Detail</th></tr>{tr}</table>"


# ---------------------------------------------------------------- pages
def paths_from(text):
    return [p.strip().strip('"') for p in (text or "").replace(";", "\n").splitlines() if p.strip()]


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name)


@app.get("/browse")
def browse():
    kind = request.args.get("kind", "files")
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--pick", kind]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--pick", kind]
    try:
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=900, creationflags=flags)
        lines = [l for l in out.stdout.splitlines() if l.strip().startswith("[")]
        return jsonify({"paths": json.loads(lines[-1]) if lines else []})
    except Exception as e:
        return jsonify({"paths": [], "error": f"The file browser could not be opened on this computer ({e}). Type or paste the path instead."})


@app.get("/")
def home():
    s = request.args
    checks = system_checks()
    blocking = [r for r in checks if r["state"] == "Unavailable" and r["component"] not in ("DICOM imaging engine",)]
    advis = [r for r in checks if r["state"] in ("Advisory", "Unavailable")]
    first = not VAULT.exists()
    pw = ("<label class='l'>Create the vault password</label><div class='help'>First run on this computer. Minimum 8 characters. The password protects the file that "
          "keeps each patient's anonymised ID the same across batches. It is held by the hospital administrator; if it is lost, future batches cannot reuse the same IDs.</div>"
          "<div class='field'><input type='password' name='password' minlength='8' required placeholder='New vault password'>"
          "<input type='password' name='password2' minlength='8' required placeholder='Confirm password'></div>") if first else \
         ("<label class='l'>Vault password</label><div class='help'>Set by the hospital administrator on the first run.</div>"
          "<div class='field'><input type='password' name='password' required placeholder='Vault password'></div>")
    readiness = ("<span class='pill p-ok'>All components operational</span>" if not advis else
                 f"<span class='pill {'p-bad' if blocking else 'p-warn'}'>{len(advis)} item(s) need attention</span>")
    body = f"""
<div class="eyebrow">Prepare a batch</div>
<h1>De-identify hospital records for research</h1>
<p class="lead">Takes the hospital's export as supplied, links radiology reports and images to the right patient and admission, removes every direct
identifier, and builds the dataset in the receiving team's format: modules, documentation, pre-transfer checklist and SHA-256 archive.</p>
<div class="grid">
<form class="card run" method="post" action="/run">
 <div class="hd"><h2>Batch inputs</h2>{readiness}</div>
 <div class="step"><div class="num">01</div><div><h3>Hospital records export <span class="req">Required</span></h3>
  <div class="help">Admissions list and/or discharge summaries, as exported by the hospital system: Excel (.xlsx, .xls), CSV, or the discharge-summary printout (PDF). Several files may be combined.</div>
  <div class="field"><textarea id="exports" name="export_paths" placeholder="Choose the export file(s)">{escape(s.get('exports', ''))}</textarea>
  <button type="button" class="btn ghost sm" onclick="pick('export','exports',true)">Browse files</button></div>
  <label class="l">Source system name <span class="muted" style="font-weight:400">(as it should appear in the README)</span></label>
  <div class="field"><input type="text" name="source_system" value="{escape(s.get('system', ''))}" placeholder="e.g. Hospital Information System export"></div></div></div>
 <div class="step"><div class="num">02</div><div><h3>Radiology reports <span class="opt">Optional</span></h3>
  <div class="help">A report list (Excel/CSV, one row per report) and/or a folder of individual report documents (Word .docx or PDF). Each report is matched to the patient
  by IP number, patient number, then name, sex, age and date. Reports are used with or without images.</div>
  <div class="field"><textarea id="reports" name="report_paths" placeholder="Report list file(s) and/or report folder(s)">{escape(s.get('reports', ''))}</textarea>
  <div style="display:flex;flex-direction:column;gap:6px"><button type="button" class="btn ghost sm" onclick="pick('reports','reports',true)">Add files</button>
  <button type="button" class="btn ghost sm" onclick="pick('folder','reports',true)">Add folder</button></div></div></div></div>
 <div class="step"><div class="num">03</div><div><h3>Imaging (DICOM) <span class="opt">Optional</span></h3>
  <div class="help">Folder of DICOM files exported from the radiology system. Leave empty when images are not supplied; the batch still runs.</div>
  <div class="field"><input type="text" id="xrays" name="xray_dir" value="{escape(s.get('xrays', ''))}" placeholder="Folder of DICOM images">
  <button type="button" class="btn ghost sm" onclick="pick('folder','xrays',false)">Browse folder</button></div></div></div>
 <div class="step"><div class="num">04</div><div><h3>Laboratory results <span class="opt">Optional</span></h3>
  <div class="help">Laboratory export with one row per result (test, value, unit, date), linked by IP number or patient number.</div>
  <div class="field"><input type="text" id="labs" name="lab_path" value="{escape(s.get('labs', ''))}" placeholder="Laboratory export file">
  <button type="button" class="btn ghost sm" onclick="pick('labs','labs',false)">Browse file</button></div></div></div>
 <div class="step"><div class="num">05</div><div><h3>Output location and vault <span class="req">Required</span></h3>
  <div class="help">Where the batch folder is created on this computer. The TRANSFER archive inside it is the only item to be shared.</div>
  <div class="field"><input type="text" id="outdir" name="out_dir" value="{escape(s.get('out', str(DEFAULT_OUT)))}">
  <button type="button" class="btn ghost sm" onclick="pick('folder','outdir',false)">Choose folder</button></div>
  {pw}</div></div>
 <div class="runbar"><span class="muted small">Nothing is uploaded. Identifiable inputs remain on this computer.</span>
 <button class="btn lime" type="submit" {'disabled' if blocking else ''}>Run de-identification →</button></div>
</form>
<aside class="side">
 <div class="card"><div class="hd"><h3>How a batch is processed</h3></div><div class="bd"><ol class="flow">
  <li><b>Read</b>Columns recognised by meaning; any layout accepted.</li>
  <li><b>Link</b>Reports and images matched to patient and admission. Uncertain matches are held for review.</li>
  <li><b>De-identify</b>Names, numbers, contacts and exact dates removed; free text scrubbed; burned-in text masked; small groups suppressed.</li>
  <li><b>Structure</b>Modules in FHIR R4 NDJSON and CSV, PDF/A documents, DICOM by anonymised study.</li>
  <li><b>Verify and package</b>13-point pre-transfer checklist, leak scan, schema validation, SHA-256 archive.</li></ol></div></div>
 <div class="card"><div class="hd"><h3>Confidentiality</h3></div><div class="bd small">
  <div class="notice">Only <b>output › batch › TRANSFER</b> may leave the hospital. Review lists, previews, uploads and the identity vault stay in
  <b>KEEP_INSIDE_HOSPITAL</b>.</div></div></div>
 <form class="card" method="post" action="/samples"><div class="hd"><h3>Demonstration data</h3></div><div class="bd small">
  <p class="muted" style="margin-top:0">Creates invented hospital exports, radiology reports (Excel, Word, PDF) and X-rays with deliberate traps, then fills this form. No real patient data.</p>
  <button class="btn ghost sm" type="submit">Create demonstration data</button></div></form>
 <div class="card"><div class="hd"><h3>System readiness</h3><a class="small" href="/system">Full report →</a></div><div class="bd small">
  {''.join(f"<div style='display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px solid #F0F0EA'><span>{escape(r['component'])}</span>{pill(r['state'])}</div>" for r in checks)}
 </div></div>
</aside></div>"""
    return page(body)


@app.get("/system")
def system():
    rows = system_checks()
    issues = [r for r in rows if r["state"] in ("Advisory", "Unavailable")]
    verdict = ("<div class='status s-ok'><div class='t'>Ready for processing</div><div class='small'>All required components are operational on this computer.</div></div>"
               if not issues else
               f"<div class='status {'s-bad' if any(r['state'] == 'Unavailable' for r in issues) else 's-warn'}'><div class='t'>{len(issues)} component(s) need attention</div>"
               "<div class='small'>See the detail column. Advisory items do not stop a batch but reduce automatic protection.</div></div>")
    body = f"""<div class="eyebrow">System readiness</div><h1>Pre-flight verification</h1>
<p class="lead">Each component the pipeline depends on, its purpose, and its state on this computer. Run before every batch and include this page when
recording the hospital's sign-off.</p>{verdict}
<div class="card" style="margin-top:20px">{readiness_table(rows)}</div>
<p class="muted small" style="margin-top:14px">Checked {dt.datetime.now().strftime('%d %b %Y, %H:%M')} · Application folder: {escape(str(APP_DIR))}</p>"""
    return page(body, "system")


@app.get("/about")
def about():
    body = """<div class="eyebrow">Data handling</div><h1>Where data lives</h1>
<p class="lead">This application runs entirely on the hospital computer. It has no server, no account and no upload. The table shows what is created and who may see it.</p>
<div class="card" style="margin-top:24px"><table>
<tr><th>Item</th><th>Location</th><th>May leave the hospital?</th></tr>
<tr><td><b>Hospital exports, reports, images</b> (identifiable)</td><td>Where the hospital saved them</td><td><span class="pill p-bad">No</span></td></tr>
<tr><td><b>Identity vault</b> (encrypted)</td><td>KEEP_INSIDE_HOSPITAL / id_vault.enc</td><td><span class="pill p-bad">No</span></td></tr>
<tr><td><b>Review lists, mapping, previews, run report</b></td><td>output / batch / KEEP_INSIDE_HOSPITAL</td><td><span class="pill p-bad">No</span></td></tr>
<tr><td><b>Anonymised dataset</b> (for inspection)</td><td>output / batch / anonymised_dataset_v1.0</td><td><span class="pill p-warn">Via the archive</span></td></tr>
<tr><td><b>TRANSFER archive + SHA-256</b></td><td>output / batch / TRANSFER</td><td><span class="pill p-ok">Yes, by the agreed secure route</span></td></tr>
</table></div>
<section class="block"><div class="eyebrow">Output format</div><div class="card"><div class="bd small">
Released folder <b>anonymised_dataset_v[X.X]</b>: 00_documentation (README, data dictionary CSV/XLSX, schema.json, value sets, ERD, anonymisation methodology,
data quality statement, file inventory) · 01_patient · 02_encounters · 03_observations · 04_documents (PDF/A) · 05_imaging (studies, reports, DICOM by
anonymised study / series / instance) · 06_governance. Structured data in FHIR R4 NDJSON with a parallel CSV extract.</div></div></section>"""
    return page(body, "about")


@app.post("/samples")
def samples():
    from wf_deid import samples as S
    try:
        r = S.make_all(APP_DIR / "sample_data")
    except Exception as e:
        return back("Demonstration data could not be created", f"{e}\n\n{traceback.format_exc()}")
    return redirect(url_for("home", exports="\n".join(str(p) for p in r["exports"]), labs=str(r["labs"]), xrays=str(r["xrays"]),
                            reports="\n".join(str(p) for p in r["reports"]), out=str(DEFAULT_OUT), system="Demonstration HIS export"))


@app.post("/run")
def run():
    f = request.form
    exports = paths_from(f.get("export_paths"))
    reports = paths_from(f.get("report_paths"))
    lab = f.get("lab_path", "").strip().strip('"') or None
    xdir = f.get("xray_dir", "").strip().strip('"') or None
    out = f.get("out_dir", "").strip().strip('"') or str(DEFAULT_OUT)
    pw = f.get("password", "")
    if "password2" in f and pw != f.get("password2"):
        return back("Passwords do not match", "Enter the same vault password in both fields.")
    if not exports:
        return back("No hospital export selected", "Choose at least one export file in step 01.")
    missing = [p for p in exports + reports if not Path(p).exists()]
    if missing:
        return back("File or folder not found", "\n".join(missing))
    if lab and not Path(lab).exists():
        return back("Laboratory file not found", lab)
    if xdir and not Path(xdir).is_dir():
        return back("Imaging folder not found", xdir)
    if xdir and not xray.available():
        return back("Imaging engine unavailable", "The DICOM engine is not installed on this computer. Remove the imaging folder to run records and reports only.")
    logs = []
    try:
        rep = pipeline.run(exports, xdir, out, pw, lab_file=lab, log=logs.append, radiology_paths=reports or None,
                           source_system=f.get("source_system", "").strip() or None)
    except pipeline.StopRun as e:
        return back("Stopped before anything shareable was written", str(e))
    except Exception as e:
        return back("The batch could not be completed", f"{e}\n\n{traceback.format_exc()}")
    LAST.clear()
    LAST.update(rep)
    LAST["log"] = logs
    return redirect(url_for("result"))


def read_csv(path, limit=500):
    p = Path(path)
    if not p.exists():
        return [], []
    with open(p, encoding="utf-8-sig") as fh:
        r = list(csv.reader(fh))
    return (r[0], r[1:limit + 1]) if r else ([], [])


def table(head, rows, pill_col=None, nice=None, scroll=True):
    if not head or not rows:
        return '<p class="muted small">None.</p>'
    th = "".join(f"<th>{escape((nice or {}).get(h, h.replace('_', ' ')))}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{pill(c) if i == pill_col else escape(c)}</td>" for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="{"scroll" if scroll else "card"}"><table><tr>{th}</tr>{tr}</table></div>'


def review_form(prv, fname, kind):
    head, rows = read_csv(prv / fname)
    if not rows:
        return '<p class="muted small">None waiting.</p>'
    ix = {h: i for i, h in enumerate(head)}
    if kind == "xray":
        cols = [("X-ray file", lambda x: Path(x[ix["xray_file"]]).name), ("Name on image", lambda x: x[ix["xray_name"]]), ("ID on image", lambda x: x[ix["xray_id"]]),
                ("Image date", lambda x: x[ix["xray_date"]])]
        key = lambda x: x[ix["study_uid"]]
    else:
        cols = [("Report", lambda x: x[ix["report_source"]]), ("Name on report", lambda x: x[ix["report_name"]]), ("ID on report", lambda x: x[ix["report_id"]]),
                ("Report date", lambda x: x[ix["report_date"]]), ("Study", lambda x: x[ix["report_study"]])]
        key = lambda x: x[ix["report_key"]]
    cols += [("Suggested patient", lambda x: x[ix["suggested_name"]]), ("Admission", lambda x: x[ix["suggested_admission_no"]]),
             ("Admitted", lambda x: x[ix["suggested_admission_date"]]), ("Score", lambda x: x[ix["score"]])]
    th = "<th>Same patient?</th>" + "".join(f"<th>{c}</th>" for c, _ in cols)
    tr = "".join(f"<tr><td><input type='checkbox' name='accept' value='{escape(key(x))}|{escape(x[ix['admission_key']])}'></td>"
                 + "".join(f"<td>{escape(fn(x))}</td>" for _, fn in cols) + "</tr>" for x in rows)
    return (f"<form method='post' action='/confirm'><div class='scroll'><table><tr>{th}</tr>{tr}</table></div>"
            "<div style='display:flex;gap:14px;align-items:center;margin-top:12px'><button class='btn sm' type='submit'>Save confirmed links</button>"
            "<span class='muted small'>Confirmed links are applied automatically when the batch is run again.</span></div></form>")


@app.get("/result")
def result():
    r = LAST
    if not r:
        return page("<div class='eyebrow'>Latest results</div><h1>No batch has been run in this session</h1>"
                    "<p class='lead'>Results appear here after a batch completes. Previous batches remain in the output folder.</p>"
                    "<p style='margin-top:20px'><a class='btn' href='/'>Prepare a batch</a></p>", "result")
    prv = Path(r["private_folder"])
    st = r["status"]
    cls, title = ("s-bad", "Batch failed: do not share") if st.startswith("FAILED") else \
                 (("s-warn", "Passed automated checks: manual sign-off required") if "manual" in st else ("s-ok", "Passed: ready for transfer"))
    counts = r["counts"]
    metric = lambda n, label: f"<div><b>{n:,}</b><span>{escape(label)}</span></div>"
    im = r.get("imaging", {})
    mets = "".join(metric(counts.get(m, 0), spec.MODULES[m]["title"]) for m in
                   ("patient_demographics", "encounters", "diagnoses", "procedures", "medications", "vitals", "laboratory_results", "document_index",
                    "imaging_studies", "imaging_reports"))
    mets += metric(im.get("report_only_studies", 0), "Report-only imaging studies") + metric(im.get("x_ray_images_received", 0), "Image files received")
    ck = table(["no", "item", "status", "detail"], [[str(c["no"]), c["item"], c["status"], c["detail"]] for c in r["checklist"]], pill_col=2,
               nice={"no": "#", "item": "Pre-transfer check", "status": "Status", "detail": "Detail"}, scroll=False)
    unm = "".join(f"<section class='block'><div class='eyebrow'>Not coded · {k} ({n})</div>" + table(*read_csv(prv / f"unmapped_{k}.csv")) + "</section>"
                  for k, n in r["unmapped"].items() if n)
    previews = sorted((prv / "review_previews").glob("*.png"))[:150]
    thumbs = "".join(f"<figure><a href='/preview/{p.name}' target='_blank'><img src='/preview/{p.name}'></a><figcaption>{escape(p.stem)}</figcaption></figure>"
                     for p in previews)
    warn = "".join(f"<div class='notice' style='margin-top:10px;border-left-color:#E0B84A;background:#FBF6E6'>{escape(w)}</div>" for w in r["warnings"] + r["errors"])
    body = f"""<div class="eyebrow">Latest results · {escape(r['batch'])}</div><h1>Batch summary</h1>
<div class="status {cls}"><div class="t">{title}</div><div class="small" style="color:inherit;opacity:.85">{escape(st)}</div></div>{warn}
<div class="metrics">{mets}</div>
<div class="card" style="margin-top:18px"><div class="bd"><dl class="kv">
<dt>Transfer archive</dt><dd>{escape(str(Path(r['batch_folder']) / 'TRANSFER'))}</dd>
<dt>Released dataset</dt><dd>{escape(r['release_folder'])}</dd>
<dt>Keep inside hospital</dt><dd>{escape(r['private_folder'])}</dd></dl>
<div style="display:flex;gap:10px;margin-top:16px"><a class="btn sm" href="/open?which=transfer">Open TRANSFER folder</a>
<a class="btn ghost sm" href="/open?which=release">Open released dataset</a><a class="btn ghost sm" href="/open?which=private">Open private folder</a></div></div></div>
<section class="block"><div class="eyebrow">Pre-transfer checklist (section 9)</div>{ck}
<p class="muted small">MANUAL items require a named person at the hospital to review and sign off before transfer.</p></section>
<section class="block"><div class="eyebrow">Radiology reports awaiting confirmation</div>
<p class="muted small">Close but not exact name, or no patient number. These reports were not released.</p>{review_form(prv, 'report_review_queue.csv', 'report')}</section>
<section class="block"><div class="eyebrow">Images awaiting confirmation</div>{review_form(prv, 'xray_review_queue.csv', 'xray')}</section>
<section class="block"><div class="eyebrow">Radiology reports not matched to any patient</div>{table(*read_csv(prv / 'report_unmatched.csv'))}</section>
<section class="block"><div class="eyebrow">Images not matched to any patient</div>{table(*read_csv(prv / 'xray_unmatched.csv'))}</section>
<section class="block"><div class="eyebrow">How the export's columns were read</div>
<p class="muted small">If a column was read incorrectly, add one line to config/column_overrides.yaml and run the batch again.</p>{table(*read_csv(prv / 'column_mapping.csv'))}</section>
{unm}
<section class="block"><div class="eyebrow">Image previews after masking ({len(previews)})</div>
<p class="muted small">Confirm that no name, number or date remains visible in any image (checklist item 2).</p>
<div class="thumbs">{thumbs or "<p class='muted small'>No images in this batch.</p>"}</div></section>"""
    return page(body, "result")


@app.post("/confirm")
def confirm():
    picks = request.form.getlist("accept")
    rows = []
    if CONFIRMED.exists():
        with open(CONFIRMED, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    have = {x["study_uid"] for x in rows}
    for p in picks:
        uid, key = p.split("|", 1)
        if uid not in have:
            rows.append({"study_uid": uid, "admission_key": key, "decision": "yes"})
    PRIVATE.mkdir(exist_ok=True)
    with open(CONFIRMED, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["study_uid", "admission_key", "decision"])
        w.writeheader()
        w.writerows(rows)
    return page(f"<div class='eyebrow'>Review saved</div><h1>{len(picks)} link(s) confirmed</h1><p class='lead'>Run the same batch again to include them. "
                "Confirmations are kept inside the hospital and reused for future batches.</p><p style='margin-top:20px'><a class='btn' href='/'>Return to batch preparation</a></p>", "result")


@app.get("/preview/<name>")
def preview(name):
    if not LAST or "/" in name or "\\" in name or not name.endswith(".png"):
        abort(404)
    p = Path(LAST["private_folder"]) / "review_previews" / name
    if not p.exists():
        abort(404)
    return send_file(p)


@app.get("/open")
def open_folder():
    if not LAST:
        return redirect(url_for("home"))
    which = request.args.get("which")
    p = {"release": LAST["release_folder"], "private": LAST["private_folder"],
         "transfer": str(Path(LAST["batch_folder"]) / "TRANSFER")}.get(which, LAST["private_folder"])
    if not Path(p).exists():
        p = LAST["batch_folder"]
    try:
        if os.name == "nt":
            os.startfile(p)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass
    return redirect(url_for("result"))


def _port_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    url = f"http://127.0.0.1:{PORT}"
    if _port_busy():
        print("Workframe De-ID is already running. Opening it in the browser.")
        webbrowser.open(url)
        return
    PRIVATE.mkdir(exist_ok=True)
    print("=" * 66)
    print(f"  WORKFRAME  Health · Clinical Data De-identification {APP_VERSION}")
    print("=" * 66)
    print(f"  Open in your browser:  {url}")
    print("  Runs only on this computer. Nothing is uploaded.")
    print("  Keep this window open while you work. Close it to stop the app.")
    print("=" * 66)
    Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=PORT, threads=4)
    except ImportError:
        app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()
