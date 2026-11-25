"""Workframe Health De-ID 2.1: hospital export + radiology reports + X-rays (all optional except the export) in,
the receiving team's anonymised dataset out.

Order of work (as required):
  1. read the hospital export (any layout) and the X-ray folder
  2. link each X-ray to its patient and admission (patient number first, then name/sex/age/date)
  3. de-identify everything
  4. code and arrange it exactly as the Technical Data Provision Reference asks (Indian standards)
  5. generate documentation, run the 13-point pre-transfer checklist, and package with SHA-256

Command line:
  python -m wf_deid.pipeline --input export.xlsx --xrays C:\\exports\\xray --out C:\\deid_out --password "..."
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import uuid
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import yaml

from . import docs, fhir, ingest, pdfdoc, radiology, spec, xray
from .coding import Coder, norm
from .linker import link, link_reports
from .textscrub import Scrubber
from .vault import Vault, VaultError

from .paths import base

APP = base()
VERSION = "2.1"
NAME_FIELD = re.compile(r"name|consultant|doctor|physician|relative|attendant|guardian|referred", re.I)


class StopRun(Exception):
    pass


def load_settings(path=None):
    p = Path(path) if path else APP / "config" / "settings.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def parse_date(v, dayfirst=True):
    s = str(v or "").strip()
    if not s:
        return None
    s = re.split(r"\s+", s)[0]
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=dayfirst).date()
    except Exception:
        return None


def parse_age(v):
    m = re.search(r"(\d{1,3}(?:\.\d+)?)", str(v or ""))
    if not m:
        return None
    a = float(m.group(1))
    if re.search(r"MONTH|MTH|\bM\b", str(v).upper()) and a < 24:
        a = a / 12
    elif re.search(r"DAY|\bD\b", str(v).upper()) and a < 60:
        a = a / 365
    return a if 0 <= a < 125 else None


def month(d):
    return d.strftime("%Y-%m") if d else ""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split_list(v):
    return [x.strip(" -•*") for x in re.split(r"[;\n|]+", str(v or "")) if x.strip(" -•*") and x.strip().upper() != "WITH"]


VITAL_RX = {
    "temp": r"\bTEMP(?:ERATURE)?\s*[:=-]?\s*(\d{2,3}(?:\.\d)?)",
    "bp": r"\bBP\s*[:=-]?\s*(\d{2,3}\s*/\s*\d{2,3})",
    "pulse": r"\b(?:HR|PR|PULSE|HEART RATE)\s*[:=-]?\s*(\d{2,3})",
    "spo2": r"\bSPO2\s*[:=-]?\s*(\d{2,3})",
    "rr": r"\b(?:RR|RESP(?:IRATORY)? RATE)\s*[:=-]?\s*(\d{1,2})",
    "weight": r"\b(?:WT|WEIGHT)\s*[:=-]?\s*(\d{1,3}(?:\.\d)?)",
}


# ======================================================================= normalisation
def normalise(r, coder, cfg):
    g = lambda k: str(r.get(k, "") or "").strip()
    a = {"raw": r}
    a["name"], a["uhid"] = g("patient_name"), g("uhid")
    a["sex"] = coder.sex(g("sex"))
    admit, disch = parse_date(g("admission_date"), cfg.get("dayfirst", True)), parse_date(g("discharge_date"), cfg.get("dayfirst", True))
    if disch and admit and disch < admit:
        disch = None
    a["admit"], a["discharge"] = admit, disch
    age = parse_age(g("age"))
    if age is None and g("dob"):
        b = parse_date(g("dob"))
        if b and (admit or disch):
            age = ((admit or disch) - b).days / 365.25
    a["age"] = age
    a["los"] = (disch - admit).days if admit and disch else ""
    et = norm(g("encounter_type"))
    if re.search(r"\bOP|OUT ?PATIENT|OPD", et):
        a["enc_type"] = "Outpatient"
    elif re.search(r"EMERG|CASUALTY|\bER\b", et):
        a["enc_type"] = "Emergency"
    elif re.search(r"DAY ?CARE", et):
        a["enc_type"] = "Day care"
    else:
        a["enc_type"] = "Inpatient"
    a["ward_type"] = coder.ward_type(g("ward"))
    a["region"] = coder.region(g("pin"))
    a["payer"] = coder.payer(g("payer"))
    paren = " ".join(re.findall(r"\(([^)]+)\)", g("consultant")))
    a["spec_code"], a["spec_name"] = coder.specialty(g("department"), paren, g("consultant_type"))

    # diagnoses
    code, desc = coder.icd_from_code(g("icd_code"))
    if code:
        a["dx"], a["dx_unmapped"], a["dx_source"] = [(code, desc or "")], "", "coded by hospital"
    else:
        a["dx"], a["dx_unmapped"] = coder.diagnoses(g("diagnosis"))
        a["dx_source"] = "mapped from text"
    yr = re.search(r"(19|20)\d{2}", g("diagnosis_year"))
    a["years_since_dx"] = (admit.year - int(yr.group(0))) if (yr and admit and admit.year >= int(yr.group(0))) else ""

    # procedures
    a["procs"], a["proc_unmapped"] = [], []
    pdate = parse_date(g("surgery_date")) or admit
    for s in split_list(g("surgery_name")):
        p = coder.procedure(s)
        (a["procs"].append((p[0], p[1], month(pdate))) if p else a["proc_unmapped"].append(s))

    # medicines
    a["meds"], a["med_unmapped"] = [], []
    for item in coder.split_medicines(g("treatment_given")):
        m = coder.medicine(item)
        m["context"], m["dose"], m["days"] = "Inpatient administered", "", ""
        (a["meds"] if m["mapped"] else a["med_unmapped"]).append(m)
    dl = r.get("discharge_medication_list") or []
    if not dl:
        for item in split_list(g("discharge_medication")):
            dose = re.search(r"\b\d(?:/\d)?-\d(?:/\d)?-\d(?:/\d)?(?:-\d)?\b", item)
            days = re.search(r"(\d+)\s*DAYS?", item, re.I)
            name = re.split(r"\s+\d(?:/\d)?-\d", item)[0]
            dl.append({"name": name.strip(), "strength": "", "dosage": dose.group(0) if dose else "", "days": days.group(1) if days else ""})
    a["discharge_meds_table"] = dl
    for d in dl:
        m = coder.medicine(f"{d['name']} {d.get('strength', '')}")
        if d.get("strength"):
            m["strength"] = d["strength"]
        m["context"], m["dose"], m["days"] = "Discharge prescription", d.get("dosage", ""), d.get("days", "")
        (a["meds"] if m["mapped"] else a["med_unmapped"]).append(m)

    # vitals
    exam_text = " ; ".join(g(k) for k in ("exam_other", "exam_pa", "exam_rs", "exam_cvs", "exam_cns"))
    v = {k: g(k) for k in ("temp", "bp", "pulse", "spo2", "rr", "weight")}
    for k, rx in VITAL_RX.items():
        if not v.get(k):
            m = re.search(rx, exam_text, re.I)
            if m:
                v[k] = m.group(1)
    a["vitals_raw"] = v

    # investigations ordered
    a["tests"], a["test_unmapped"] = [], []
    for t in split_list(g("investigations")):
        o = coder.observation(t)
        (a["tests"].append(o) if o else a["test_unmapped"].append(t))

    a["xray_report"], a["xray_report_status"] = g("xray_report"), g("xray_report_status")
    a["doc_text"] = {
        "complaints": [re.sub(r"^C\s*/\s*O\b[\s:.-]*", "", x, flags=re.I) for x in split_list(g("complaints"))],
        "exam": [f"{lbl}: {g(k)}" for lbl, k in (("PA", "exam_pa"), ("RS", "exam_rs"), ("CVS", "exam_cvs"), ("CNS", "exam_cns")) if g(k)]
                + [x for x in split_list(g("exam_other")) if not re.search(r"\b(TEMP|BP|HR|PULSE|SPO2)\b\s*[:=-]?\s*\d", x, re.I)
                   and not re.match(r"\s*(PA|P/A|RS|R/S|CVS|CNS)\b", x, re.I)]
                + [f"{lbl}: {m.group(2).strip()}" for x in split_list(g("exam_other"))
                   for m in [re.match(r"\s*(PA|P/A|RS|R/S|CVS|CNS)\b[\s:.-]*(.+)", x, re.I)] if m
                   for lbl in [m.group(1).upper().replace("/", "")] if not g("exam_" + lbl.lower())],
        "investigations": split_list(g("investigations")),
        "treatment": coder.split_medicines(g("treatment_given")),
        "diagnosis": split_list(g("diagnosis")),
        "surgery": [x for x in (g("surgery_name"), g("surgery_notes")) if x],
        "advice": split_list(g("advice")),
    }
    # identifiers that must never appear in the output
    a["identifiers"] = [x for x in [g("uhid"), g("abha"), g("admission_no"), g("phone"), g("consultant_mobile"), g("address"), g("bed") if len(g("bed")) > 2 else ""]
                        + list(r.get("_identifier_values", [])) if x]
    a["names"] = [x for x in [g("patient_name"), re.sub(r"\([^)]*\)", "", g("consultant"))] if x]
    birth_year = int(admit.year - age) if (admit and age is not None) else ""
    a["pkey"] = a["uhid"] or f"{norm(a['name'])}|{a['sex']}|{birth_year}"
    a["ekey"] = g("admission_no") or f"{a['pkey']}|{admit}"
    a["source"] = r.get("_source", "")
    return a


def vital_obs(a, cfg):
    """Vitals as LOINC/UCUM observations; implausible values dropped and counted."""
    pl = cfg.get("vitals_plausible", {})
    v, out, dropped = a["vitals_raw"], [], []

    def ok(val, key):
        lo, hi = pl.get(key, [-1e9, 1e9])
        return lo <= val <= hi

    def num(s):
        m = re.search(r"\d+(\.\d+)?", str(s))
        return float(m.group(0)) if m else None

    t = num(v.get("temp"))
    if t is not None:
        c = round((t - 32) * 5 / 9, 1) if t > 50 else t
        (out.append(("8310-5", c, "Cel")) if ok(c, "temperature_c") else dropped.append("temperature"))
    m = re.match(r"\s*(\d{2,3})\s*/\s*(\d{2,3})", str(v.get("bp") or ""))
    if m:
        s_, d_ = float(m.group(1)), float(m.group(2))
        (out.append(("8480-6", s_, "mm[Hg]")) if ok(s_, "systolic") else dropped.append("systolic"))
        (out.append(("8462-4", d_, "mm[Hg]")) if ok(d_, "diastolic") else dropped.append("diastolic"))
    for key, code, unit, rng in (("pulse", "8867-4", "/min", "heart_rate"), ("rr", "9279-1", "/min", "resp_rate"),
                                 ("spo2", "59408-5", "%", "spo2"), ("weight", "29463-7", "kg", None)):
        x = num(v.get(key))
        if x is not None:
            (out.append((code, x, unit)) if (rng is None or ok(x, rng)) else dropped.append(key))
    return out, dropped


# ======================================================================= main run
def run(inputs, xray_dir, out_root, password, lab_file=None, settings_path=None, overrides_path=None, log=print, private_dir=None,
        radiology_paths=None, source_system=None, imaging_system=None):
    private_dir = Path(private_dir) if private_dir else APP / "KEEP_INSIDE_HOSPITAL"
    cfg = load_settings(settings_path)
    if source_system:
        cfg["dataset"]["source_hospital_system"] = source_system
    if imaging_system:
        cfg["dataset"]["source_imaging_system"] = imaging_system
    k = int(cfg.get("suppression", {}).get("min_group_size", 5))
    coder = Coder()
    try:
        vault = Vault(private_dir / "id_vault.enc", password)
    except VaultError as e:
        raise StopRun(str(e))
    started = dt.datetime.now()
    batch = out_root and Path(out_root) / started.strftime("batch_%Y-%m-%d_%H%M%S")
    ver = str(cfg["dataset"]["version"])
    rel = batch / f"anonymised_dataset_v{ver}"
    prv = batch / "KEEP_INSIDE_HOSPITAL"
    for p in (rel, prv):
        p.mkdir(parents=True, exist_ok=True)
    R = {"batch": batch.name, "version": VERSION, "started": started.isoformat(timespec="seconds"),
         "warnings": [], "errors": [], "dq": defaultdict(list), "counts": {}, "excluded": {}, "source_rows": 0}

    def warn(m):
        R["warnings"].append(m)
        log("WARNING: " + m)

    # ------------------------------------------------------------------ 1. read
    inputs = [Path(p) for p in (inputs if isinstance(inputs, (list, tuple)) else [inputs])]
    log("Reading hospital export: " + ", ".join(p.name for p in inputs))
    try:
        recs, mapping, notes = ingest.ingest(inputs, overrides_path)
    except Exception as e:
        raise StopRun(f"Could not read the hospital export: {e}")
    for n in notes:
        log(n)
    if not recs:
        raise StopRun("No admissions found in the export. Check the column names against data/header_synonyms.yaml.")
    R["mapping"], R["notes"] = mapping, notes
    R["source_rows"] = len(recs)
    adms, seen, dups = [], {}, 0
    for r in recs:
        a = normalise(r, coder, cfg)
        if a["ekey"] in seen:
            dups += 1
            continue
        seen[a["ekey"]] = True
        adms.append(a)
    R["excluded"]["duplicate admission rows removed"] = dups
    if dups:
        R["dq"]["duplicates"].append(f"{dups} duplicate admission row(s) removed (same admission number).")
    no_date = sum(1 for a in adms if not a["admit"])
    R["no_admission_date"] = no_date
    if no_date:
        warn(f"{no_date} admission(s) have no readable admission date; X-rays cannot be linked to them and their month is blank.")
    log(f"{len(adms)} admission(s) for {len({a['pkey'] for a in adms})} patient(s)")

    # ------------------------------------------------------------------ scrubber (everything identifying)
    scrub = Scrubber(cfg.get("id_patterns"))
    for a in adms:
        for n in a["names"]:
            scrub.add_name(n)
        for x in a["identifiers"]:
            if re.search(r"\d", x):
                scrub.add_literal(x)
            else:
                scrub.add_name(x)

    # ------------------------------------------------------------------ optional lab file
    labs = []
    if lab_file:
        try:
            labs = ingest.read_lab_file(lab_file)
            log(f"{len(labs)} laboratory result row(s) read")
        except Exception as e:
            warn(f"Lab file could not be read: {e}")

    # ------------------------------------------------------------------ 2. X-rays: read + link
    headers = []
    if xray_dir:
        if not xray.available():
            raise StopRun("pydicom is not installed, so X-rays cannot be read. Run setup again.")
        files = [p for p in Path(xray_dir).rglob("*") if p.is_file()]
        log(f"Scanning {len(files)} file(s) in the X-ray folder")
        skipped = 0
        for p in files:
            h = xray.read_header(p)
            if h is None:
                continue
            if cfg["xray"].get("keep_only_images", True) and not h["is_image"]:
                skipped += 1
                continue
            headers.append(h)
            if h["patient_name"]:
                scrub.add_name(h["patient_name"])
            if h["patient_id"]:
                scrub.add_literal(h["patient_id"])
        R["excluded"]["non-image DICOM objects (presentation states, structured reports)"] = skipped
        if skipped:
            R["dq"]["imaging"].append(f"{skipped} non-image DICOM object(s) (presentation states, reports) excluded.")
    # ------------------------------------------------------------------ optional radiology reports (table and/or documents)
    reports = []
    if radiology_paths:
        xcode = str(cfg.get("xray", {}).get("report_only_xray_modality", "CR"))
        reports, rproblems = radiology.read_reports(radiology_paths, xcode, log)
        for pmsg in rproblems:
            R["dq"]["radiology reports"].append(pmsg)
        for r_ in reports:
            if r_.get("name"):
                scrub.add_name(r_["name"])
            for x in r_.get("ids", []):
                scrub.add_literal(x) if re.search(r"\d", x) else scrub.add_name(x)
            for doc_ in r_.get("people", []):
                scrub.add_name(doc_)
    R["reports_received"] = len(reports)
    scrub.build()
    rows = [{"row_index": i, "pid": a["uhid"], "adm": a["raw"].get("admission_no", ""), "name": a["name"], "sex": a["sex"], "age": a["age"],
             "admit": a["admit"], "discharge": a["discharge"] or a["admit"]} for i, a in enumerate(adms)]
    # links a person has already confirmed from an earlier review (study UID -> admission), kept inside the hospital
    confirmed = {}
    cpath = private_dir / "confirmed_xray_links.csv"
    if cpath.exists():
        with open(cpath, encoding="utf-8-sig") as fh:
            for c in csv.DictReader(fh):
                if str(c.get("decision", "")).strip().lower() in ("yes", "y", "accept", "accepted"):
                    confirmed[c["study_uid"]] = c["admission_key"]
    ekey_index = {a["ekey"]: i for i, a in enumerate(adms)}
    pre = {h["path"]: (ekey_index[confirmed[h["study_uid"]]], 1.0, "confirmed by reviewer")
           for h in headers if h.get("study_uid") in confirmed and confirmed[h["study_uid"]] in ekey_index}
    rest = [h for h in headers if h["path"] not in pre]
    assignments, review, unmatched = link(rows, rest, cfg) if rest else ({}, [], [])
    assignments.update(pre)
    for rv in review:
        a = adms[rv.pop("suggested_row_index")]
        rv.update({"suggested_source": a["source"], "suggested_admission_no": a["raw"].get("admission_no", ""),
                   "suggested_admission_date": a["admit"], "admission_key": a["ekey"], "decision": ""})
    # X-rays matched by patient number but outside any admission: patient-level only
    patient_only = {}
    if cfg["linking"].get("include_patient_xrays_outside_admission", True):
        by_pid = {}
        for i, a in enumerate(adms):
            if a["uhid"]:
                by_pid.setdefault(re.sub(r"[\s-]", "", a["uhid"].upper()).lstrip("0"), i)
        still = []
        for u in unmatched:
            key = re.sub(r"[\s-]", "", str(u["xray_id"]).upper()).lstrip("0")
            if key and key in by_pid:
                patient_only[u["xray_file"]] = by_pid[key]
            else:
                still.append(u)
        unmatched = still
    if headers:
        log(f"X-rays: {len(assignments)} linked to an admission, {len(patient_only)} linked to a patient only, "
            f"{len(review)} for review, {len(unmatched)} unmatched")
    else:
        log("No X-ray images supplied for this batch (optional)")
    # radiology reports -> admissions (confirmed links first)
    rep_pre = {r_["key"]: (ekey_index[confirmed[r_["key"]]], 1.0, "confirmed by reviewer", True)
               for r_ in reports if r_["key"] in confirmed and confirmed[r_["key"]] in ekey_index}
    rep_assign, rep_review, rep_unmatched = link_reports(rows, [r_ for r_ in reports if r_["key"] not in rep_pre], cfg) if reports else ({}, [], [])
    rep_assign.update(rep_pre)
    for rv in rep_review:
        a = adms[rv.pop("suggested_row_index")]
        rv.update({"suggested_admission_no": a["raw"].get("admission_no", ""), "suggested_admission_date": a["admit"],
                   "suggested_source": a["source"], "admission_key": a["ekey"], "decision": ""})
    if reports:
        log(f"Radiology reports: {len(rep_assign)} linked, {len(rep_review)} for review, {len(rep_unmatched)} unmatched")

    # ------------------------------------------------------------------ IDs
    for a in adms:
        a["pid"] = vault.patient(a["pkey"])
        a["eid"] = vault.encounter(a["ekey"])
    by_patient = defaultdict(list)
    for a in adms:
        by_patient[a["pid"]].append(a)

    # ------------------------------------------------------------------ 3/4. modules
    M = {m: [] for m in spec.MODULES}
    for pid, lst in by_patient.items():
        last = max(lst, key=lambda x: x["admit"] or dt.date.min)
        M["patient_demographics"].append({"anon_patient_id": pid, "age_band": coder.age_band(last["age"]), "sex": last["sex"],
                                          "region": last["region"], "deprivation_decile": "", "payer_category": last["payer"]})
    unm_dx, unm_med, unm_test, unm_proc = Counter(), Counter(), Counter(), Counter()
    dropped_vitals = Counter()
    for a in adms:
        M["encounters"].append({"anon_encounter_id": a["eid"], "anon_patient_id": a["pid"], "encounter_date_month": month(a["admit"]),
                                "encounter_type": a["enc_type"], "specialty_code": a["spec_code"] or "OTHER",
                                "specialty": a["spec_name"] or coder.spec_desc.get("OTHER", "Other"),
                                "length_of_stay_days": a["los"], "ward_type": a["ward_type"]})
        for code, desc in a["dx"]:
            M["diagnoses"].append({"anon_patient_id": a["pid"], "anon_encounter_id": a["eid"], "diagnosis_code": code,
                                   "code_system": "ICD-10", "diagnosis_display": desc, "years_since_diagnosis": a["years_since_dx"]})
        if a["dx_unmapped"]:
            unm_dx[scrub.scrub(a["dx_unmapped"])] += 1
        for code, disp, mth in a["procs"]:
            M["procedures"].append({"anon_patient_id": a["pid"], "anon_encounter_id": a["eid"], "procedure_code": code,
                                    "code_system": "WF-PROC", "procedure_display": disp, "procedure_month": mth})
        for p in a["proc_unmapped"]:
            unm_proc[scrub.scrub(p)] += 1
        for m in a["meds"]:
            M["medications"].append({"anon_patient_id": a["pid"], "anon_encounter_id": a["eid"], "medication_code": m["atc"],
                                     "code_system": "ATC", "medication_context": m["context"], "route": m["route"],
                                     "strength": m["strength"], "dose_pattern": m["dose"], "frequency": m["frequency"],
                                     "duration_days": m["days"], "atc_confidence": m["atc_confidence"]})
        for m in a["med_unmapped"]:
            unm_med[norm(m["raw"])] += 1
        obs, dropped = vital_obs(a, cfg)
        for d in dropped:
            dropped_vitals[d] += 1
        for code, val, unit in obs:
            val = int(val) if float(val).is_integer() else val
            M["vitals"].append({"anon_patient_id": a["pid"], "anon_encounter_id": a["eid"], "observation_code": code, "code_system": "LOINC",
                                "observation_value": val, "observation_units": unit, "result_status": "Final",
                                "observation_month": month(a["admit"])})
        for o in a["tests"]:
            M["laboratory_tests_ordered"].append({"anon_patient_id": a["pid"], "anon_encounter_id": a["eid"], "observation_code": o["code"],
                                                  "code_system": o["system"], "order_month": month(a["admit"])})
        for t in a["test_unmapped"]:
            unm_test[norm(t)] += 1

    # lab results file
    if labs:
        by_adm = {re.sub(r"\s", "", str(a["raw"].get("admission_no", "")).upper()): a for a in adms if a["raw"].get("admission_no")}
        by_uhid = defaultdict(list)
        for a in adms:
            if a["uhid"]:
                by_uhid[a["uhid"].upper()].append(a)
        unlinked = 0
        for L in labs:
            a = by_adm.get(re.sub(r"\s", "", str(L.get("admission_no", "")).upper()))
            d = parse_date(L.get("date"))
            if a is None and L.get("uhid"):
                cands = by_uhid.get(str(L["uhid"]).upper(), [])
                if d:
                    cands = [c for c in cands if c["admit"] and c["admit"] - dt.timedelta(days=2) <= d <= (c["discharge"] or c["admit"]) + dt.timedelta(days=2)]
                a = cands[0] if len(cands) == 1 else None
            if a is None:
                unlinked += 1
                continue
            o = coder.observation(L.get("test", ""))
            if not o:
                unm_test[norm(L.get("test", ""))] += 1
                continue
            st = str(L.get("status", "") or "Final").strip().title()
            st = st if st in spec.STATUSES else ("Preliminary" if st.startswith("Prov") or st.startswith("Pend") else "Final")
            M["laboratory_results"].append({"anon_patient_id": a["pid"], "anon_encounter_id": a["eid"], "observation_code": o["code"],
                                            "code_system": o["system"], "observation_value": scrub.scrub(L.get("value", "")),
                                            "observation_units": L.get("unit", "") or o.get("ucum", ""), "result_status": st,
                                            "observation_month": month(d or a["admit"])})
        R["excluded"]["lab result rows not linkable to an admission"] = unlinked
        if unlinked:
            R["dq"]["laboratory"].append(f"{unlinked} lab result row(s) could not be linked to an admission and were excluded.")

    # ------------------------------------------------------------------ small-cell suppression
    sup = Counter()
    P = M["patient_demographics"]
    for keys, field, newval in ((("age_band", "sex", "region"), "region", "Suppressed"),
                                (("age_band", "sex"), "age_band", "Suppressed"),
                                (("sex",), "sex", "Unknown")):
        cnt = Counter(tuple(p[x] for x in keys) for p in P)
        for p in P:
            if cnt[tuple(p[x] for x in keys)] < k and p[field] not in ("", newval):
                p[field] = newval
                sup[f"patient {field}"] += 1
    dx_pat = defaultdict(set)
    for d in M["diagnoses"]:
        dx_pat[d["diagnosis_code"]].add(d["anon_patient_id"])
    for d in M["diagnoses"]:
        if len(dx_pat[d["diagnosis_code"]]) < k and len(d["diagnosis_code"]) > 3:
            d["diagnosis_code"] = d["diagnosis_code"][:3]
            d["diagnosis_display"] = coder.icd_desc.get(d["diagnosis_code"], f"ICD-10 category {d['diagnosis_code']}")
            sup["diagnosis generalised to category"] += 1
    cat_pat = defaultdict(set)
    for d in M["diagnoses"]:
        cat_pat[d["diagnosis_code"]].add(d["anon_patient_id"])
    before = len(M["diagnoses"])
    M["diagnoses"] = [d for d in M["diagnoses"] if len(cat_pat[d["diagnosis_code"]]) >= k]
    sup["diagnosis rows suppressed"] += before - len(M["diagnoses"])
    sp_pat = defaultdict(set)
    for e in M["encounters"]:
        sp_pat[e["specialty_code"]].add(e["anon_patient_id"])
    for e in M["encounters"]:
        if e["specialty_code"] != "OTHER" and len(sp_pat[e["specialty_code"]]) < k:
            e["specialty_code"], e["specialty"] = "OTHER", coder.spec_desc.get("OTHER", "Other")
            sup["specialty grouped to OTHER"] += 1
    pr_pat = defaultdict(set)
    for p in M["procedures"]:
        pr_pat[p["procedure_code"]].add(p["anon_patient_id"])
    before = len(M["procedures"])
    M["procedures"] = [p for p in M["procedures"] if len(pr_pat[p["procedure_code"]]) >= k]
    sup["procedure rows suppressed"] += before - len(M["procedures"])
    R["suppression"] = dict(sup)

    # ------------------------------------------------------------------ imaging: de-identify into study/series/instance
    img_dir = rel / "05_imaging" / "dicom"
    linked = [(h, assignments[h["path"]][0], assignments[h["path"]][1], assignments[h["path"]][2]) for h in headers if h["path"] in assignments]
    linked += [(h, patient_only[h["path"]], 1.0, "patient number (outside admission)") for h in headers if h["path"] in patient_only]
    studies = defaultdict(list)
    for h, ri, score, method in linked:
        studies[(h["study_uid"] or h["path"])].append((h, ri, score, method))
    link_log = []
    for orig_study, items in studies.items():
        ri = items[0][1]
        a = adms[ri]
        enc = a["eid"] if any(x[3] != "patient number (outside admission)" for x in items) else ""
        sid = vault.study(orig_study)
        series = sorted({x[0]["series_uid"] or "s" for x in items})
        s_index = {s: n + 1 for n, s in enumerate(series)}
        per_series = defaultdict(list)
        for x in items:
            per_series[x[0]["series_uid"] or "s"].append(x)
        infos, series_out = [], []
        for s, xs in per_series.items():
            xs.sort(key=lambda x: (int(x[0]["instance_number"]) if str(x[0]["instance_number"]).isdigit() else 0, x[0]["path"]))
            for m, (h, _ri, score, method) in enumerate(xs, 1):
                dest = img_dir / sid / f"series_{s_index[s]:03d}" / f"instance_{m:04d}.dcm"
                png = prv / "review_previews" / f"{sid}_series{s_index[s]:03d}_instance{m:04d}.png"
                try:
                    info = xray.deidentify(h["path"], dest, a["pid"], vault, scrub, cfg, png)
                except Exception as e:
                    R["errors"].append(f"X-ray failed ({Path(h['path']).name}): {e}")
                    continue
                for w in info["warnings"]:
                    if w not in R["warnings"]:
                        warn(w)
                infos.append(info)
                link_log.append({"original_file": h["path"], "anon_imaging_study_id": sid, "series": s_index[s], "instance": m,
                                 "link_method": method, "link_score": score, "masked_text_regions": info["masked_regions"], "ocr": info["ocr"]})
            series_out.append({"uid": infos[-1]["new_series_uid"] if infos else "", "number": s_index[s], "count": len(xs)})
        if not infos:
            continue
        mod = next((i["modality"] for i in infos if i["modality"]), "")
        agent = any(i["contrast_agent"] for i in infos)
        contrast = "Yes" if agent else ("No" if mod in ("CR", "DX", "MG", "RF", "PX", "IO") else "Unknown")
        row = {"anon_imaging_study_id": sid, "anon_patient_id": a["pid"], "anon_encounter_id": enc, "modality": mod,
               "body_region": next((i["body_part"] for i in infos if i["body_part"]), ""),
               "study_date_month": next((i["study_month"] for i in infos if i["study_month"]), ""), "contrast_used": contrast,
               "number_of_series": len(series_out), "number_of_instances": len(infos),
               "view_position": "/".join(sorted({i["view"] for i in infos if i["view"]})), "dicom_folder": f"dicom/{sid}"}
        row["_series"] = series_out
        row["_date"] = xray.parse_da(items[0][0].get("study_date"))
        M["imaging_studies"].append(row)
    # imaging reports, 1: separate radiology reports. Merge with an X-ray study of the same patient and date when one exists,
    # otherwise the report becomes its own study (metadata from the report; no image files).
    XR = {"CR", "DX", "XRAY"}
    same_mod = lambda m1, m2: (m1 in XR and m2 in XR) or (m1 and m1 == m2)
    rep_log, merged, report_only = [], 0, 0
    for r_ in reports:
        if r_["key"] not in rep_assign:
            continue
        ri, score, method, enc_linked = rep_assign[r_["key"]]
        a = adms[ri]
        target = None
        if r_.get("date"):
            cands = [s_ for s_ in M["imaging_studies"] if s_["anon_patient_id"] == a["pid"] and s_.get("_date")
                     and abs((s_["_date"] - r_["date"]).days) <= 1 and same_mod(s_["modality"], r_["modality"])
                     and (not r_["body_region"] or not s_["body_region"] or r_["body_region"] in s_["body_region"].upper() or s_["body_region"].upper() in r_["body_region"])]
            if cands:
                target = min(cands, key=lambda s_: abs((s_["_date"] - r_["date"]).days))
        if target is not None:
            merged += 1
            if not target["body_region"] and r_["body_region"]:
                target["body_region"] = r_["body_region"]
            sid = target["anon_imaging_study_id"]
        else:
            report_only += 1
            sid = vault.study(r_["key"])
            mth = month(r_["date"]) if r_.get("date") else month(a["admit"])
            M["imaging_studies"].append({
                "anon_imaging_study_id": sid, "anon_patient_id": a["pid"], "anon_encounter_id": a["eid"] if enc_linked else "",
                "modality": r_["modality"] or "OT", "body_region": r_["body_region"], "study_date_month": mth,
                "contrast_used": r_["contrast"], "number_of_series": 0, "number_of_instances": 0, "view_position": r_["view"],
                "dicom_folder": "", "_series": [], "_date": r_.get("date")})
        M["imaging_reports"].append({"anon_imaging_report_id": vault.document(r_["key"]), "anon_imaging_study_id": sid,
                                     "anon_patient_id": a["pid"], "report_text": scrub.scrub(r_["text"]), "report_status": r_["status"]})
        rep_log.append({"report_source": r_["source"], "anon_imaging_study_id": sid, "link_method": method, "link_score": score,
                        "attached_to": "X-ray study (images)" if target is not None else "report-only study"})
    reported = {x["anon_imaging_study_id"] for x in M["imaging_reports"]}
    # imaging reports, 2: a report column inside the main export, only for studies without a separate report (attached to that admission's latest study)
    st_by_enc = defaultdict(list)
    for s_ in M["imaging_studies"]:
        if s_["anon_encounter_id"]:
            st_by_enc[s_["anon_encounter_id"]].append(s_)
    for a in adms:
        if a["xray_report"] and st_by_enc.get(a["eid"]):
            s_ = max(st_by_enc[a["eid"]], key=lambda x: x["study_date_month"])
            if s_["anon_imaging_study_id"] in reported:      # a separate report already covers this study
                continue
            stt = a["xray_report_status"].title() if a["xray_report_status"].title() in ("Preliminary", "Final", "Amended") else "Final"
            M["imaging_reports"].append({"anon_imaging_report_id": vault.document("xrcol|" + a["ekey"]), "anon_imaging_study_id": s_["anon_imaging_study_id"],
                                         "anon_patient_id": a["pid"], "report_text": scrub.scrub(a["xray_report"]), "report_status": stt})
    R["imaging"] = {"x_ray_images_received": len(headers), "reports_received": len(reports), "reports_linked": len(rep_log),
                    "reports_merged_with_images": merged, "report_only_studies": report_only,
                    "reports_for_review": len(rep_review), "reports_unmatched": len(rep_unmatched)}

    # ------------------------------------------------------------------ clinical documents (PDF/A)
    log("Writing de-identified discharge summaries (PDF/A)")
    dx_final = defaultdict(list)
    for d in M["diagnoses"]:
        dx_final[d["anon_encounter_id"]].append((d["diagnosis_code"], d["diagnosis_display"]))
    pat = {p["anon_patient_id"]: p for p in M["patient_demographics"]}
    enc = {e["anon_encounter_id"]: e for e in M["encounters"]}
    for a in adms:
        did = vault.document(a["ekey"])
        f = rel / "04_documents" / "documents_pdfa" / f"{did}.pdf"
        e, p = enc[a["eid"]], pat[a["pid"]]
        dt_ = a["doc_text"]
        vit = [f"{lbl} {val}" for lbl, val in (("Temp", a["vitals_raw"].get("temp")), ("BP", a["vitals_raw"].get("bp")),
                                               ("Pulse", a["vitals_raw"].get("pulse")), ("SpO2", a["vitals_raw"].get("spo2")),
                                               ("RR", a["vitals_raw"].get("rr"))) if val]
        sc = lambda lst: [scrub.scrub(x) for x in lst if str(x).strip()]
        pdfdoc.discharge_summary(f, {
            "anon_patient_id": a["pid"], "anon_encounter_id": a["eid"], "sex": p["sex"], "age_band": p["age_band"],
            "encounter_date_month": e["encounter_date_month"], "encounter_type": e["encounter_type"], "los": e["length_of_stay_days"],
            "ward_type": e["ward_type"], "specialty": e["specialty"], "complaints": sc(dt_["complaints"]),
            "vitals": sc(vit), "exam": sc(dt_["exam"]), "investigations": sc(dt_["investigations"]), "treatment": sc(dt_["treatment"]),
            "diagnosis_text": [], "diagnosis_codes": dx_final.get(a["eid"], []), "years_since_diagnosis": a["years_since_dx"],
            "surgery": sc(dt_["surgery"]), "advice": sc(dt_["advice"]),
            "discharge_meds": [{k2: scrub.scrub(v2) for k2, v2 in m.items()} for m in a["discharge_meds_table"]]})
        M["document_index"].append({"anon_document_id": did, "anon_patient_id": a["pid"], "anon_encounter_id": a["eid"],
                                    "document_type": "Discharge Summary", "document_type_code": "18842-5",
                                    "document_month": month(a["discharge"] or a["admit"]),
                                    "file": f"documents_pdfa/{did}.pdf", "sha256": sha256(f)})

    # ------------------------------------------------------------------ write modules: CSV + FHIR NDJSON
    log("Writing modules")
    written = {}
    for mod, rowsx in M.items():
        if not rowsx:
            continue
        base = rel / (spec.FILE_PATHS[mod])
        base.parent.mkdir(parents=True, exist_ok=True)
        cols = spec.columns(mod)
        with open(str(base) + ".csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r_ in rowsx:
                w.writerow({c: ("" if r_.get(c) is None else r_.get(c)) for c in cols})
        ftype = spec.MODULES[mod]["fhir"]
        if ftype:
            with open(str(base) + ".ndjson", "w", encoding="utf-8") as fh:
                for i, r_ in enumerate(rowsx, 1):
                    res = {"Patient": lambda: fhir.patient(r_), "Encounter": lambda: fhir.encounter(r_),
                           "Condition": lambda: fhir.condition(r_, i), "Procedure": lambda: fhir.procedure(r_, i),
                           "MedicationStatement": lambda: fhir.medication(r_, i),
                           "Observation": lambda: fhir.observation(r_, i, "vital-signs" if mod == "vitals" else "laboratory"),
                           "DocumentReference": lambda: fhir.document(r_),
                           "ImagingStudy": lambda: fhir.imaging_study(r_, r_.get("_series", [])),
                           "DiagnosticReport": lambda: fhir.diagnostic_report(r_)}[ftype]()
                    fh.write(json.dumps(res, ensure_ascii=False) + "\n")
        written[mod] = len(rowsx)
    R["counts"] = written

    # ------------------------------------------------------------------ private review files (inside hospital only)
    def wcsv(path, recs_, fields):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for x in recs_:
                w.writerow(x)

    wcsv(prv / "column_mapping.csv", mapping, ["source", "column", "field", "how"])
    wcsv(prv / "xray_review_queue.csv", review, ["xray_file", "xray_name", "xray_id", "xray_date", "suggested_name", "suggested_admission_no",
                                                 "suggested_admission_date", "suggested_source", "score", "decision", "study_uid", "admission_key"])
    wcsv(prv / "xray_unmatched.csv", unmatched, ["xray_file", "xray_name", "xray_id", "xray_date", "best_score"])
    wcsv(prv / "report_review_queue.csv", rep_review, ["report_source", "report_name", "report_id", "report_date", "report_study", "suggested_name",
                                                       "suggested_admission_no", "suggested_admission_date", "suggested_source", "score", "decision",
                                                       "report_key", "admission_key"])
    wcsv(prv / "report_unmatched.csv", rep_unmatched, ["report_source", "report_name", "report_id", "report_date", "report_study", "best_score", "reason"])
    wcsv(prv / "report_link_log.csv", rep_log, ["report_source", "anon_imaging_study_id", "link_method", "link_score", "attached_to"])
    wcsv(prv / "xray_link_log.csv", link_log, ["original_file", "anon_imaging_study_id", "series", "instance", "link_method", "link_score", "masked_text_regions", "ocr"])
    for name, cnt in (("unmapped_diagnoses.csv", unm_dx), ("unmapped_medicines.csv", unm_med),
                      ("unmapped_tests.csv", unm_test), ("unmapped_procedures.csv", unm_proc)):
        wcsv(prv / name, [{"text": t, "count": n} for t, n in cnt.most_common()], ["text", "count"])
    R["unmapped"] = {"diagnoses": sum(unm_dx.values()), "medicines": sum(unm_med.values()),
                     "tests": sum(unm_test.values()), "procedures": sum(unm_proc.values())}
    R["excluded"]["implausible vital-sign values"] = sum(dropped_vitals.values())
    R["excluded"]["X-rays held for human review (name not exact or no patient number)"] = len(review)
    R["excluded"]["X-rays not matched to any patient in the export"] = len(unmatched)
    R["excluded"]["X-rays that failed de-identification"] = sum(1 for e in R["errors"] if e.startswith("X-ray failed"))
    R["xray_received"] = len(headers)
    R["excluded"]["Radiology reports held for human review"] = len(rep_review)
    R["excluded"]["Radiology reports not matched to any patient in the export"] = len(rep_unmatched)
    if dropped_vitals:
        R["dq"]["observations"].append("Implausible vital values removed: " + ", ".join(f"{k_} x{v_}" for k_, v_ in dropped_vitals.items()) + ".")
    gov_src = APP / "config" / "governance"
    gdir = rel / "06_governance"
    gdir.mkdir(parents=True, exist_ok=True)
    gov_found = []
    for g_ in ("permitted_use_statement.pdf", "data_sharing_agreement_reference.pdf", "dpia_summary.pdf"):
        if (gov_src / g_).exists():
            shutil.copy2(gov_src / g_, gdir / g_)
            gov_found.append(g_)
    R["governance"] = gov_found

    # ------------------------------------------------------------------ 5. documentation, checks, package
    ctx = dict(R=R, M=M, adms=adms, headers=headers, link_log=link_log, review=review, unmatched=unmatched, cfg=cfg, coder=coder,
               k=k, rel=rel, prv=prv, scrub=scrub, labs=labs)
    docs.write_all(ctx, log)
    status = docs.checks_and_package(ctx, log)
    vault.save({"batch": batch.name, "at": dt.datetime.now().isoformat(timespec="seconds"), "admissions": len(adms)})
    R["status"] = status
    R["batch_folder"], R["release_folder"], R["private_folder"] = str(batch), str(ctx["rel"]), str(prv)
    R["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    R["dq"] = dict(R["dq"])
    (prv / "run_report.json").write_text(json.dumps({k_: v_ for k_, v_ in R.items() if k_ not in ("mapping",)}, indent=2, default=str))
    log(f"Done: {status}")
    return R


def main():
    ap = argparse.ArgumentParser(description="Workframe Health De-ID 2.0")
    ap.add_argument("--input", required=True, nargs="+", help="hospital export(s): .xlsx, .csv (or .pdf printouts)")
    ap.add_argument("--xrays", default="", help="optional folder of DICOM images")
    ap.add_argument("--reports", default=[], nargs="*", help="optional radiology reports: Excel/CSV files and/or folders of Word/PDF reports")
    ap.add_argument("--labs", default="", help="optional lab-results export")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--password", required=True, help="hospital vault password (min 8 characters)")
    a = ap.parse_args()
    try:
        rep = run(a.input, a.xrays or None, a.out, a.password, a.labs or None, radiology_paths=a.reports or None)
    except StopRun as e:
        print("STOPPED:", e)
        raise SystemExit(2)
    print(json.dumps({k: rep[k] for k in ("status", "counts", "batch_folder")}, indent=2, default=str))


if __name__ == "__main__":
    main()
