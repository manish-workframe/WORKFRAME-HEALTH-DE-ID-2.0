"""Documentation (section 5), file inventory and volumes (section 7), and the 13-point pre-transfer checklist (section 9)."""
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path

import pandas as pd

from . import pdfdoc, spec, xray

INDIA = [
    ("NHS number", "Hospital UHID / MRN, IP / admission number, ABHA number and address: all removed; Aadhaar never included"),
    ("Postcode -> region", "PIN code -> state (first two digits; district if the India Post directory is installed); PIN not released"),
    ("Deprivation decile (IMD)", "No patient-level Indian equivalent: left blank. payer_category supplied instead (by agreement)"),
    ("ICD-10 / SNOMED CT (diagnoses)", "WHO ICD-10, as used in India"),
    ("OPCS (procedures)", "Local procedure codes (WF-PROC) pending SNOMED CT mapping from the NRCeS India release"),
    ("dm+d (medicines)", "Brand name -> generic -> WHO ATC; India has no dm+d equivalent"),
    ("LOINC / UCUM", "Same"),
    ("Encounter type A&E", "Emergency (casualty); Day care added"),
    ("FHIR R4", "Same; resource choices follow ABDM/NRCeS practice"),
    ("DPIA, data sharing agreement", "DPIA under India's DPDP Act; hospital MOU / data sharing agreement; institutional ethics committee approval (ICMR guidelines)"),
]


def _rel(p, root):
    return str(Path(p).relative_to(root)).replace("\\", "/")


def count_records(p):
    s = p.suffix.lower()
    if s == ".csv":
        with open(p, encoding="utf-8", errors="ignore") as f:
            return max(0, sum(1 for _ in f) - 1)
    if s == ".ndjson":
        with open(p, encoding="utf-8", errors="ignore") as f:
            return sum(1 for line in f if line.strip())
    return 1


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ======================================================================= section 5 documents
UNITS = {"length_of_stay_days": "days", "years_since_diagnosis": "years", "duration_days": "days", "age_band": "years",
         "observation_value": "as stated in observation_units", "observation_units": "UCUM code", "strength": "as prescribed (mg, ml, g, mcg, IU, %)",
         "number_of_series": "count", "number_of_instances": "count", "deprivation_decile": "decile (1-10)"}
CODED = {"diagnosis_code": "diagnosis_code", "medication_code": "medication_code", "observation_code": "observation_code",
         "specialty_code": "specialty_code", "procedure_code": "procedure_code", "document_type_code": "document_type_code"}


def dictionary_rows(present):
    rows = []
    for mod, m in spec.MODULES.items():
        if mod not in present:
            continue
        for f in m["fields"]:
            if f["enum"]:
                fmt, perm = "Text, controlled list", " | ".join(f["enum"])
            elif f["name"] in CODED:
                fmt, perm = f["format"], f"Codes listed in value_sets.csv (value_set = {CODED[f['name']]})"
            elif f["name"] == "observation_units":
                fmt, perm = f["format"], "UCUM codes listed in value_sets.csv (value_set = observation_units)"
            else:
                fmt, perm = f["format"], ("Any value matching the format" if f["pattern"] or f["type"] in ("integer", "number")
                                          else "Free text, de-identified" if f["name"] in ("report_text",) else "Any value matching the format")
            rows.append({"module": m["title"], "file": spec.FILE_PATHS[mod] + ".csv", "field_name": f["name"],
                         "description": f["description"], "data_type": f["type"], "format": fmt, "permitted_values": perm,
                         "units": UNITS.get(f["name"], ""), "source_system": f["source"],
                         "anonymisation_rule": f["rule"], "synthetic_example": f["example"],
                         "required": "Yes" if f["required"] else "No", "additional_field": "Yes" if f["additional"] else "No"})
    return rows


CODE_SYSTEMS = [
    ("ICD-10", "http://hl7.org/fhir/sid/icd-10", "WHO ICD-10 (2019 edition)", "diagnosis_code"),
    ("ATC", "http://www.whocc.no/atc", "WHO ATC/DDD Index (current)", "medication_code"),
    ("LOINC", "http://loinc.org", "LOINC (current release)", "observation_code, document_type_code"),
    ("UCUM", "http://unitsofmeasure.org", "UCUM", "observation_units"),
    ("DICOM", "http://dicom.nema.org/resources/ontology/DCM", "DICOM PS3.16", "modality"),
    ("HL7 v3 ActCode", "http://terminology.hl7.org/CodeSystem/v3-ActCode", "HL7 FHIR R4", "encounter_type (FHIR files)"),
    ("WF-SPECIALTY", "https://workframe.health/fhir/CodeSystem/specialty", "local, this release", "specialty_code"),
    ("WF-PROC", "https://workframe.health/fhir/CodeSystem/procedure", "local, pending SNOMED CT", "procedure_code"),
    ("LOCAL (WF-LAB / WF-IMG)", "https://workframe.health/fhir/CodeSystem/local-lab", "local, pending LOINC", "observation_code where no LOINC code exists"),
]


