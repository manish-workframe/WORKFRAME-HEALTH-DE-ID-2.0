"""Synthetic Keystone-style test data. Every name, number and date is invented.

Creates, in the shapes a Keystone hospital is likely to export:
  keystone_ip_register.xlsx          list of admissions, one row each (60 admissions)
  keystone_discharge_summaries.xlsx  three discharge summaries in the report layout (like the sample PDF)
  keystone_lis_results.xlsx          lab results for some admissions
  xrays/                             ImagePilot-style DICOM X-rays, with deliberate traps:
     a name burned into an image, a misspelt name with no patient number, an X-ray taken outside any
     admission (outpatient), and an X-ray of a patient who is not in the export.
"""
import datetime as dt
import random
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

FIRST_F = ["KAVITHA", "LAKSHMI", "PRIYA", "ANITHA", "SUMA", "REKHA", "DEEPA", "SHOBHA", "NAGARATHNA", "GEETHA", "PADMA", "ROOPA", "SUNITHA", "MANJULA", "VANI"]
FIRST_M = ["ANIL", "RAVI", "SURESH", "MANJUNATH", "PRAKASH", "VENKATESH", "MAHESH", "RAJU", "GIRISH", "NAVEEN", "SHIVA", "KIRAN", "FAROOQ", "JOSEPH", "IMRAN"]
LAST = ["GOWDA", "RAO", "SHETTY", "MURTHY", "REDDY", "NAIDU", "HEGDE", "KUMAR", "PATIL", "BHAT", "KHAN", "DSOUZA", "IYER", "NAIK", "PRASAD"]
CASES = [  # diagnosis text, specialty, complaints, investigations, treatment, discharge meds, surgery, xray body part
    ("COMMUNITY ACQUIRED PNEUMONIA", "PULMONOLOGIST", "FEVER; COUGH WITH SPUTUM; BREATHLESSNESS", "CBC; CRP; CHEST X RAY PA; RFT",
     "INJ-MONOCEF-1GM-IV-BD; INJ-AZEE-500-IV-OD; INJ-PANTOP-40-IV-OD; NEB-DUOLIN-TDS", "TAB-AUGMENTIN 625 1-0-1 5 DAYS; TAB-DOLO 650 1-1-1 3 DAYS", "", "CHEST"),
    ("ACUTE GASTROENTERITIS WITH DEHYDRATION", "PHYSICIAN", "LOOSE STOOLS; VOMITING; ABDOMINAL PAIN", "CBC; RFT; SODIUM; POTASSIUM",
     "IVF-RL-500ML; INJ-ONDEM-4MG-IV-TDS; INJ-PANTOP-40-IV-BD; INJ-METROGYL-100ML-IV-TDS", "TAB-OFLOX 200 1-0-1 5 DAYS; ELECTRAL SACHET 1-1-1 3 DAYS", "", ""),
    ("DENGUE FEVER WITH THROMBOCYTOPENIA", "PHYSICIAN", "FEVER; BODY PAIN; HEADACHE", "CBC; PLATELET; DENGUE NS1; LFT",
     "IVF-NS-500ML; INJ-NEOMOL-1GM-IV-SOS; INJ-PANTOP-40-IV-OD", "TAB-DOLO 650 1-1-1 3 DAYS", "", ""),
    ("URINARY TRACT INFECTION", "PHYSICIAN", "BURNING MICTURITION; FEVER", "CBC; URINE ANALYSIS; RFT",
     "INJ-MONOCEF-1GM-IV-BD; IVF-NS-500ML; INJ-PANTOP-40-IV-OD", "TAB-CIPLOX 500 1-0-1 5 DAYS", "", ""),
    ("TYPE 2 DIABETES MELLITUS; SYSTEMIC HYPERTENSION", "PHYSICIAN", "GIDDINESS; GENERALISED WEAKNESS", "CBC; RBS; HBA1C; RFT; URINE ANALYSIS",
     "INJ-ACTRAPID-SC-TDS; TAB-TELMA-40-OD; INJ-PANTOP-40-IV-OD", "TAB-GLYCOMET 500 1-0-1 30 DAYS; TAB-TELMA 40 1-0-0 30 DAYS", "", ""),
    ("CONGESTIVE CARDIAC FAILURE", "CARDIOLOGIST", "BREATHLESSNESS; PEDAL OEDEMA", "CBC; RFT; SODIUM; POTASSIUM; CHEST X RAY PA; ECG",
     "INJ-LASIX-40-IV-BD; TAB-ECOSPRIN-75-OD; TAB-ATORVA-20-HS", "TAB-DYTOR 10 1-0-0 30 DAYS; TAB-ECOSPRIN 75 0-1-0 30 DAYS; TAB-ATORVA 20 0-0-1 30 DAYS", "", "CHEST"),
    ("CASE OF SERO NEGATIVE ARTHRITIS/ RHEUMATOID ARTHRITIS", "PHYSICIAN", "GIDDINESS; HEAD ACHE; BODY PAIN; LOWER LIMB PAIN",
     "ANTI CCP; CBC; CRP; ESR; LFT; RA TEST; RFT; URINE ANALYSIS", "IVF-NS-500ML WITH INJ-OPTINEUROM; INJ-NEOMOL-1GM-IV; INJ-PANTOP-40-IV-BD",
     "TAB-INTACOXIA 90 0-0-1 5 DAYS; TAB-PANTOP 40 1-0-1 5 DAYS; TAB-HCQS 200 0-0-1 30 DAYS", "", ""),
    ("FRACTURE DISTAL END RADIUS RIGHT", "ORTHOPAEDICS", "PAIN AND SWELLING RIGHT WRIST AFTER FALL", "CBC; RFT; X RAY WRIST",
     "INJ-MONOCEF-1GM-IV-BD; INJ-TRAMAZAC-50-IV-SOS; INJ-PANTOP-40-IV-OD", "TAB-ZERODOL P 1-0-1 5 DAYS; TAB-SHELCAL 500 0-1-0 30 DAYS", "ORIF RIGHT DISTAL RADIUS", "WRIST"),
    ("ACUTE EXACERBATION OF COPD", "PULMONOLOGIST", "BREATHLESSNESS; WHEEZE; COUGH", "CBC; CHEST X RAY PA; RFT",
     "NEB-DUOLIN-QID; NEB-BUDECORT-BD; INJ-HYDROCORT-100-IV-TDS; INJ-MONOCEF-1GM-IV-BD", "TAB-MONTAIR LC 0-0-1 10 DAYS; TAB-DOXY 100 1-0-1 5 DAYS", "", "CHEST"),
    ("ACUTE CALCULOUS CHOLECYSTITIS", "SURGEON", "RIGHT UPPER ABDOMINAL PAIN; VOMITING", "CBC; LFT; RFT; ULTRASOUND ABDOMEN",
     "INJ-PIPZO-4.5-WITH-NS-100ML-IV-TDS; INJ-PANTOP-40-IV-BD; INJ-TRAMAZAC-50-IV-SOS", "TAB-AUGMENTIN 625 1-0-1 5 DAYS; TAB-ULTRACET 1-0-1 3 DAYS", "LAPAROSCOPIC CHOLECYSTECTOMY", ""),
]
PINS = ["560034", "560076", "560011", "560068", "560043", "560100", "562125", "560097", "571401", "600028", "500032"]
PAYERS = ["CASH", "CASH", "INSURANCE (TPA)", "PMJAY", "CASH", "CORPORATE", "CGHS"]
WARDS = ["GENERAL", "GENERAL", "SEMI PRIVATE", "PRIVATE", "ICU"]


