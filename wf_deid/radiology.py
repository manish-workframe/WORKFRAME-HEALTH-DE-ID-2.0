"""Radiology reports supplied separately from the main export.

Accepted:
  - a table (Excel .xlsx/.xls or CSV): one row per report, any column names (recognised by meaning)
  - a folder (or list) of individual report documents: Word .docx, PDF (text-based), plain .txt
    A PDF or Word file may hold several reports one after another.

Each report becomes a dict:
  key, source, pid, adm, name, age, sex, date (datetime.date), modality (DICOM code), body_region,
  view, contrast (Yes/No/Unknown), text (findings + impression, identifiers still present), status,
  people (names of doctors found, for redaction), ids (numbers found, for redaction)
"""
import datetime as dt
import hashlib
import re
from pathlib import Path

from . import ingest

DATE_RX = r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[\s-][A-Za-z]{3,9}[\s-]\d{2,4})"

MODALITY = [  # (pattern, DICOM modality code)
    (r"\bMAMMO", "MG"),
    (r"\b(MRI|MR\b|MAGNETIC RESONANCE)", "MR"),
    (r"\b(CECT|NCCT|HRCT|CT\b|CT[- ]|COMPUTED TOMOGRAPHY|CAT SCAN)", "CT"),
    (r"\b(USG|ULTRASOUND|ULTRASONOGRAPHY|SONOGRAPHY|SONO|DOPPLER|ECHO\b|NT SCAN|ANOMALY SCAN|TVS\b)", "US"),
    (r"\b(FLUORO|BARIUM|IVP\b|HSG\b|MCU\b|RGU\b)", "RF"),
    (r"\b(PET|NUCLEAR|BONE SCAN|SPECT)", "NM"),
    (r"\b(X[\s-]?RAY|XRAY|RADIOGRAPH|SKIAGRAM|CXR\b|\bCR\b|\bDX\b|PLAIN FILM)", "XRAY"),
]
BODY = [
    "CHEST", "ABDOMEN", "PELVIS", "KUB", "SKULL", "BRAIN", "HEAD", "NECK", "PNS", "PARANASAL", "ORBIT", "MASTOID",
    "CERVICAL SPINE", "DORSAL SPINE", "THORACIC SPINE", "LUMBAR SPINE", "LUMBOSACRAL SPINE", "SACRUM", "COCCYX", "SPINE",
    "SHOULDER", "CLAVICLE", "SCAPULA", "HUMERUS", "ARM", "ELBOW", "FOREARM", "WRIST", "HAND", "FINGER", "THUMB",
    "HIP", "FEMUR", "THIGH", "KNEE", "LEG", "TIBIA", "FIBULA", "ANKLE", "FOOT", "HEEL", "CALCANEUM", "TOE",
    "THYROID", "BREAST", "SCROTUM", "TESTIS", "PROSTATE", "UTERUS", "OVARY", "OBSTETRIC", "PREGNANCY", "LIVER",
    "GALL BLADDER", "KIDNEY", "RENAL", "HEART", "CARDIAC", "SOFT TISSUE", "FACE", "NASAL", "MANDIBLE", "TMJ",
]
VIEW = r"\b(PA|AP|LAT(?:ERAL)?|OBLIQUE|AXIAL|LORDOTIC|DECUBITUS)\b"
HEADER_LINE = re.compile(
    r"\b(PATIENT\s*NAME|NAME|AGE|SEX|GENDER|UHID|MRN|REG(ISTRATION)?\s*NO|PATIENT\s*ID|P\.?\s*ID|HOSP(ITAL)?\s*NO|IP\s*NO|IPD|OP\s*NO|"
    r"ADM(ISSION)?\s*NO|BED|WARD|REF(ERRED)?\s*BY|REF\.?\s*DR|CONSULTANT|DATE|MOBILE|PHONE|PH\b|ADDRESS|ACCESSION|BILL\s*NO|"
    r"RECEIPT|VISIT\s*NO|LAB\s*NO|SAMPLE)\b", re.I)
