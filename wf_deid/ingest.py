"""Reads whatever the hospital system exports and turns it into one record per admission.

Accepted shapes (detected automatically, sheet by sheet):
  1. List / register layout: one row per admission, any column names (recognised via data/header_synonyms.yaml).
  2. Discharge-summary layout: the Keystone discharge summary saved as Excel/CSV (labels such as
     "Patient Name :", section headings such as "Brief History", "Investigations"...). One or many
     summaries per sheet.
  3. PDF printouts of the discharge summary (optional, needs pdfplumber).
"""
import re
from pathlib import Path

import pandas as pd
import yaml

from .paths import base

APP = base()
DATA = APP / "data"

IDENTIFIER_FIELDS = {"patient_name", "uhid", "abha", "dob", "admission_no", "bed", "address", "phone",
                     "consultant", "consultant_mobile"}
IDENTIFIER_HEADER = re.compile(
    r"name|phone|mobile|contact|address|addr|aadha|aadhar|adhar|abha|pan\b|email|relative|guardian|attendant|"
    r"father|mother|husband|wife|spouse|son of|kin|policy|insurance no|card no|tpa|mlc|police|signature|"
    r"employee|emp id|passport|voter|ration", re.I)

SECTIONS = [  # (key, heading patterns) in the Keystone discharge-summary layout
    ("complaints", r"^(BRIEF HISTORY|HISTORY|CHIEF COMPLAINTS?|PRESENTING COMPLAINTS?)\b"),
    ("exam", r"^(CLINICAL EXAMINATION|EXAMINATION|ON EXAMINATION)\b"),
    ("investigations", r"^(INVESTIGATIONS?)\b"),
    ("treatment_given", r"^(TREATMENT GIVEN|COURSE IN HOSPITAL|HOSPITAL COURSE|TREATMENT)\b"),
    ("diagnosis", r"^(DIAGNOSIS DETAILS|FINAL DIAGNOSIS|DIAGNOSIS)\b"),
    ("consultant", r"^(CONSULTANT DETAILS)\b"),
    ("advice", r"^(ADVICE ON DISCHARGE|ADVICE|DISCHARGE ADVICE)\b"),
    ("medication", r"^(MEDICATION ?/ ?PR[IE]?SCRIPTION|MEDICATION|DISCHARGE MEDICATION|PRESCRIPTION)\b"),
    ("xray_report", r"^(RADIOLOGY|X ?RAY REPORT|IMAGING REPORT|IMPRESSION)\b"),
    ("_end", r"^(SIGNATURE OF DOCTOR|SIGNATURE)\b"),
]
DATE = r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2})"


def hnorm(h):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(h).lower()).split())


def load_synonyms():
    syn = yaml.safe_load((DATA / "header_synonyms.yaml").read_text(encoding="utf-8"))
    return {f: [hnorm(x) for x in v] for f, v in syn.items()}


def load_overrides(path=None):
    p = Path(path) if path else APP / "config" / "column_overrides.yaml"
    if p.exists():
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return {hnorm(k): v for k, v in d.items()}
    return {}


def match_header(h, syn, overrides):
    n = hnorm(h)
    if not n:
        return None, ""
    if n in overrides:
        return overrides[n], "settings override"
    for f, names in syn.items():
        if n in names:
            return f, "exact name"
    best, blen = None, 0
    for f, names in syn.items():
        for s in names:
            if len(s) >= 4 and re.search(rf"\b{re.escape(s)}\b", n) and len(s) > blen:
                best, blen = f, len(s)
    return (best, "partial name") if best else (None, "")