def _people(rng):
    out = []
    for i in range(60):
        f = rng.random() < 0.5
        out.append({"name": f"{'Ms.' if f else 'Mr.'} {rng.choice(FIRST_F if f else FIRST_M)} {rng.choice(LAST)}",
                    "sex": "Female" if f else "Male", "age": rng.choice([24, 33, 38, 45, 47, 52, 53, 58, 61, 66, 72, 75, 83]),
                    "uhid": f"KH{240000 + i * 7}", "mobile": f"9{rng.randint(100000000, 999999999)}",
                    "pin": rng.choice(PINS[:6] * 4 + PINS[6:]), "case": i % len(CASES)})
    return out


def register(path, rng):
    people = _people(rng)
    rows = []
    base = dt.date(2026, 9, 1)
    for i, p in enumerate(people):
        dx, spec, comp, inv, tx, dmed, surg, _ = CASES[p["case"]]
        adm = base + dt.timedelta(days=i % 75)
        los = rng.randint(1, 6)
        first = p["name"].split()[1].title()
        rows.append({
            "Sl No": i + 1, "UHID": p["uhid"], "IP No": f"IP2627/{100200 + i}", "Patient Name": p["name"], "Age": f"{p['age']} Yrs",
            "Gender": p["sex"], "Mobile No": p["mobile"], "Address": f"{rng.randint(1, 99)}, {rng.choice(['4TH CROSS', 'MAIN ROAD', '2ND STAGE'])}, BANGALORE",
            "Pin Code": p["pin"], "Patient Category": rng.choice(PAYERS), "DOA": adm.strftime("%d/%m/%y"),
            "DOD": (adm + dt.timedelta(days=los)).strftime("%d/%m/%y"), "Ward": rng.choice(WARDS), "Bed No": rng.randint(1, 40),
            "Consultant": f"DR. {rng.choice(FIRST_M)} {rng.choice(LAST)} ({spec})", "Consultant Type": "Specialist",
            "Brief History": f"C/O {comp}. Brought by son {rng.choice(FIRST_M).title()}, contact {p['mobile'][:5]} {p['mobile'][5:]}" if i % 4 == 0 else f"C/O {comp}",
            "Clinical Examination": f"Temp {rng.choice([96, 98, 99, 101])}, BP {rng.choice(['130/90', '120/80', '140/90', '110/70'])}, HR {rng.randint(72, 110)} ; PA SOFT ; RS B/L AIR ENTRY GOOD ; CVS S1 S2+",
            "Investigations": inv, "Treatment Given": tx, "Diagnosis Details": dx + (f" (patient {first})" if i % 9 == 0 else ""),
            "Surgery Name": surg, "Surgery Date": (adm + dt.timedelta(days=1)).strftime("%d/%m/%y") if surg else "",
            "Medication / Prescription": dmed,
            "X-Ray Report": ("Cardiac silhouette enlarged. Bilateral lower zone haziness." if CASES[p["case"]][7] == "CHEST" and i % 2 == 0 else ""),
        })
    pd.DataFrame(rows).to_excel(path, index=False)
    return people, rows