SIGNATURE = re.compile(
    r"^\s*(DR\.?\s|DR\s|CONSULTANT\s*RADIOLOGIST|RADIOLOGIST|SONOLOGIST|REPORTED\s*BY|VERIFIED\s*BY|SIGNATURE|M\.?D\.?\b|DMRD|DNB|"
    r"MBBS|REG(\.|ISTRATION)?\s*NO|KMC|MCI|NMC|THANKS|THANK YOU|NOTE\s*:|DISCLAIMER|THIS REPORT|NOT VALID|E\.?\s*&?\s*O\.?\s*E)", re.I)
DOCTOR = re.compile(r"\bDR\.?[ \t]*([A-Z][A-Za-z.]*(?:[ \t]+[A-Z][A-Za-z.]*){0,3})", re.I)
STOP = {"DATE", "AGE", "SEX", "UHID", "MD", "MBBS", "DMRD", "DNB", "RADIOLOGIST", "CONSULTANT", "REG", "NO", "IP", "PH", "MOBILE", "KMC"}


def _doctor_names(text):
    out = set()
    for m in DOCTOR.finditer(text):
        toks = []
        for t in m.group(1).split():
            if t.upper().strip(".") in STOP:
                break
            toks.append(t)
        if toks and len("".join(toks)) > 2:
            out.add(" ".join(toks))
    return sorted(out)


def modality_of(text):
    u = str(text or "").upper()
    for rx, code in MODALITY:
        if re.search(rx, u):
            return code
    return ""


def body_of(text):
    u = " " + re.sub(r"[^A-Z ]", " ", str(text or "").upper()) + " "
    if re.search(r" (2D )?ECHO(CARDIOGRAPHY)? ", u):
        return "HEART"
    for b in BODY:
        if f" {b} " in u:
            return {"PARANASAL": "PNS", "THORACIC SPINE": "DORSAL SPINE", "RENAL": "KIDNEY", "CARDIAC": "HEART"}.get(b, b)
    return ""


def contrast_of(text, modality):
    u = str(text or "").upper()
    if re.search(r"\b(CECT|POST[- ]?CONTRAST|WITH CONTRAST|CONTRAST[- ]ENHANCED|IV CONTRAST|GADOLINIUM)\b", u):
        return "Yes"
    if re.search(r"\b(NCCT|PLAIN|NON[- ]?CONTRAST|WITHOUT CONTRAST)\b", u) or modality in ("XRAY", "CR", "DX", "MG", "US"):
        return "No"
    return "Unknown"


def status_of(text):
    u = str(text or "").upper()
    if re.search(r"\b(PRELIMINARY|PROVISIONAL|DRAFT|WET READ)\b", u):
        return "Preliminary"
    if re.search(r"\b(ADDENDUM|AMENDED|REVISED|CORRECTED)\b", u):
        return "Amended"
    return "Final"


def parse_date(v):
    from .pipeline import parse_date as pd_
    s = str(v or "").strip()
    if not s:
        return None
    d = pd_(s)
    if d:
        return d
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%d-%b-%y", "%d %b %y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _key(*parts):
    return "report:" + hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:20]


def _finish(r, xray_code):
    title = " ".join(x for x in (r.get("study", ""), r.get("text", "")[:300]) if x)
    mod = r.get("modality_raw") and modality_of(r["modality_raw"]) or modality_of(title)
    r["modality"] = xray_code if mod == "XRAY" else mod
    r["body_region"] = body_of(r.get("study", "")) or body_of(r.get("modality_raw", "")) or body_of(r.get("text", "")[:400])
    v = re.findall(VIEW, (r.get("study", "") + " " + r.get("text", "")[:300]).upper())
    r["view"] = "/".join(sorted({x[:3] if x.startswith("LAT") else x for x in v}))[:20]
    r["contrast"] = contrast_of(title, mod)
    if not r.get("status"):
        r["status"] = status_of(r.get("text", ""))
    else:
        s = r["status"].strip().title()
        r["status"] = s if s in ("Preliminary", "Final", "Amended") else status_of(s)
    r["people"] = _doctor_names(r.get("raw", "") + "\n" + r.get("text", "") + "\n" + r.get("doctor", ""))
    r["key"] = r.get("key") or _key(r["source"], r.get("pid"), r.get("adm"), r.get("name"), r.get("date"), r["modality"], r["body_region"], r.get("text", "")[:200])
    return r