# ======================================================================= file loading
def read_grids(path):
    """Return list of (sheet_label, grid) where grid is a list of rows of cell strings."""
    path = Path(path)
    suf = path.suffix.lower()
    out = []
    if suf in (".xlsx", ".xlsm", ".xls"):
        for name, df in pd.read_excel(path, header=None, dtype=object, sheet_name=None).items():
            out.append((f"{path.name}:{name}", _df_to_grid(df)))
    elif suf in (".csv", ".txt"):
        df = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                df = pd.read_csv(path, header=None, dtype=object, encoding=enc, keep_default_na=False,
                                 sep=None, engine="python")
                break
            except UnicodeDecodeError:
                continue
        out.append((path.name, _df_to_grid(df)))
    elif suf == ".pdf":
        out.append((path.name, _pdf_to_grid(path)))
    else:
        raise ValueError(f"Unsupported file type: {path.name}")
    return out


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%d/%m/%Y")
    s = str(v).strip()
    if s.lower() in ("nan", "nat", "none"):
        return ""
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s


def _df_to_grid(df):
    return [[_cell(v) for v in row] for row in df.itertuples(index=False)]


def _pdf_to_grid(path):
    import pdfplumber
    grid = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text(layout=True) or "").splitlines():
                cells = [c.strip() for c in re.split(r"\s{2,}", line) if c.strip()]
                if cells:
                    grid.append(cells)
            grid.append(["<<PAGE>>"])
    return grid


# ======================================================================= layout detection
def detect(grid, syn, overrides):
    """Return ('table', header_row_index) or ('summary', None) or ('unknown', None)."""
    best_i, best_n = None, 0
    for i, row in enumerate(grid[:25]):
        n = sum(1 for c in row if c and match_header(c, syn, overrides)[0])
        filled = sum(1 for c in row if c)
        if n >= 3 and n >= 0.5 * filled and n > best_n:
            best_i, best_n = i, n
    text = " ".join(" ".join(r) for r in grid[:200]).upper()
    labels = sum(1 for k in ("PATIENT NAME", "ADMISSION NO", "BRIEF HISTORY", "INVESTIGATIONS", "TREATMENT GIVEN",
                             "DIAGNOSIS", "DISCHARGE SUMMARY", "DATE OF DISCHARGE") if k in text)
    if best_i is not None and (best_n >= 5 or (best_n >= 3 and labels < 4)):
        return "table", best_i
    if labels >= 3:
        return "summary", None
    if best_i is not None:
        return "table", best_i
    return "unknown", None


# ======================================================================= list / register layout
def parse_table(grid, hrow, syn, overrides, source):
    headers = grid[hrow]
    mapping, used = [], {}
    for j, h in enumerate(headers):
        if not h:
            continue
        f, how = match_header(h, syn, overrides)
        if f == "ignore":
            mapping.append({"source": source, "column": h, "field": "", "how": "ignored by settings"})
            continue
        if f and f not in used:
            used[f] = j
            mapping.append({"source": source, "column": h, "field": f, "how": how})
        else:
            ident = bool(IDENTIFIER_HEADER.search(h))
            mapping.append({"source": source, "column": h, "field": "",
                            "how": "identifier - removed" if ident else
                            ("duplicate of " + f if f else "not recognised - not released")})
            used.setdefault(f"_extra_{j}", j)
    records = []
    for row in grid[hrow + 1:]:
        if not any(row):
            continue
        rec = {"_source": source}
        for f, j in used.items():
            rec[f] = row[j] if j < len(row) else ""
        extras = [row[j] for f, j in used.items() if f.startswith("_extra_") and j < len(row) and row[j]
                  and IDENTIFIER_HEADER.search(headers[j] or "")]
        rec["_identifier_values"] = [v for v in extras]
        rec = {k: v for k, v in rec.items() if not k.startswith("_extra_")}
        if any(rec.get(k) for k in ("patient_name", "uhid", "admission_no", "admission_date")):
            records.append(rec)
    return records, mapping


# ======================================================================= discharge-summary layout
def _lines(grid):
    out = []
    for row in grid:
        cells = [c for c in row if c and c != "<<PAGE>>"]
        if cells:
            l = " | ".join(cells)
            l = re.sub(r"(?<=[A-Za-z]):(?=[a-z])", "", l)      # PDF text artefacts such as "F:emale"
            l = re.sub(r"(?<=\d):(?=[/.-]\d)", "", l)          # and "13:/11/26"
            out.append(l)
    return out