def summaries(path):
    """Three discharge summaries laid out like the Keystone Crystal Reports printout, exported to Excel."""
    docs = [
        ("Ms.MEENAKSHI SUBRAMANYA", "Female", 53, "ADM2627/000301", "13/11/26 7:15:00AM", "15/11/26 2:31:00PM", "GENERAL, Bed no:6",
         ["-GIDDINESS", "-HEAD ACHE", "-BODY PAIN", "-LOWER LIMB PAIN"], ("96", "130/90", "91"),
         ["ANTI CCP", "CBC-COMPLETE BLOOD COUNT", "CRP QUANTAVITE", "ERYTHROCYTE SEDIMENTATION RATE (ESR)", "RA-TEST(RHEUMATOID FACTORS)"],
         ["IVF-NS-500ML", "WITH", "INJ-OPTINEUROM", "INJ-NEOMOL-1GM-IV", "INJ-PANTOP-40MH-IV-BD", "TAB-DOLO-650", "TAB-INTACOXIA-90"],
         ["2013", "-CASE OF SERO NEGATIVE ARTHRITIS/ RHEUMATOID ARTHRITIS"], ("NAGESH RAO(PHYSICIAN)", "9886012345", "Specialist"),
         [("TAB-SKEEF", "500", "1-0-1", "5 DAYS"), ("TAB-PANTOP", "40", "1-0-1", "5 DAYS")]),
        ("Mr.HARISH CHANDRA", "Male", 64, "ADM2627/000302", "14/11/26 9:40:00AM", "18/11/26 11:05:00AM", "ICU, Bed no:3",
         ["-BREATHLESSNESS", "-PEDAL OEDEMA"], ("98", "150/90", "104"), ["CBC-COMPLETE BLOOD COUNT", "RENAL FUNCTION TEST", "ECG"],
         ["INJ-LASIX-40-IV-BD", "TAB-ECOSPRIN-75", "TAB-ATORVA-20"], ["-CONGESTIVE CARDIAC FAILURE", "-SYSTEMIC HYPERTENSION"],
         ("PRADEEP KULKARNI(CARDIOLOGIST)", "9900554433", "Specialist"), [("TAB-DYTOR", "10", "1-0-0", "30 DAYS"), ("TAB-TELMA", "40", "1-0-0", "30 DAYS")]),
        ("Ms.FATHIMA BEGUM", "Female", 29, "ADM2627/000303", "15/11/26 6:10:00PM", "16/11/26 4:00:00PM", "GENERAL, Bed no:12",
         ["-LOOSE STOOLS", "-VOMITING"], ("99", "110/70", "96"), ["CBC-COMPLETE BLOOD COUNT", "SODIUM", "POTASSIUM"],
         ["IVF-RL-500ML", "INJ-ONDEM-IV-BD", "INJ-PANTOP-40-IV-BD"], ["-ACUTE GASTROENTERITIS"], ("NAGESH RAO(PHYSICIAN)", "9886012345", "Specialist"),
         [("TAB-OFLOX", "200", "1-0-1", "5 DAYS")]),
    ]
    grid = []
    for (name, sex, age, adm, doa, dod, ward, comp, vit, inv, tx, dx, con, meds) in docs:
        grid += [["", "", "Discharge Summary", ""],
                 ["Patient Name :", name, "Gender / Age", f": {sex} / {age} years"],
                 ["Admission No :", adm, "Admission Date", f": {doa}"],
                 ["Ward", f": {ward}", "Date of Discharge", f": {dod}"],
                 ["Brief History"], ["C/O"]] + [[c] for c in comp] + \
                [["Clinical Examination"], ["Temp", "BP", "HR Pulse"], list(vit), ["PA :", "SOFT.BS+"], ["RS :", "B/L AIR ENTRY GOOD"], ["CVS :", "S1.S2+"],
                 ["Investigations"]] + [[i] for i in inv] + [["Treatment Given"]] + [[t] for t in tx] + \
                [["Diagnosis Details"]] + [[d] for d in dx] + \
                [["Consultant Details"], ["Consultant", "Mobile_Number", "Consultant_Type"], list(con),
                 ["Surgery_Name", "Surgery_Date", "Notes"], ["Advice on Discharge"], ["Medication / Priscription"],
                 ["Medication Name", "Strength", "Dosage", "No_of_days"]] + [list(m) for m in meds] + \
                [["", "", "", "SIGNATURE OF DOCTOR"], [""]]
    pd.DataFrame(grid).to_excel(path, index=False, header=False)