# ----------------------------------------------------------------------- tables
TABLE_SYN = {
    "pid": ["uhid", "mrn", "patient id", "patientid", "reg no", "registration no", "hospital no", "hosp no", "pid", "uhid no", "mr no", "patient no", "op no", "regn no"],
    "adm": ["ip no", "ipno", "ip number", "admission no", "adm no", "admission number", "ipd no", "inpatient no", "encounter no", "visit no"],
    "name": ["patient name", "name", "patientname", "pt name", "name of patient"],
    "age": ["age", "age yrs", "age years", "patient age"],
    "sex": ["sex", "gender"],
    "age_sex": ["age sex", "age / sex", "age/sex", "sex age", "age gender"],
    "date": ["date", "study date", "exam date", "examination date", "report date", "scan date", "test date", "reported on", "date of study", "bill date", "order date"],
    "modality_raw": ["modality", "department", "dept", "investigation type", "test type", "category", "service type", "section"],
    "study": ["study", "study name", "examination", "exam", "test", "test name", "procedure", "investigation", "service", "service name", "study description", "body part", "region", "part"],
    "findings": ["findings", "report", "report text", "observation", "observations", "result", "details", "description", "narrative", "report details"],
    "impression": ["impression", "conclusion", "opinion", "diagnosis", "remarks", "summary"],
    "status": ["status", "report status"],
    "accession": ["accession", "accession no", "report no", "study id", "order no", "bill no"],
    "doctor": ["radiologist", "reported by", "referring doctor", "ref by", "referred by", "consultant", "doctor", "ref doctor"],
}


def _hn(h):
    return re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).strip()


def read_table(path, xray_code="CR"):
    out = []
    syn = {k: {_hn(x) for x in v} for k, v in TABLE_SYN.items()}
    for label, grid in ingest.read_grids(path):
        hrow, cols = None, {}
        for i, row in enumerate(grid[:25]):
            found = {}
            for j, c in enumerate(row):
                n = _hn(c)
                for f, names in syn.items():
                    if f not in found and n in names:
                        found[f] = j
            if ("findings" in found or "impression" in found) and len(found) >= 3:
                hrow, cols = i, found
                break
        if hrow is None:
            continue
        for k, row in enumerate(grid[hrow + 1:], start=hrow + 2):
            g = lambda f: str(row[cols[f]]).strip() if f in cols and cols[f] < len(row) and row[cols[f]] is not None else ""
            text = "\n".join(x for x in (g("findings"), ("IMPRESSION: " + g("impression")) if g("impression") else "") if x)
            if not text.strip():
                continue
            age, sex = g("age"), g("sex")
            if g("age_sex"):
                m = re.search(r"(\d{1,3})\s*\w*\s*[/,\s]\s*([MFmf])", g("age_sex"))
                if m:
                    age, sex = age or m.group(1), sex or m.group(2)
            r = {"source": f"{label} row {k}", "pid": g("pid"), "adm": g("adm"), "name": g("name"),
                 "age": _age(age), "sex": _sex(sex), "date": parse_date(g("date")), "modality_raw": g("modality_raw"),
                 "study": g("study"), "text": text, "status": g("status"), "raw": " ".join(str(c) for c in row if c),
                 "ids": [x for x in (g("pid"), g("adm"), g("accession")) if x], "doctor": g("doctor")}
            if g("accession"):
                r["key"] = _key("acc", g("accession"))
            out.append(_finish(r, xray_code))
    return out


def _age(v):
    m = re.search(r"(\d{1,3}(?:\.\d+)?)", str(v or ""))
    if not m:
        return None
    a = float(m.group(1))
    u = str(v).upper()
    if re.search(r"\bM(ONTHS?|THS?)?\b", u) and "Y" not in u:
        a /= 12
    elif re.search(r"\bD(AYS?)?\b", u) and "Y" not in u:
        a /= 365
    return a if 0 <= a < 125 else None


def _sex(v):
    s = str(v or "").strip().upper()
    return "Male" if s.startswith("M") else "Female" if s.startswith("F") else ""