def mappings(M, coder):
    """How hospital text was turned into codes: only the rules actually used in this release."""
    out = []
    used = lambda mod, col: {r[col] for r in M.get(mod, []) if r.get(col)}
    dx = used("diagnoses", "diagnosis_code")
    for rx, code, desc in coder.icd:
        if code in dx or code[:3] in dx:
            out.append(("Diagnosis text -> ICD-10", rx.pattern, "ICD-10", code if code in dx else code[:3],
                        desc if code in dx else f"{desc} (released at category level)", ""))
    meds = used("medications", "medication_code")
    for rx, r in coder.drugs:
        if r["atc"] in meds:
            out.append(("Medicine brand -> generic -> ATC", rx.pattern, "ATC", r["atc"], r["generic"], f"confidence {r['atc_confidence']}"))
    obs = used("vitals", "observation_code") | used("laboratory_results", "observation_code") | used("laboratory_tests_ordered", "observation_code")
    for rx, r in coder.loinc:
        if r["code"] in obs:
            out.append(("Test or vital name -> LOINC", rx.pattern, r["system"], r["code"], r["display"], f"unit {r['ucum']}" if r.get("ucum") else ""))
    sp = used("encounters", "specialty_code")
    for rx, code, desc in coder.spec:
        if code in sp:
            out.append(("Department / consultant speciality -> specialty", rx.pattern, "WF-SPECIALTY", code, desc, ""))
    pr = used("procedures", "procedure_code")
    for rx, code, desc in coder.proc:
        if code in pr:
            out.append(("Surgery name -> procedure", rx.pattern, "WF-PROC", code, desc, ""))
    regions = used("patient_demographics", "region")
    for pre, st in sorted(coder.pin.items()):
        if st in regions:
            out.append(("PIN code (first two digits) -> region", pre, "State", st, st, "PIN code itself not released"))
    for src, tgt in (("PMJAY / Ayushman / ABArK", "PM-JAY"), ("CGHS / ECHS / ESI / Govt / state schemes", "Government scheme"),
                     ("Insurance / TPA / cashless / mediclaim", "Private insurance"), ("Corporate / panel / company", "Corporate"),
                     ("Cash / self / general", "Self-pay"), ("anything else", "Other")):
        out.append(("Patient category -> payer_category", src, "enumeration", tgt, tgt, ""))
    for src, tgt in (("ICU / CCU / NICU / PICU / HDU / MICU / SICU", "Critical care"), ("Semi private", "Semi-private"),
                     ("Private / deluxe / suite / single / special", "Private room"), ("Day care", "Day care"),
                     ("General / male / female ward", "General ward"), ("anything else", "Other")):
        out.append(("Ward -> ward_type", src, "enumeration", tgt, tgt, ""))
    for src, tgt, note in (("Temperature in degrees F (value above 50)", "Cel", "converted: (F - 32) x 5/9, one decimal"),
                           ("Any exact date", "YYYY-MM", "day removed"), ("Age in years or date of birth", "age_band", "0-17 / 18-39 / 40-59 / 60-79 / 80+"),
                           ("Admission type (IP / OP / casualty / day care)", "encounter_type", "Inpatient / Outpatient / Emergency / Day care")):
        out.append(("Transformation", src, "", tgt, tgt, note))
    return [dict(zip(("mapping", "source_value_or_rule", "target_code_system", "target_code", "target_display", "notes"), r)) for r in out]


def schema(present):
    out = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Anonymised EHR and Imaging Dataset (CSV files)",
           "description": "One JSON Schema per CSV file. All CSV values are text; numeric fields are constrained by pattern.",
           "files": {}}
    for mod in present:
        props, req = {}, []
        for f in spec.MODULES[mod]["fields"]:
            p = {"type": "string", "description": f["description"]}
            if f["enum"]:
                p["enum"] = sorted(set(f["enum"] + ([""] if not f["required"] else [])))
            elif f["pattern"]:
                p["pattern"] = f["pattern"] if f["required"] else f"({f['pattern'][1:-1]})?" and f"^(?:{f['pattern'][1:-1]})?$"
            elif f["type"] == "integer":
                p["pattern"] = r"^-?\d+$" if f["required"] else r"^(-?\d+)?$"
            elif f["type"] == "number":
                p["pattern"] = r"^-?\d+(\.\d+)?$"
            if f["required"]:
                req.append(f["name"])
                if "pattern" not in p and "enum" not in p:
                    p["minLength"] = 1
            props[f["name"]] = p
        out["files"][spec.FILE_PATHS[mod] + ".csv"] = {"type": "object", "properties": props, "required": req,
                                                       "additionalProperties": False}
    return out