def lis(path, people, rows, rng):
    out = []
    for p, r in list(zip(people, rows))[:30]:
        for test, val, unit in (("HAEMOGLOBIN", round(rng.uniform(9.5, 15.5), 1), "g/dL"), ("TOTAL WBC COUNT", rng.randint(4, 16) * 1000 / 1000, "10*3/uL"),
                                ("PLATELET COUNT", rng.randint(60, 400), "10*3/uL"), ("SERUM CREATININE", round(rng.uniform(0.6, 2.4), 2), "mg/dL")):
            out.append({"UHID": p["uhid"], "IP No": r["IP No"], "Test Name": test, "Result": val, "Units": unit,
                        "Result Date": r["DOA"], "Status": "Final"})
    pd.DataFrame(out).to_excel(path, index=False)


def _pixels(seed, burn=None, size=(1024, 820)):
    rs = np.random.default_rng(seed)
    h, w = size
    yy, xx = np.mgrid[0:h, 0:w]
    body = np.exp(-(((xx - w / 2) / (w * 0.38)) ** 2 + ((yy - h * 0.55) / (h * 0.45)) ** 2))
    lungs = np.exp(-(((xx - w * 0.33) / (w * 0.12)) ** 2 + ((yy - h * 0.5) / (h * 0.25)) ** 2)) + \
            np.exp(-(((xx - w * 0.67) / (w * 0.12)) ** 2 + ((yy - h * 0.5) / (h * 0.25)) ** 2))
    img = np.clip(600 + 2600 * body - 1500 * lungs + rs.normal(0, 60, (h, w)), 0, 4095).astype(np.uint16)
    if burn:
        pil = Image.fromarray((img / 16).astype(np.uint8))
        d = ImageDraw.Draw(pil)
        font = None
        for fnt in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
            try:
                font = ImageFont.truetype(fnt, 34)
                break
            except Exception:
                continue
        d.text((24, 20), burn, fill=255, font=font or ImageFont.load_default())
        img[np.array(pil) == 255] = 4095
    return img