# ----------------------------------------------------------------------- documents
def doc_text(path):
    p = Path(path)
    s = p.suffix.lower()
    if s == ".docx":
        import docx
        d = docx.Document(str(p))
        lines = [para.text for para in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                cells = []
                for c in row.cells:
                    if not cells or c.text != cells[-1]:
                        cells.append(c.text)
                lines.append("   ".join(cells))
        # header tables usually come first in reports; keep paragraph order but put tables up front if they hold the patient block
        tbl = [l for l in lines[len(d.paragraphs):] if HEADER_LINE.search(l)]
        body = lines[:len(d.paragraphs)]
        return "\n".join(tbl + body + [l for l in lines[len(d.paragraphs):] if l not in tbl])
    if s == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(p)) as pdf:
            return "\n<<PAGE>>\n".join((pg.extract_text() or "") for pg in pdf.pages)
    if s in (".txt", ".rtf"):
        return p.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"unsupported report file type {s}")


FIELD = {
    "name": r"(?:PATIENT'?S?\s*NAME|PT\.?\s*NAME|NAME)\s*[:\-.]+\s*([A-Za-z][A-Za-z .']{1,60}?)(?=\s{2,}|\s+(?:AGE|SEX|GENDER|UHID|MRN|REG|ID|DATE|IP)\b|\s*[|,]|$)",
    "age_sex": r"AGE\s*(?:/|&|AND)\s*(?:SEX|GENDER)\s*[:\-.]*\s*(\d{1,3})\s*(?:Y(?:RS?|EARS?)?|M(?:ONTHS?)?|D(?:AYS?)?)?\s*[/,\s]+\s*(M(?:ALE)?|F(?:EMALE)?)\b",
    "sex_age": r"(?:SEX|GENDER)\s*/\s*AGE\s*[:\-.]*\s*(M(?:ALE)?|F(?:EMALE)?)\s*[/,\s]+\s*(\d{1,3})",
    "age": r"\bAGE\s*[:\-.]+\s*(\d{1,3}(?:\s*(?:Y(?:RS?|EARS?)?|M(?:ONTHS?)?|D(?:AYS?)?))?)",
    "sex": r"\b(?:SEX|GENDER)\s*[:\-.]+\s*(M(?:ALE)?|F(?:EMALE)?)\b",
    "pid": r"\b(?:UHID|MRN|MR\s*NO|REG(?:ISTRATION|N)?\.?\s*NO|PATIENT\s*ID|P\.?\s*ID|HOSP(?:ITAL)?\.?\s*NO|OP\s*NO)\.?\s*[:\-.#]*\s*([A-Z0-9][A-Z0-9/\-]{2,})",
    "adm": r"\b(?:IP\s*NO|IPD\s*NO|ADM(?:ISSION)?\.?\s*NO|IP\s*NUMBER)\.?\s*[:\-.#]*\s*([A-Z0-9][A-Z0-9/\-]{2,})",
    "date": r"\b(?:STUDY\s*DATE|EXAM(?:INATION)?\s*DATE|REPORT(?:ED)?\s*(?:DATE|ON)|SCAN\s*DATE|DATE)\s*(?:&\s*TIME)?\s*[:\-.]*\s*" + DATE_RX,
    "doctor": r"\b(?:REF(?:ERRED)?\.?\s*BY|REF\.?\s*DR|CONSULTANT|REFERRING\s*(?:DOCTOR|PHYSICIAN))\s*[:\-.]*\s*((?:DR\.?\s*)?[A-Za-z][A-Za-z .]{2,50}?)(?=\s{2,}|\s+(?:DATE|AGE|SEX|UHID)\b|$)",
}
TITLE = re.compile(r"^\s*(?:(?:REPORT\s*(?:OF|ON)?\s*[:\-]?\s*)?(?:2D\s*ECHO|ECHO(?:CARDIOGRAPHY)?|X[\s-]?RAY|XRAY|RADIOGRAPH|SKIAGRAM|USG|ULTRASOUND|ULTRASONOGRAPHY|SONOGRAPHY|CT|CECT|NCCT|HRCT|MRI|MR\b|DOPPLER|MAMMO)\b.*)$", re.I)
SECTION = re.compile(r"^\s*(FINDINGS?|OBSERVATIONS?|REPORT|IMPRESSION|CONCLUSION|OPINION|TECHNIQUE|CLINICAL\s*(?:HISTORY|DETAILS|INFORMATION)|HISTORY|INDICATION|COMPARISON)\s*[:\-]?", re.I)


