"""Self-test: run once on the hospital computer after setup (setup does this for you).
Creates synthetic Keystone-style exports and X-rays with the REAL pydicom library, runs the whole pipeline twice,
and checks the output against the receiving team's specification. Uses its own test vault, never the hospital's."""
import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wf_deid.paths import base  # noqa: E402

HERE = base()


def main():
    from wf_deid import pipeline, samples, spec, xray
    if not xray.available():
        print("FAIL: pydicom is not installed. Run setup again.")
        return 1
    import pydicom
    work = HERE / "selftest_run"
    shutil.rmtree(work, ignore_errors=True)
    print("Creating synthetic test data ...")
    d = samples.make_all(work / "data")
    problems, notes = [], []

    def run(tag):
        return pipeline.run(d["exports"], d["xrays"], work / f"out_{tag}", "selftest-password", lab_file=d["labs"],
                            log=lambda m: None, private_dir=work / "private", radiology_paths=d["reports"])

    print("Run 1 ...")
    r1 = run("1")
    rel = Path(r1["release_folder"])
    prv = Path(r1["private_folder"])
    if r1["status"].startswith("FAILED"):
        problems.append("batch failed: " + "; ".join(f"{c['no']} {c['detail']}" for c in r1["checklist"] if c["status"] == "FAIL"))
    if r1["leaks"]:
        problems.append(f"{r1['leaks']} identifier(s) found in the release folder")

    # spec folders and files
    for f in ["00_documentation/README.md", "00_documentation/data_dictionary.csv", "00_documentation/data_dictionary.xlsx",
              "00_documentation/schema.json", "00_documentation/value_sets.csv", "00_documentation/entity_relationship_diagram.pdf",
              "00_documentation/anonymisation_methodology.pdf", "00_documentation/data_quality_statement.pdf",
              "00_documentation/file_inventory.csv", "01_patient/patient_demographics.csv", "01_patient/patient_demographics.ndjson",
              "02_encounters/encounters.csv", "02_encounters/diagnoses.csv", "02_encounters/procedures.csv", "02_encounters/medications.csv",
              "03_observations/vitals.csv", "03_observations/laboratory_results.csv", "04_documents/document_index.ndjson",
              "05_imaging/imaging_studies.ndjson", "05_imaging/imaging_reports.ndjson"]:
        if not (rel / f).exists():
            problems.append(f"missing {f}")
    z = list((Path(r1["batch_folder"]) / "TRANSFER").glob("*.zip"))
    if not z or not Path(str(z[0]) + ".sha256").exists():
        problems.append("transfer archive or SHA-256 missing")

    # X-ray traps
    rv = list(csv.DictReader(open(prv / "xray_review_queue.csv", encoding="utf-8-sig")))
    un = list(csv.DictReader(open(prv / "xray_unmatched.csv", encoding="utf-8-sig")))
    log = list(csv.DictReader(open(prv / "xray_link_log.csv", encoding="utf-8-sig")))
    if not any(x["xray_name"] == "NITHA GOWDA" or "NITHA" in x["xray_name"] for x in rv):
        problems.append("misspelt-name X-ray was not sent to review")
    if not any("KH999999" in x["xray_id"] for x in un):
        problems.append("X-ray of a person not in the export was not left unmatched")
    if not any("outside admission" in x["link_method"] for x in log):
        problems.append("outpatient X-ray was not linked at patient level")
    burned = [x for x in log if x["original_file"].endswith("IMG0001_1.dcm")]
    if burned and int(burned[0]["masked_text_regions"]) == 0:
        notes.append("No printed text was found inside the test X-ray. Install Tesseract (see README): real X-rays can carry the name inside the image.")

    # DICOM headers and layout
    pids = {r["anon_patient_id"] for r in csv.DictReader(open(rel / "01_patient/patient_demographics.csv", encoding="utf-8"))}
    for f in (rel / "05_imaging" / "dicom").rglob("*.dcm"):
        parts = f.relative_to(rel / "05_imaging" / "dicom").parts
        if len(parts) != 3 or not parts[1].startswith("series_") or not parts[2].startswith("instance_"):
            problems.append(f"DICOM path not in study/series/instance form: {f}")
        ds = pydicom.dcmread(str(f))
        for kw in ("PatientBirthDate", "InstitutionName", "OperatorsName", "ImageComments", "StationName", "DeviceSerialNumber"):
            if kw in ds and str(getattr(ds, kw) or ""):
                problems.append(f"{f.name}: {kw} still present")
        if str(ds.PatientID) not in pids:
            problems.append(f"{f.name}: PatientID is not an anon_patient_id")
        if str(getattr(ds, "StudyDate", ""))[6:] not in ("", "01"):
            problems.append(f"{f.name}: study date not generalised to month")

    # coding of the sample discharge summary (the hospital's real layout)
    dx = list(csv.DictReader(open(rel / "02_encounters/diagnoses.csv", encoding="utf-8")))
    if not any(x["diagnosis_code"].startswith("M06") for x in dx):
        problems.append("seronegative rheumatoid arthritis was not coded to M06")
    for f in ("encounters", "diagnoses", "medications"):
        if not list(csv.DictReader(open(rel / f"02_encounters/{f}.csv", encoding="utf-8"))):
            problems.append(f"{f} is empty")

    # radiology reports
    rrv = list(csv.DictReader(open(prv / "report_review_queue.csv", encoding="utf-8-sig")))
    run_ = list(csv.DictReader(open(prv / "report_unmatched.csv", encoding="utf-8-sig")))
    studies = list(csv.DictReader(open(rel / "05_imaging/imaging_studies.csv", encoding="utf-8")))
    reps_ = list(csv.DictReader(open(rel / "05_imaging/imaging_reports.csv", encoding="utf-8")))
    if not any("ARJUN" in x["report_name"].upper() for x in run_):
        problems.append("radiology report of a person not in the export was not left unmatched")
    if not any("ANITH" in x["report_name"].upper() for x in rrv):
        problems.append("radiology report with a misspelt name was not sent to review")
    if not any(x["number_of_instances"] == "0" and x["modality"] == "CT" for x in studies):
        problems.append("CT report without images did not become a report-only imaging study")
    if r1["imaging"]["reports_merged_with_images"] < 10:
        problems.append("radiology reports were not attached to the matching X-ray studies")
    if not reps_:
        problems.append("no imaging reports written")

    # IDs stay the same across batches
    print("Run 2 (checks that the same patients keep the same IDs) ...")
    r2 = run("2")
    p2 = {r["anon_patient_id"] for r in csv.DictReader(open(Path(r2["release_folder"]) / "01_patient/patient_demographics.csv", encoding="utf-8"))}
    if p2 != pids:
        problems.append("patient IDs changed between batches")
    print("Run 3 (records only: no X-rays, no reports) ...")
    r3 = pipeline.run(d["exports"], None, work / "out_3", "selftest-password", log=lambda m: None, private_dir=work / "private")
    if r3["status"].startswith("FAILED"):
        problems.append("a batch without X-rays or reports failed")
    try:
        pipeline.run(d["exports"], None, work / "out_bad", "wrong-password", log=lambda m: None, private_dir=work / "private")
        problems.append("a wrong vault password was accepted")
    except pipeline.StopRun:
        pass

    print(f"pydicom {getattr(pydicom, '__version__', '?')} | status: {r1['status']}")
    print("records:", json.dumps(r1["counts"]))
    for n in notes:
        print("NOTE:", n)
    if problems:
        print("SELF-TEST FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("SELF-TEST PASSED. Example output to inspect:", rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