def xrays(folder, people, rows):
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    CR = "1.2.840.10008.5.1.4.1.1.1"
    plan = []
    for i, (p, r) in enumerate(zip(people, rows)):
        part = CASES[p["case"]][7]
        if part:
            d = dt.datetime.strptime(r["DOA"], "%d/%m/%y").date() + dt.timedelta(days=1)
            plan.append((p, part, d, None, None, None))
    # traps
    p0, r0 = people[0], rows[0]
    plan[0] = (plan[0][0], plan[0][1], plan[0][2], f"{plan[0][0]['name'].split(' ', 1)[1]}  {plan[0][0]['uhid']}", None, None)
    q = plan[1][0]
    plan[1] = (q, plan[1][1], plan[1][2], None, q["name"].split(" ", 1)[1].replace("A", "", 1), "")   # misspelt name, no ID
    plan.append((people[2], "CHEST", dt.date(2026, 12, 20), None, None, None))                        # outpatient X-ray, no admission
    plan.append(({"name": "Mr. ROHAN DSOUZA", "sex": "Male", "age": 35, "uhid": "KH999999"}, "CHEST", dt.date(2026, 10, 5), None, None, None))
    written = 0
    for n, (p, part, d, burn, oname, oid) in enumerate(plan, 1):
        study, series = generate_uid(), generate_uid()
        views = ["PA", "LAT"] if part == "CHEST" and n % 5 == 0 else (["PA"] if part == "CHEST" else ["AP", "LAT"])
        for v_i, view in enumerate(views, 1):
            px = _pixels(n * 10 + v_i, burn if v_i == 1 else None)
            fm = FileMetaDataset()
            fm.MediaStorageSOPClassUID = CR
            fm.MediaStorageSOPInstanceUID = generate_uid()
            fm.TransferSyntaxUID = ExplicitVRLittleEndian
            ds = Dataset()
            ds.file_meta = fm
            ds.SOPClassUID, ds.SOPInstanceUID = CR, fm.MediaStorageSOPInstanceUID
            ds.StudyInstanceUID, ds.SeriesInstanceUID = study, series
            nm = (oname or p["name"].split(" ", 1)[1])
            ds.PatientName = nm.replace(" ", "^", 1)
            ds.PatientID = p["uhid"] if oid is None else oid
            ds.PatientBirthDate = dt.date(d.year - p["age"], 6, 15).strftime("%Y%m%d")
            ds.PatientSex = "F" if p["sex"].startswith("F") else "M"
            ds.PatientAge = f"{p['age']:03d}Y"
            ds.StudyDate = ds.SeriesDate = ds.ContentDate = d.strftime("%Y%m%d")
            ds.StudyTime = ds.ContentTime = "101500"
            ds.AccessionNumber = f"CR{20000 + n}"
            ds.StudyID = str(n)
            ds.InstanceNumber = v_i
            ds.Modality = "CR"
            ds.BodyPartExamined = part
            ds.ViewPosition = view
            ds.StudyDescription = f"{part} {view}"
            ds.InstitutionName = "SAMPLE MULTISPECIALITY HOSPITAL"
            ds.ReferringPhysicianName = "RAO^NAGESH"
            ds.OperatorsName = "TECH^RAMESH"
            ds.StationName = "IMAGEPILOT01"
            ds.DeviceSerialNumber = "KM-IP-0042"
            ds.ImageComments = f"Patient {nm} ph {p.get('mobile', '9845067890')}"
            ds.Rows, ds.Columns = px.shape
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
            ds.BurnedInAnnotation = "YES" if (burn and v_i == 1) else "NO"
            ds.PixelData = px.tobytes()
            ds.save_as(str(folder / f"IMG{n:04d}_{v_i}.dcm"), enforce_file_format=True)
            written += 1
    return written