def _split_docs(lines):
    starts = [i for i, l in enumerate(lines) if re.search(r"PATIENT\s*NAME", l, re.I)]
    if not starts:
        return [lines]
    docs = []
    for k, s in enumerate(starts):
        b = s - 1 if s > 0 and re.search(r"DISCHARGE[\s|]+SUMMARY", lines[s - 1], re.I) else s
        e = starts[k + 1] - (1 if k + 1 < len(starts) and starts[k + 1] > 0 and
                             re.search(r"DISCHARGE[\s|]+SUMMARY", lines[starts[k + 1] - 1], re.I) else 0) \
            if k + 1 < len(starts) else len(lines)
        docs.append(lines[b:e])
    return docs


def _g(rx, text, grp=1):
    m = re.search(rx, text, re.I)
    return m.group(grp).strip(" :|") if m else ""


def _clean_item(l):
    l = re.sub(r"^\s*[-•*]+\s*", "", l)
    l = re.sub(r"^C\s*/\s*O\b\s*", "", l, flags=re.I)
    return l.strip(" |")


def parse_summary_doc(lines, source):
    head = "\n".join(lines[:12])
    flat = "\n".join(lines)
    rec = {"_source": source, "_layout": "discharge summary"}
    rec["patient_name"] = _g(r"PATIENT\s*NAME[\s:|]*([^|\n]+?)(?=\s*(\||G?ENDER\b|\n|$))", head)
    rec["uhid"] = _g(r"\b(?:UHID|MRN|MR\s*NO|UMR\s*NO|REG(?:ISTRATION)?\.?\s*NO|HOSP(?:ITAL)?\s*NO)\.?[\s:|]*([A-Z0-9][A-Z0-9/\-]+)", head)
    rec["admission_no"] = _g(r"ADMISSION\s*NO\.?[\s:|]*([A-Z0-9][A-Z0-9/\-]+)", head)
    rec["sex"] = _g(r"\b(FEMALE|MALE|TRANSGENDER)\b", head)
    rec["age"] = _g(r"(\d{1,3}(?:\.\d)?)\s*(?:YEARS?|YRS?|Y)\b", head)
    rec["admission_date"] = _g(r"ADMISSION\s*DATE[\s:|]*" + DATE, head)
    rec["discharge_date"] = _g(r"(?:DATE\s*OF\s*DISCHARGE|DISCHARGE\s*DATE)[\s:|]*" + DATE, head)
    rec["ward"] = _g(r"\bWARD[\s:|]*([^,|\n]+)", head)
    rec["bed"] = _g(r"BED\s*NO\.?[\s:|]*([A-Z0-9-]+)", head)
    rec["pin"] = _g(r"(?:PIN\s*(?:CODE)?)[\s:|]*(\d{6})", flat)
    rec["phone"] = _g(r"(?:PATIENT\s*)?(?:MOBILE|PHONE|CONTACT)\s*(?:NO)?[\s:|]*(\+?\d[\d\s-]{8,})", head)
    rec["payer"] = _g(r"(?:PAYER|PATIENT\s*TYPE|CATEGORY|SPONSOR|SCHEME)[\s:|]*([^|\n]+)", head)

    # sections
    sec_idx = []
    for i, l in enumerate(lines):
        first = l.split("|")[0].strip().upper()
        for key, rx in SECTIONS:
            if re.match(rx, first) and len(first) < 40:
                sec_idx.append((i, key))
                break
    body = {}
    for k, (i, key) in enumerate(sec_idx):
        end = sec_idx[k + 1][0] if k + 1 < len(sec_idx) else len(lines)
        rest = lines[i].split("|", 1)[1] if "|" in lines[i] else ""
        content = ([rest] if rest.strip() else []) + lines[i + 1:end]
        body.setdefault(key, []).extend(content)

    rec["complaints"] = "; ".join(x for x in (_clean_item(l) for l in body.get("complaints", [])) if x)
    rec["investigations"] = "; ".join(x for x in (_clean_item(l) for l in body.get("investigations", [])) if x)
    rec["treatment_given"] = "; ".join(x for x in (_clean_item(l) for l in body.get("treatment_given", [])) if x)

    # clinical examination: vitals header row followed by a values row, then "PA : ..." lines
    ex = body.get("exam", [])
    for k, l in enumerate(ex):
        u = l.upper()
        if "TEMP" in u and "BP" in u and k + 1 < len(ex):
            vals = [c.strip() for c in ex[k + 1].split("|")]
            heads = [c.strip().upper() for c in l.split("|")]
            for h, v in zip(heads, vals):
                if "TEMP" in h:
                    rec["temp"] = v
                elif "BP" in h:
                    rec["bp"] = v
                elif "HR" in h or "PULSE" in h:
                    rec["pulse"] = v
                elif "SPO2" in h:
                    rec["spo2"] = v
                elif "RR" in h:
                    rec["rr"] = v
            if not rec.get("bp"):
                m = re.search(r"(\d{2,3})\s*\|?\s*(\d{2,3}/\d{2,3})\s*\|?\s*(\d{2,3})", ex[k + 1])
                if m:
                    rec["temp"], rec["bp"], rec["pulse"] = m.groups()
    other = []
    for l in ex:
        m = re.match(r"\s*(PA|P/A|RS|R/S|CVS|CNS)\s*[:|]+\s*(.+)", l, re.I)
        if m:
            key = {"PA": "exam_pa", "P/A": "exam_pa", "RS": "exam_rs", "R/S": "exam_rs", "CVS": "exam_cvs",
                   "CNS": "exam_cns"}[m.group(1).upper()]
            rec[key] = m.group(2).strip(" |")
        elif not re.search(r"TEMP|BP|\d{2,3}/\d{2,3}", l, re.I):
            other.append(_clean_item(l))
    rec["exam_other"] = "; ".join(x for x in other if x)

    # diagnosis: an optional year line, then text
    dx_lines, year = [], ""
    for l in body.get("diagnosis", []):
        t = _clean_item(l)
        if re.fullmatch(r"(19|20)\d{2}", t):
            year = t
        elif t:
            dx_lines.append(re.sub(r"^CASE OF\s+", "", t, flags=re.I))
    rec["diagnosis"] = "; ".join(dx_lines)
    rec["diagnosis_year"] = year

    # consultant and surgery tables
    con = body.get("consultant", [])
    for k, l in enumerate(con):
        u = l.upper()
        if "CONSULTANT" in u and ("MOBILE" in u or "TYPE" in u) and k + 1 < len(con):
            cells = [c.strip() for c in con[k + 1].split("|")]
            if cells and not re.search(r"SURGERY", cells[0], re.I):
                rec["consultant"] = cells[0]
                ph = [c for c in cells if re.fullmatch(r"\+?\d[\d\s-]{8,}", c)]
                rec["consultant_mobile"] = ph[0] if ph else ""
                rest = [c for c in cells[1:] if c not in ph]
                rec["consultant_type"] = rest[0] if rest else ""
        if "SURGERY" in u and "NAME" in u and k + 1 < len(con):
            cells = [c.strip() for c in con[k + 1].split("|")]
            if cells and not re.search(r"ADVICE|MEDICATION", cells[0], re.I):
                rec["surgery_name"] = cells[0]
                dd = [c for c in cells if re.fullmatch(DATE, c)]
                rec["surgery_date"] = dd[0] if dd else ""
                rec["surgery_notes"] = " ".join(c for c in cells[1:] if c not in dd)

    # discharge medication table
    meds = []
    rows = body.get("medication", []) + [l for l in body.get("advice", []) if re.match(r"^(TAB|CAP|SYP|INJ|POWDER|DROPS|OINT|CREAM|NEB|INH)", l, re.I)]
    for l in rows:
        if re.search(r"MEDICATION\s*NAME|STRENGTH|DOSAGE|NO_?OF_?DAYS", l, re.I):
            continue
        cells = [c.strip() for c in l.split("|") if c.strip()]
        if not cells:
            continue
        name = cells[0]
        dose = next((c for c in cells[1:] if re.fullmatch(r"\d(?:/\d)?-\d(?:/\d)?-\d(?:/\d)?(?:-\d)?", c)), "")
        days = next((re.search(r"\d+", c).group(0) for c in cells[1:] if re.search(r"\d+\s*DAYS?", c, re.I)), "")
        strength = next((c for c in cells[1:] if c not in (dose,) and re.fullmatch(r"\d+(\.\d+)?\s*(MG|MCG|ML|G|GM|IU)?", c, re.I)), "")
        meds.append({"name": name, "strength": strength, "dosage": dose, "days": days})
    rec["discharge_medication_list"] = meds
    rec["discharge_medication"] = "; ".join(" ".join(x for x in (m["name"], m["strength"], m["dosage"],
                                                                 (m["days"] + " DAYS") if m["days"] else "") if x) for m in meds)
    adv = [_clean_item(l) for l in body.get("advice", []) if not re.match(r"^(TAB|CAP|SYP|INJ|POWDER|MEDICATION)", l, re.I)]
    rec["advice"] = "; ".join(x for x in adv if x)
    rec["xray_report"] = " ".join(_clean_item(l) for l in body.get("xray_report", []))
    rec["_identifier_values"] = []
    return rec