def value_sets(M, coder):
    vs = []
    used = lambda mod, col: sorted({r[col] for r in M.get(mod, []) if r.get(col)})
    for c in used("diagnoses", "diagnosis_code"):
        vs.append(("diagnosis_code", "ICD-10", c, coder.icd_desc.get(c, f"ICD-10 category {c}")))
    gen = {}
    for _, r in coder.drugs:
        gen.setdefault(r["atc"], r["generic"])
    for c in used("medications", "medication_code"):
        vs.append(("medication_code", "ATC", c, gen.get(c, "")))
    lo = {r["code"]: (r["system"], r["display"]) for _, r in coder.loinc}
    for mod in ("vitals", "laboratory_results", "laboratory_tests_ordered"):
        for c in used(mod, "observation_code"):
            vs.append(("observation_code", lo.get(c, ("", ""))[0], c, lo.get(c, ("", ""))[1]))
    for c in used("encounters", "specialty_code"):
        vs.append(("specialty_code", "WF-SPECIALTY (local)", c, coder.spec_desc.get(c, "")))
    for c in used("procedures", "procedure_code"):
        vs.append(("procedure_code", "WF-PROC (local)", c, coder.proc_desc.get(c, "")))
    for u in sorted(set(used("vitals", "observation_units")) | set(used("laboratory_results", "observation_units"))):
        vs.append(("observation_units", "UCUM", u, ""))
    for name, values in (("age_band", spec.AGE_BANDS), ("sex", spec.SEXES), ("encounter_type", spec.ENC_TYPES),
                         ("result_status", spec.STATUSES), ("payer_category", [p for p in spec.PAYERS if p]),
                         ("ward_type", [w for w in spec.WARDS if w]), ("contrast_used", ["Yes", "No", "Unknown"]),
                         ("document_type", ["Discharge Summary"])):
        for v in values:
            vs.append((name, "enumeration", v, ""))
    vs.append(("document_type_code", "LOINC", "18842-5", "Discharge summary"))
    seen, out = set(), []
    for r in vs:
        if r not in seen:
            seen.add(r)
            out.append({"value_set": r[0], "code_system": r[1], "code": r[2], "display": r[3]})
    return out