FINDINGS = {
    "CHEST": ("X-RAY CHEST PA VIEW", "Bilateral lower zone haziness noted. Cardiac silhouette is mildly enlarged. Costophrenic angles are clear.",
              "Features suggestive of bilateral lower lobe consolidation. Mild cardiomegaly."),
    "WRIST": ("X-RAY RIGHT WRIST AP/LAT", "Transverse fracture of the distal radius with dorsal angulation. Ulnar styloid intact.",
              "Fracture distal end radius, right."),
}


def radiology(root, people, rows):
    """A report list (Excel) for most X-ray patients, plus Word/PDF reports with traps:
    - ultrasound for a gastroenteritis patient with no X-ray (report-only study), with UHID
    - a PDF holding two reports: one known patient by IP number, one person not in the export
    - a Word report with a misspelt name and no patient number (goes to review)"""
    import docx
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from . import pdfdoc  # registers fonts
    root = Path(root)
    folder = root / "radiology_reports"
    folder.mkdir(parents=True, exist_ok=True)
    lst = []
    for p, r in zip(people, rows):
        part = CASES[p["case"]][7]
        if not part:
            continue
        d = dt.datetime.strptime(r["DOA"], "%d/%m/%y").date() + dt.timedelta(days=1)
        title, f, imp = FINDINGS[part]
        lst.append({"Reg No": p["uhid"], "IP Number": r["IP No"], "Patient Name": p["name"], "Age/Sex": f"{p['age']} Y / {p['sex'][0]}",
                    "Exam Date": d.strftime("%d-%m-%Y"), "Investigation": title,
                    "Findings": f + f" Compared with film of {p['name'].split(' ', 1)[1].title()} dated {d.strftime('%d/%m/%Y')}.",
                    "Impression": imp, "Reported By": "Dr. Suma Hegde, MD (Radiodiagnosis)", "Status": "Final"})
    pd.DataFrame(lst).to_excel(root / "radiology_report_list.xlsx", index=False)
    # 1. ultrasound, Word, UHID present, no X-ray for this admission (report-only)
    ge = next((p, r) for p, r in zip(people, rows) if CASES[p["case"]][0].startswith("ACUTE GASTRO"))
    p, r = ge
    d = dt.datetime.strptime(r["DOA"], "%d/%m/%y").date()
    doc = docx.Document()
    doc.add_paragraph("SAMPLE MULTISPECIALITY HOSPITAL - DEPARTMENT OF RADIOLOGY   Ph: 080-23456789")
    t = doc.add_table(rows=3, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = f"Patient Name : {p['name']}", f"Age/Sex : {p['age']} Yrs / {p['sex'][0]}"
    t.cell(1, 0).text, t.cell(1, 1).text = f"UHID : {p['uhid']}", f"Date : {d.strftime('%d/%m/%Y')}"
    t.cell(2, 0).text, t.cell(2, 1).text = "Ref By : Dr. Ravi Kulkarni", f"Mobile : {p['mobile']}"
    doc.add_paragraph("USG ABDOMEN AND PELVIS")
    doc.add_paragraph("FINDINGS:")
    doc.add_paragraph("Liver is normal in size and echotexture. Gall bladder is well distended with no calculi. "
                      "Mild bowel wall thickening is seen in the right iliac fossa with a few prominent mesenteric lymph nodes.")
    doc.add_paragraph("IMPRESSION:")
    doc.add_paragraph("Features suggestive of acute enterocolitis. No free fluid.")
    doc.add_paragraph("Dr. Suma Hegde")
    doc.add_paragraph("MD Radiodiagnosis, KMC Reg No 45678")
    doc.save(folder / "usg_report_0001.docx")
    # 2. PDF with two reports
    known = next((p, r) for p, r in zip(people, rows) if CASES[p["case"]][0].startswith("ACUTE CALCULOUS"))
    c = canvas.Canvas(str(folder / "ct_reports_batch.pdf"), pagesize=A4)
    for who, ip, title, body, imp in (
            (known[0], known[1]["IP No"], "CECT ABDOMEN", "Gall bladder is distended with multiple calculi and wall thickening of 5 mm with "
             "pericholecystic fat stranding. CBD is not dilated.", "Acute calculous cholecystitis."),
            ({"name": "Mr. ARJUN MEHRA", "age": 41, "sex": "Male", "uhid": "XX000999"}, "IP2627/999999", "NCCT BRAIN",
             "No intracranial haemorrhage. Ventricles and sulci are normal for age.", "Normal study.")):
        dd = dt.datetime.strptime(known[1]["DOA"], "%d/%m/%y").date() + dt.timedelta(days=1)
        y = 800
        for line in ("SAMPLE MULTISPECIALITY HOSPITAL - CT DEPARTMENT", f"Patient Name : {who['name']}        Age/Sex : {who['age']} Y / {who['sex'][0]}",
                     f"IP No : {ip}        Date : {dd.strftime('%d/%m/%Y')}", "Ref By : Dr. Pradeep Kulkarni", "", title, "FINDINGS:", body, "IMPRESSION:", imp,
                     "", "Dr. Mahesh Bhat", "Consultant Radiologist"):
            c.setFont("DV", 10)
            c.drawString(50, y, line)
            y -= 18
        c.showPage()
    c.save()
    # 3. misspelt name, no number
    q = next((p, r) for p, r in zip(people, rows) if CASES[p["case"]][0].startswith("CONGESTIVE"))
    p, r = q
    d = dt.datetime.strptime(r["DOA"], "%d/%m/%y").date() + dt.timedelta(days=2)
    first, last = p["name"].split(" ", 1)[1].split(" ", 1)
    doc = docx.Document()
    doc.add_paragraph(f"Name : {first[:-1]} {last}      Age : {p['age']} years      Sex : {p['sex']}")
    doc.add_paragraph(f"Date : {d.strftime('%d/%m/%Y')}")
    doc.add_paragraph("2D ECHO")
    doc.add_paragraph("Dilated left ventricle with global hypokinesia. LVEF 35%. Mild mitral regurgitation.")
    doc.add_paragraph("IMPRESSION: Dilated cardiomyopathy with moderate LV systolic dysfunction.")
    doc.add_paragraph("Dr. Pradeep Kulkarni, DM Cardiology")
    doc.save(folder / "echo_report_misspelt.docx")
    return {"list": root / "radiology_report_list.xlsx", "folder": folder}


def make_all(root, seed=7):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    people, rows = register(root / "keystone_ip_register.xlsx", rng)
    summaries(root / "keystone_discharge_summaries.xlsx")
    lis(root / "keystone_lis_results.xlsx", people, rows, rng)
    n = xrays(root / "xrays", people, rows)
    rad = radiology(root, people, rows)
    return {"exports": [root / "keystone_ip_register.xlsx", root / "keystone_discharge_summaries.xlsx"],
            "labs": root / "keystone_lis_results.xlsx", "xrays": root / "xrays", "images": n,
            "reports": [rad["list"], rad["folder"]]}


if __name__ == "__main__":
    import sys
    print(make_all(sys.argv[1] if len(sys.argv) > 1 else "sample_data"))