def parse_summary(grid, source):
    docs = _split_docs(_lines(grid))
    recs = [parse_summary_doc(d, source) for d in docs]
    recs = [r for r in recs if r.get("patient_name") or r.get("admission_no")]
    mapping = [{"source": source, "column": "(discharge-summary layout)", "field": "all sections",
                "how": f"{len(recs)} summary document(s) read"}]
    return recs, mapping


# ======================================================================= entry point
def ingest(paths, overrides_path=None):
    syn = load_synonyms()
    ov = load_overrides(overrides_path)
    records, mapping, notes = [], [], []
    for p in paths:
        for label, grid in read_grids(p):
            kind, hrow = detect(grid, syn, ov)
            if kind == "table":
                r, m = parse_table(grid, hrow, syn, ov, label)
            elif kind == "summary":
                r, m = parse_summary(grid, label)
            else:
                notes.append(f"{label}: layout not recognised, skipped")
                continue
            records += r
            mapping += m
            notes.append(f"{label}: read as {'list of admissions' if kind == 'table' else 'discharge-summary layout'}, {len(r)} admission(s)")
    return records, mapping, notes


def read_lab_file(path, overrides_path=None):
    """Optional lab-results export: one row per test result."""
    syn = {
        "uhid": load_synonyms()["uhid"], "admission_no": load_synonyms()["admission_no"],
        "test": [hnorm(x) for x in ["test", "test name", "investigation", "parameter", "service name", "test description"]],
        "value": [hnorm(x) for x in ["result", "value", "result value", "observed value"]],
        "unit": [hnorm(x) for x in ["unit", "units", "uom"]],
        "date": [hnorm(x) for x in ["date", "result date", "report date", "sample date", "collection date"]],
        "status": [hnorm(x) for x in ["status", "result status", "report status"]],
        "patient_name": load_synonyms()["patient_name"],
    }
    out = []
    for label, grid in read_grids(path):
        hrow, cols = None, {}
        for i, row in enumerate(grid[:20]):
            found = {}
            for j, c in enumerate(row):
                n = hnorm(c)
                for f, names in syn.items():
                    if n in names and f not in found:
                        found[f] = j
            if "test" in found and "value" in found:
                hrow, cols = i, found
                break
        if hrow is None:
            continue
        for row in grid[hrow + 1:]:
            if not any(row):
                continue
            out.append({f: (row[j] if j < len(row) else "") for f, j in cols.items()})
    return out