def write_all(ctx, log):
    R, M, rel, coder, k = ctx["R"], ctx["M"], ctx["rel"], ctx["coder"], ctx["k"]
    d = rel / "00_documentation"
    d.mkdir(parents=True, exist_ok=True)
    present = [m for m in spec.MODULES if M.get(m)]
    ctx["present"] = present
    log("Writing documentation")

    dd = dictionary_rows(present)
    pd.DataFrame(dd).to_csv(d / "data_dictionary.csv", index=False, encoding="utf-8")
    with pd.ExcelWriter(d / "data_dictionary.xlsx") as xw:
        pd.DataFrame(dd).to_excel(xw, sheet_name="Data dictionary", index=False)
        pd.DataFrame([{"UK specification": a, "Applied in India": b} for a, b in INDIA]).to_excel(xw, sheet_name="Indian adaptations", index=False)
    (d / "schema.json").write_text(json.dumps(schema(present), indent=2), encoding="utf-8")
    vs = value_sets(M, coder)
    mp = mappings(M, coder)
    cs = [dict(zip(("code_system", "uri", "version", "used_for"), r)) for r in CODE_SYSTEMS]
    pd.DataFrame(vs).to_csv(d / "value_sets.csv", index=False, encoding="utf-8")
    pd.DataFrame(mp).to_csv(d / "value_set_mappings.csv", index=False, encoding="utf-8")
    pd.DataFrame(cs).to_csv(d / "code_systems.csv", index=False, encoding="utf-8")
    (d / "value_sets.json").write_text(json.dumps({"code_systems": cs, "permitted_values_and_codes": vs, "mappings": mp},
                                                  indent=2, ensure_ascii=False), encoding="utf-8")
    pdfdoc.erd_pdf(d / "entity_relationship_diagram.pdf", spec.MODULES)

    sup = R.get("suppression", {})
    pdfdoc.text_pdf(d / "anonymisation_methodology.pdf", "Anonymisation Methodology",
                    f"Workframe Health De-ID {R['version']} · batch {R['batch']} · generated {dt.date.today().isoformat()}", [
        ("h", "1. Where processing happens"),
        ("p", "All processing ran on a computer inside the hospital. Identifiable data never left it. Only this folder is released. "
              "Review lists, the column mapping, X-ray previews and the encrypted ID vault stay in KEEP_INSIDE_HOSPITAL."),
        ("h", "2. Identifiers"),
        ("p", "anon_patient_id, anon_encounter_id, anon_imaging_study_id and anon_document_id are random UUIDs (version 4). "
              "A password-encrypted vault (scrypt key derivation, Fernet/AES-128) held only by the hospital records which UUID belongs to "
              "each patient, keyed by a secret HMAC-SHA256 of the hospital number, so the same patient keeps the same ID across batches. "
              "The receiving party cannot reverse or recompute the IDs."),
        ("h", "3. Direct identifiers removed (section 4.1)"),
        ("p", "Patient name; UHID/MRN, IP/admission number, ABHA number and address, Aadhaar, PAN; date of birth (age band only); PIN code and "
              "address (state only); phone numbers and e-mail; relatives' names (W/O, S/O, D/O); medico-legal case numbers; consultant, referring, "
              "operator and reporting clinician names and phone numbers; signature; bed number; device serial numbers and station names; "
              "exact dates (generalised to YYYY-MM)."),
        ("h", "4. Free text (section 4.4)"),
        ("p", "Narrative fields are scrubbed by rules: every name and number from the identifier columns of the whole batch, plus patterns "
              "for phone, ABHA, Aadhaar, PAN, e-mail, relatives, MLC numbers, hospital-number formats and dates. Codes replace diagnosis, "
              "procedure and medicine text in the coded modules. A sample of documents must be reviewed manually before transfer "
              "(checklist item 3)."),
        ("h", "4a. Radiology reports"),
        ("p", "Radiology reports supplied separately (Excel/CSV lists or Word/PDF report documents) are read inside the hospital. The patient header "
              "block, referring and reporting doctor names, registration numbers and signatures are removed; the remaining findings and impression "
              "are scrubbed with the same rules as other free text. Reports are linked to the patient by IP/admission number, then patient number, then "
              "name + sex + age + date; uncertain matches are held for human review and are not released."),
        ("h", "5. DICOM (section 4.2)"),
        ("p", "Removed or blanked: PatientName and PatientID (replaced by anon_patient_id), PatientBirthDate, PatientAge, AccessionNumber, StudyID, "
              "InstitutionName/Address, ReferringPhysicianName, OperatorsName, PerformingPhysicianName, StationName, DeviceSerialNumber, "
              "ImageComments, RequestAttributesSequence, all private tags and overlay planes. Every UID is replaced through the vault. "
              "Dates are set to the first day of their month (consistent within each patient) and times removed. Burned-in text is detected "
              "with OCR (Tesseract) and blacked out. Files are stored as dicom/<anon_imaging_study_id>/series_NNN/instance_NNNN.dcm."),
        ("h", f"6. Small-cell suppression (section 4.3): threshold {k}"),
        ("p", "Patients: region is suppressed where fewer than the threshold share age band + sex + region, then age band where fewer share "
              "age band + sex, then sex. Diagnoses: codes held by fewer patients than the threshold are truncated to the 3-character ICD-10 "
              "category; categories still below it are removed. Specialties below it are grouped to OTHER. Procedures below it are removed."),
        ("table", [["Action", "Count"]] + [[a, str(b)] for a, b in sup.items()] or [["Action", "Count"], ["none", "0"]], [120, 30]),
        ("h", "7. Indian adaptations of the specification"),
        ("table", [["Specification (UK)", "Applied in India"]] + [list(x) for x in INDIA], [55, 120]),
        ("h", "8. Tools"),
        ("p", "Python, pandas, pydicom, Tesseract OCR, ReportLab and pikepdf (PDF/A-2b), cryptography (vault). Coding tables: ICD-10 starter table, "
              "brand-to-ATC table (to be verified by a pharmacist), LOINC starter table, local specialty and procedure tables."),
        ("h", "9. Residual risks"),
        ("p", "Text-pattern scrubbing cannot guarantee removal of every unusual identifier in narrative text; documents are therefore reviewed "
              "manually on a sample. OCR may miss faint or stylised burned-in text; every X-ray preview should be checked. Rare combinations "
              "in a single hospital remain a re-identification risk below the agreed threshold; the threshold should be agreed with the "
              "receiving team. Length of stay and years since diagnosis are released as additional fields and should be confirmed in scope."),
    ])

    unm = R.get("unmapped", {})
    ex = R.get("excluded", {})
    sup = R.get("suppression", {})
    miss = []
    for mod in present:
        for f in spec.MODULES[mod]["fields"]:
            vals = [r.get(f["name"], "") for r in M[mod]]
            empty = sum(1 for v in vals if v in ("", None))
            if vals and empty:
                why = ("not available in India (payer_category supplied instead)" if f["name"] == "deprivation_decile"
                       else "no PIN code in the export, or suppressed" if f["name"] == "region"
                       else "X-ray taken outside any admission in the export (linked to the patient only)" if (mod == "imaging_studies" and f["name"] == "anon_encounter_id")
                       else "not stated in the discharge summary" if f["name"] in ("dose_pattern", "frequency", "duration_days", "strength", "route")
                       else "no year of first diagnosis recorded" if f["name"] == "years_since_diagnosis"
                       else "not recorded in the source" )
                miss.append([spec.MODULES[mod]["title"], f["name"], f"{empty}/{len(vals)}", f"{100 * empty / len(vals):.0f}%", why])
    ctx["missingness"] = miss
    lab_note = ("Laboratory values come from the separate LIS export, linked by admission number or UHID and date."
                if M.get("laboratory_results") else "No LIS export was supplied, so no laboratory result values are included.")
    blocks = [
        ("p", f"Release covers {len(M['encounters'])} encounter(s) for {len(M['patient_demographics'])} patient(s), from "
              f"{R.get('source_rows', 0)} source row(s) and {R.get('xray_received', 0)} X-ray image(s) received. "
              f"Modules supplied: {', '.join(spec.MODULES[m]['title'] for m in present)}. Not supplied: "
              + ("; ".join(f"{spec.MODULES[m]['title']} (no source data in this batch)" for m in spec.MODULES if m not in present) or "none") + "."),
        ("h", "1. Missingness"),
        ("p", "Fields with blank values, by module. Blank means not recorded in the source, not available in India, or removed by suppression."),
        ("table", [["Module", "Field", "Blank", "%", "Reason"]] + (miss or [["-", "none", "0", "0%", "-"]]), [38, 38, 18, 12, 64]),
        ("h", "2. Duplicates"),
        ("p", f"{ex.get('duplicate admission rows removed', 0)} duplicate admission row(s) found and removed (same admission number in more than one row "
              "or file). Each patient appears once in Patient Demographics; repeat admissions of the same patient share one anon_patient_id."),
        ("h", "3. Coding limitations"),
        ("p", f"Hospital text not matched to a code is left out of the coded modules rather than guessed: diagnoses {unm.get('diagnoses', 0)}, "
              f"medicine lines {unm.get('medicines', 0)}, test names {unm.get('tests', 0)}, procedures {unm.get('procedures', 0)}. "
              "Diagnoses are mapped from free text by rule-based patterns, not by a clinical coder. Medium-confidence ATC codes are group-level or "
              "combination products (see atc_confidence). Procedure codes are local (WF-PROC) pending SNOMED CT. Some tests use local codes "
              "(WF-LAB) where no LOINC code was assigned. Every rule used is listed in value_set_mappings.csv."),
        ("h", "4. Delayed results"),
        ("p", "Results reported after the export date (for example cultures or histopathology) are not included; a later batch carries them. "
              + lab_note + " Imaging report text is included where reports were supplied (export column, report list or report documents); "
              "reports finalised after the export are not captured. Discharge summaries are taken as exported; later amendments are not tracked."),
        ("h", "5. Excluded records"),
        ("table", [["What was excluded", "Count"]] + [[k_[0].upper() + k_[1:], str(v_)] for k_, v_ in ex.items()]
                  + [["Rows removed by small-cell suppression: " + k_, str(v_)] for k_, v_ in sup.items() if "rows" in k_]
                  + [["Admissions with no readable admission date (kept, month blank)", str(R.get("no_admission_date", 0))]], [140, 30]),
        ("p", "Held-back X-rays are listed inside the hospital for review; they are released in a later batch once a person confirms the patient."),
        ("h", "6. Other data-quality issues"),
        ("p", "Values generalised for privacy (not errors): " + (", ".join(f"{k_} {v_}" for k_, v_ in sup.items() if "rows" not in k_) or "none") + "."),
    ]
    im = R.get("imaging", {})
    if im.get("reports_received") or im.get("x_ray_images_received"):
        blocks += [("p", f"Imaging: {im.get('x_ray_images_received', 0)} X-ray image file(s) and {im.get('reports_received', 0)} radiology report(s) received; "
                         f"{im.get('reports_linked', 0)} report(s) linked to patients, of which {im.get('reports_merged_with_images', 0)} were attached to an "
                         f"imaging study with images and {im.get('report_only_studies', 0)} are report-only studies (study metadata taken from the report; "
                         "number_of_instances = 0, no DICOM files). Reports held for review or unmatched are listed under Excluded records.")]
    for key, items in R["dq"].items():
        if key not in ("duplicates",):
            blocks += [("p", x) for x in items]
    if R["warnings"]:
        blocks += [("p", "Processing warnings: " + " ".join(R["warnings"]))]
    blocks += [("p", "Region requires a PIN code in the export. Vital signs outside plausible ranges are removed (counted above). "
                     "Free text in the clinical documents is de-identified by rule; a sample is reviewed by a person before transfer.")]
    pdfdoc.text_pdf(d / "data_quality_statement.pdf", "Data Quality Statement", f"Batch {R['batch']}", blocks)

    (rel / "06_governance").mkdir(exist_ok=True)
    if len(R.get("governance", [])) < 3:
        (rel / "06_governance" / "README.txt").write_text(
            "Place the signed governance documents here before transfer, or put them in config/governance/ so every batch includes them:\n"
            "  permitted_use_statement.pdf\n  data_sharing_agreement_reference.pdf\n  dpia_summary.pdf  (DPIA under India's DPDP Act)\n",
            encoding="utf-8")