def _split_reports(text):
    """A document may contain several reports. Split where a new patient header starts after report text."""
    chunks, cur, seen_body = [], [], False
    for line in text.replace("<<PAGE>>", "\n").splitlines():
        is_name = re.search(r"\b(PATIENT'?S?\s*NAME|NAME)\s*[:\-.]", line, re.I)
        if is_name and seen_body and cur:
            chunks.append("\n".join(cur))
            cur, seen_body = [], False
        cur.append(line)
        if line.strip() and not HEADER_LINE.search(line) and len(line.strip()) > 25:
            seen_body = True
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


def parse_document_text(text, source, xray_code="CR"):
    out = []
    for n, chunk in enumerate(_split_reports(text), 1):
        flat = re.sub(r"[ \t]+", " ", chunk)
        f = {k: (re.search(rx, flat, re.I | re.M) or None) for k, rx in FIELD.items()}
        age = sex = ""
        if f["age_sex"]:
            age, sex = f["age_sex"].group(1), f["age_sex"].group(2)
        elif f["sex_age"]:
            sex, age = f["sex_age"].group(1), f["sex_age"].group(2)
        age = age or (f["age"].group(1) if f["age"] else "")
        sex = sex or (f["sex"].group(1) if f["sex"] else "")
        lines = [l.rstrip() for l in chunk.splitlines()]
        title, body, started = "", [], False
        for l in lines:
            s = l.strip()
            if not s:
                if started:
                    body.append("")
                continue
            if not started:
                if TITLE.match(s) and not HEADER_LINE.search(s.split(":")[0]):
                    title, started = s, True
                    continue
                if SECTION.match(s):
                    started = True
                elif HEADER_LINE.search(s) or len(s) < 4:
                    continue
                else:
                    started = True
            if SIGNATURE.match(s):
                break
            body.append(s)
        text_out = re.sub(r"\n{3,}", "\n\n", "\n".join(body)).strip()
        if not text_out:
            continue
        r = {"source": f"{Path(source).name}" + (f" #{n}" if n > 1 else ""), "pid": f["pid"].group(1) if f["pid"] else "",
             "adm": f["adm"].group(1) if f["adm"] else "", "name": f["name"].group(1).strip() if f["name"] else "",
             "age": _age(age), "sex": _sex(sex), "date": parse_date(f["date"].group(1)) if f["date"] else None,
             "modality_raw": "", "study": title, "text": text_out, "status": "", "raw": chunk,
             "ids": [x.group(1) for x in (f["pid"], f["adm"]) if x], "doctor": f["doctor"].group(1) if f["doctor"] else ""}
        out.append(_finish(r, xray_code))
    return out


REPORT_DOC_TYPES = (".docx", ".pdf", ".txt")
REPORT_TABLE_TYPES = (".xlsx", ".xls", ".xlsm", ".csv")


def read_reports(paths, xray_code="CR", log=print):
    """paths: files and/or folders. Returns (reports, problems)."""
    reports, problems = [], []
    files = []
    for p in paths or []:
        p = Path(p)
        if p.is_dir():
            files += sorted(x for x in p.rglob("*") if x.is_file() and x.suffix.lower() in REPORT_DOC_TYPES + REPORT_TABLE_TYPES + (".doc",))
        elif p.exists():
            files.append(p)
        else:
            problems.append(f"Radiology path not found: {p}")
    for f in files:
        s = f.suffix.lower()
        try:
            if s in REPORT_TABLE_TYPES:
                got = read_table(f, xray_code)
            elif s in REPORT_DOC_TYPES:
                got = parse_document_text(doc_text(f), f, xray_code)
            elif s == ".doc":
                problems.append(f"{f.name}: old Word .doc format; save it as .docx or PDF and run again")
                continue
            else:
                continue
            if not got:
                problems.append(f"{f.name}: no report text recognised")
            reports += got
        except Exception as e:
            problems.append(f"{f.name}: could not be read ({e})")
    # remove exact duplicates (same patient, date, study and text)
    seen, uniq = set(), []
    for r in reports:
        sig = (r.get("pid"), r.get("name"), r.get("date"), r["modality"], r["body_region"], r["text"][:300])
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(r)
    if len(uniq) < len(reports):
        problems.append(f"{len(reports) - len(uniq)} duplicate report(s) removed")
    log(f"{len(uniq)} radiology report(s) read from {len(files)} file(s)")
    return uniq, problems