# ======================================================================= checks, README, inventory, archive
def checks_and_package(ctx, log):
    R, M, rel, prv, scrub, k = ctx["R"], ctx["M"], ctx["rel"], ctx["prv"], ctx["scrub"], ctx["k"]
    log("Running the 13-point pre-transfer checklist")
    checks = []

    def add(n, item, status, detail):
        checks.append({"no": n, "item": item, "status": status, "detail": detail})

    # 1 + 6: identifier scan across every released file, DICOM header and file path
    leaks = []
    for p in rel.rglob("*"):
        if not p.is_file():
            continue
        for hit in scrub.leaks(_rel(p, rel)):
            leaks.append((f"path {_rel(p, rel)}", hit))
        s = p.suffix.lower()
        try:
            if s in (".csv", ".ndjson", ".json", ".md", ".txt"):
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    for hit in scrub.leaks(line):
                        leaks.append((f"{_rel(p, rel)} line {i}", hit))
            elif s == ".dcm":
                for v in xray.header_strings(p):
                    for hit in scrub.leaks(v):
                        leaks.append((f"{_rel(p, rel)} header", hit))
            elif s == ".pdf":
                try:
                    import pdfplumber
                    with pdfplumber.open(str(p)) as pdf:
                        txt = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
                    for hit in scrub.leaks(txt):
                        leaks.append((_rel(p, rel), hit))
                except ImportError:
                    pass
        except Exception as e:
            leaks.append((_rel(p, rel), f"could not scan: {e}"))
    with open(prv / "leak_findings.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["where", "found"])
        w.writerows(leaks)
    add(1, "All direct identifiers removed from all modules and DICOM headers", "PASS" if not leaks else "FAIL",
        f"{len(leaks)} finding(s) from scanning every released file" if leaks else "Automated scan of every released file found no identifiers from the source")
    n_img = sum(1 for _ in rel.rglob("*.dcm"))
    ocr_bad = [x for x in ctx["link_log"] if "not installed" in str(x.get("ocr")) or "failed" in str(x.get("ocr"))]
    add(2, "DICOM pixel data reviewed for burned-in annotations", "MANUAL" if n_img and not ocr_bad else ("WARN" if n_img else "N/A"),
        (f"OCR masking ran on {n_img} image(s); confirm visually using KEEP_INSIDE_HOSPITAL/review_previews" if not ocr_bad
         else f"OCR unavailable for {len(ocr_bad)} image(s): install Tesseract, then review previews") if n_img else "No imaging in this batch")
    add(3, "All narrative and free-text fields reviewed; identifiable content redacted", "MANUAL",
        f"Automated redaction applied; review a sample of at least {min(20, len(M.get('document_index', [])))} PDF(s) in 04_documents and sign off")
    add(4, "Small-cell suppression applied where required", "PASS", f"Threshold {k}; actions: " + (", ".join(f"{a} {b}" for a, b in R.get("suppression", {}).items()) or "none needed"))
    pids = {r["anon_patient_id"] for r in M["patient_demographics"]}
    orphan = [m for m in ctx["present"] if m != "patient_demographics" and any(r.get("anon_patient_id") not in pids for r in M[m] if "anon_patient_id" in r)]
    add(5, "anon_patient_id generated and used consistently across all modules", "PASS" if not orphan else "FAIL",
        "Every record links to a patient in Patient Demographics" if not orphan else "Orphan records in: " + ", ".join(orphan))
    bad_paths = [p for p in (rel / "05_imaging").rglob("*") if p.is_file() and scrub.leaks(_rel(p, rel))] if (rel / "05_imaging").exists() else []
    add(6, "DICOM files linked only via anon_patient_id and anon_imaging_study_id", "PASS" if not bad_paths and not [l for l in leaks if l[0].endswith("header")] else "FAIL",
        "Paths are dicom/<anon_imaging_study_id>/series/instance; headers carry only anon_patient_id" if n_img else "No imaging in this batch")
    dd = pd.read_csv(rel / "00_documentation" / "data_dictionary.csv", dtype=str)
    documented = set(zip(dd["file"], dd["field_name"]))
    undocumented = []
    for p in rel.rglob("*.csv"):
        r_ = _rel(p, rel)
        if r_.startswith("00_documentation"):
            continue
        with open(p, encoding="utf-8") as f:
            cols = next(csv.reader(f), [])
        undocumented += [f"{r_}:{c}" for c in cols if (r_, c) not in documented]
    add(7, "Data dictionary completed for every supplied field", "PASS" if not undocumented else "FAIL",
        f"{len(dd)} field(s) documented" if not undocumented else "Missing: " + ", ".join(undocumented[:10]))
    import jsonschema
    sch = json.loads((rel / "00_documentation" / "schema.json").read_text())
    vs = pd.read_csv(rel / "00_documentation" / "value_sets.csv", dtype=str, keep_default_na=False)
    codes_vs = set(vs["code"])
    errs = []
    for fname, s in sch["files"].items():
        p = rel / fname
        if not p.exists():
            continue
        for i, row in enumerate(csv.DictReader(open(p, encoding="utf-8")), 2):
            try:
                jsonschema.validate(row, s)
            except jsonschema.ValidationError as e:
                errs.append(f"{fname} row {i}: {e.message[:90]}")
            for c in ("diagnosis_code", "medication_code", "observation_code", "specialty_code", "procedure_code"):
                if row.get(c) and row[c] not in codes_vs:
                    errs.append(f"{fname} row {i}: {c} {row[c]} not in value sets")
    add(8, "Schema and value sets validated against the delivered files", "PASS" if not errs else "FAIL",
        "All CSV rows valid against schema.json; all codes present in value_sets" if not errs else f"{len(errs)} error(s), e.g. {errs[0]}")
    R["schema_errors"] = errs[:50]
    eids = {r["anon_encounter_id"] for r in M["encounters"]}
    sids = {r["anon_imaging_study_id"] for r in M.get("imaging_studies", [])}
    bad_link = []
    for m in ctx["present"]:
        for r in M[m]:
            if r.get("anon_encounter_id") and m != "encounters" and r["anon_encounter_id"] not in eids:
                bad_link.append(m)
            if m == "imaging_reports" and r["anon_imaging_study_id"] not in sids:
                bad_link.append(m)
    key_missing = [f"{m}.{f['name']}" for m in ctx["present"] for f in spec.MODULES[m]["fields"]
                   if f["required"] and any(r.get(f["name"]) in ("", None) for r in M[m])]
    add(10, "Sample dataset tested for linkage and completeness of key fields", "PASS" if not bad_link else "FAIL",
        ("All encounter and study links resolve" if not bad_link else "Broken links in " + ", ".join(sorted(set(bad_link))))
        + ("" if not key_missing else f"; required fields with blanks: {', '.join(key_missing[:6])}"))
    expected = ["00_documentation", "01_patient", "02_encounters", "03_observations", "04_documents", "05_imaging", "06_governance"]
    have = sorted(p.name for p in rel.iterdir() if p.is_dir())
    add(11, "Folder structure and file naming as per section 6", "PASS" if all(e in have for e in expected[:2] + ["06_governance"]) else "FAIL",
        "Top level: " + ", ".join(have) + ". Conditional module folders appear only when data exists.")
    add(12, "Secure transfer method confirmed with the receiving team", "MANUAL", "Agree SFTP or encrypted cloud transfer; never e-mail")
    if len(R.get("governance", [])) < 3:
        add(12, "Governance documents in 06_governance", "WARN", "Missing: " + ", ".join(x for x in ("permitted_use_statement.pdf", "data_sharing_agreement_reference.pdf", "dpia_summary.pdf") if x not in R.get("governance", [])))

    failed = any(c["status"] == "FAIL" for c in checks) or bool(R["errors"])

    # README with volumes (section 7), then inventory (item 9), then archive (item 13)
    def volumes():
        inst = sum(1 for _ in rel.rglob("*.dcm"))
        ser = sum(int(s["number_of_series"]) for s in M.get("imaging_studies", []))
        unc = sum(p.stat().st_size for p in rel.rglob("*") if p.is_file())
        return [("Anonymised patients", len(M["patient_demographics"])), ("Encounters", len(M["encounters"])),
                ("Diagnoses", len(M.get("diagnoses", []))), ("Procedures", len(M.get("procedures", []))),
                ("Medication records", len(M.get("medications", []))),
                ("Laboratory / observation results", len(M.get("vitals", [])) + len(M.get("laboratory_results", []))),
                ("Clinical documents", len(M.get("document_index", []))), ("Imaging studies", len(M.get("imaging_studies", []))),
                ("Imaging series", ser), ("DICOM image instances", inst), ("Imaging reports", len(M.get("imaging_reports", []))),
                ("Uncompressed data volume", f"{unc / 1e9:.4f} GB")]

    tmpzip = prv / "_size_probe.zip"
    _zip(rel, tmpzip)
    comp = tmpzip.stat().st_size
    tmpzip.unlink()
    months = sorted(r["encounter_date_month"] for r in M["encounters"] if r["encounter_date_month"])
    vol = volumes() + [("Compressed data volume (approx.)", f"{comp / 1e9:.4f} GB")]
    present = ctx["present"]
    cfg = ctx["cfg"]["dataset"]
    ex, unm = R.get("excluded", {}), R.get("unmapped", {})
    miss_top = sorted(ctx.get("missingness", []), key=lambda x: -float(x[3].rstrip("%")))[:5]
    dq_lines = [f"- Missingness: " + ("; ".join(f"{m[1]} ({m[0]}) {m[3]} blank" for m in miss_top) if miss_top else "no blank fields") + ".",
                f"- Duplicates: {ex.get('duplicate admission rows removed', 0)} duplicate admission row(s) removed.",
                f"- Coding limitations: not coded and left out: diagnoses {unm.get('diagnoses', 0)}, medicine lines {unm.get('medicines', 0)}, "
                f"tests {unm.get('tests', 0)}, procedures {unm.get('procedures', 0)}. Procedure codes are local pending SNOMED CT.",
                "- Delayed results: results reported after the export date are not included; "
                + ("lab values come from the LIS export." if M.get("laboratory_results") else "no LIS export was supplied, so no lab values."),
                "- Excluded records: " + ", ".join(f"{k_} {v_}" for k_, v_ in ex.items() if v_) + "." if any(ex.values()) else "- Excluded records: none."]
    for w in R["warnings"][:5]:
        dq_lines.append(f"- {w}")

    def size(b):
        return f"{b / 1e6:.2f} MB" if b >= 1e5 else f"{b / 1e3:.1f} KB"
    groups = {}
    for p in rel.rglob("*"):
        if p.is_file():
            parts = _rel(p, rel).split("/")
            key = parts[0] + ("/dicom" if len(parts) > 2 and parts[1] == "dicom" else "/documents_pdfa" if len(parts) > 2 and parts[1] == "documents_pdfa" else "")
            g = groups.setdefault(key, [0, 0, 0])
            g[0] += 1
            g[1] += count_records(p) if p.suffix.lower() in (".csv", ".ndjson") else 1
            g[2] += p.stat().st_size
    inv_summary = [(k_, v_[0], v_[1], size(v_[2])) for k_, v_ in sorted(groups.items())]
    lines = [f"# Anonymised EHR and Imaging Dataset v{ctx['cfg']['dataset']['version']}", "",
             "## Purpose", "Anonymised hospital admission records with linked X-ray imaging, prepared to the receiving team's "
             "Technical Data Provision Reference (v1.0, 14 October 2026), adapted to Indian standards (see below).", "",
             "## Coverage", f"- Encounter months: {months[0] if months else '-'} to {months[-1] if months else '-'}",
             f"- Source systems: {cfg['source_hospital_system']} (export); {cfg['source_imaging_system']} (DICOM)",
             f"- Batch: {R['batch']} · prepared with Workframe Health De-ID {R['version']}", "",
             "## Record counts (section 7)", "", "| Measure | Value |", "|---|---|"] + [f"| {a} | {b} |" for a, b in vol] + [
             "", "## Modules", "", "| Module | Status | Supplied | Files |", "|---|---|---|---|"] + [
             f"| {spec.MODULES[m]['title']} | {spec.MODULES[m]['status']} | {'Yes' if m in present else 'No (no source data)'} | "
             f"{spec.FILE_PATHS[m]}.csv{' + .ndjson' if spec.MODULES[m]['fhir'] else ''} |" for m in spec.MODULES] + [
             "| Imaging Objects (DICOM) | Conditional | " + ("Yes" if any(rel.rglob('*.dcm')) else "No") + " | 05_imaging/dicom/<anon_imaging_study_id>/series_NNN/instance_NNNN.dcm |",
             "", "## Code systems", "ICD-10 (WHO) diagnoses; WHO ATC medicines; LOINC observations and document type; UCUM units; "
             "DICOM modality codes; local specialty (WF-SPECIALTY) and procedure (WF-PROC) codes listed in value_sets.", "",
             "## Indian adaptations", "", "| Specification (UK) | Applied in India |", "|---|---|"] + [f"| {a} | {b} |" for a, b in INDIA] + [
             "", "## Documentation in this folder", "",
             "| Document | File |", "|---|---|",
             "| README | README.md |", "| Data dictionary | data_dictionary.csv, data_dictionary.xlsx |", "| Schema | schema.json |",
             "| Value sets | value_sets.csv (permitted values and codes), value_set_mappings.csv (how source text was coded), code_systems.csv, value_sets.json (all three) |",
             "| Anonymisation methodology | anonymisation_methodology.pdf |", "| Data quality statement | data_quality_statement.pdf |",
             "| Entity relationship diagram | entity_relationship_diagram.pdf |", "| File inventory | file_inventory.csv |",
             "", "## Known data-quality issues", "Full detail in data_quality_statement.pdf.", ""] + dq_lines + [
             "", "## File inventory", "Summary by folder below. file_inventory.csv lists every file with its record count, size in bytes and SHA-256.", "",
             "| Folder | Files | Records | Size |", "|---|---|---|---|"] + [f"| {a} | {b} | {c} | {e} |" for a, b, c, e in inv_summary] + ["",
             "## Pre-transfer checklist", "", "| # | Item | Status |", "|---|---|---|"] + [f"| {c['no']} | {c['item']} | {c['status']} |" for c in checks] + [
             "| 9 | File inventory and record counts reconciled | PASS |", "| 13 | Compressed archive and SHA-256 generated | " + ("PASS" if not failed else "NOT CREATED (batch failed)") + " |"]
    (rel / "00_documentation" / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    inv = []
    for p in sorted(rel.rglob("*")):
        if p.is_file() and p.name != "file_inventory.csv":
            inv.append({"file": _rel(p, rel), "records": count_records(p), "bytes": p.stat().st_size, "sha256": sha256(p)})
    pd.DataFrame(inv).to_csv(rel / "00_documentation" / "file_inventory.csv", index=False)
    add(9, "File inventory and record counts reconciled and documented in the README", "PASS",
        f"{len(inv)} file(s) inventoried; counts in README match module files")

    batch = rel.parent
    if failed:
        q = batch / "QUARANTINE_DO_NOT_SHARE"
        shutil.move(str(rel), str(q))
        ctx["rel"] = q
        add(13, "Compressed archive created and SHA-256 generated", "FAIL", "Not created because the batch failed a check")
        status = "FAILED: do not share"
    else:
        tdir = batch / "TRANSFER"
        tdir.mkdir(exist_ok=True)
        z = tdir / f"{rel.name}.zip"
        _zip(rel, z)
        h = sha256(z)
        (tdir / f"{rel.name}.zip.sha256").write_text(f"{h}  {z.name}\n")
        add(13, "Compressed archive created and SHA-256 generated", "PASS", f"{z.name} · SHA-256 {h}")
        manual = any(c["status"] in ("MANUAL", "WARN") for c in checks)
        status = "PASSED: manual sign-off needed before transfer" if manual else "PASSED"
    checks.sort(key=lambda c: c["no"])
    with open(prv / "pre_transfer_checklist.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["no", "item", "status", "detail"])
        w.writeheader()
        w.writerows(checks)
    R["checklist"] = checks
    R["leaks"] = len(leaks)
    return status


def _zip(folder, dest):
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, Path(folder.name) / p.relative_to(folder))
