"""X-ray (DICOM) reading and de-identification, built on pydicom."""
import datetime as dt
import re
from pathlib import Path

import numpy as np

try:
    import pydicom
    from pydicom.tag import Tag
except ImportError:  # the app reports this clearly at start-up
    pydicom = None

# Removed outright (identifying, or free text that can hold identifiers).
REMOVE = [
    "PatientBirthDate", "PatientBirthTime", "OtherPatientIDs", "OtherPatientIDsSequence", "OtherPatientNames",
    "PatientBirthName", "PatientAddress", "PatientTelephoneNumbers", "PatientMotherBirthName", "MilitaryRank",
    "Occupation", "AdditionalPatientHistory", "PatientComments", "MedicalRecordLocator", "IssuerOfPatientID",
    "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName", "ReferringPhysicianAddress",
    "ReferringPhysicianTelephoneNumbers", "PerformingPhysicianName", "NameOfPhysiciansReadingStudy",
    "OperatorsName", "PhysiciansOfRecord", "RequestingPhysician", "StationName", "DeviceSerialNumber",
    "AdmissionID", "RequestAttributesSequence", "ReferencedPatientSequence", "ImageComments", "StudyComments",
    "PatientInsurancePlanCodeSequence", "ResponsiblePerson", "ResponsibleOrganization", "CountryOfResidence",
    "RegionOfResidence", "CurrentPatientLocation", "PatientInstitutionResidence", "ScheduledProcedureStepLocation",
    "PerformedLocation", "PerformedProcedureStepID", "RequestedProcedureID", "ScheduledPerformingPhysicianName",
]
# Kept but emptied (DICOM requires them to be present).
BLANK = ["ReferringPhysicianName", "AccessionNumber", "StudyID"]
DATES = ["StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate", "InstanceCreationDate", "PerformedProcedureStepStartDate"]
DATETIMES = ["AcquisitionDateTime"]
TEXT_VRS = {"LO", "SH", "LT", "ST", "UT"}
STANDARD_UID_ROOT = "1.2.840.10008."


def available():
    return pydicom is not None


def read_header(path):
    """Read only what linking needs. Returns None for files that are not DICOM."""
    try:
        ds = pydicom.dcmread(str(path), stop_before_pixels=True)
    except Exception:
        return None
    g = lambda k: str(getattr(ds, k, "") or "").strip()
    return {
        "path": str(path),
        "patient_id": g("PatientID"),
        "patient_name": g("PatientName").replace("^", " "),
        "sex": g("PatientSex"),
        "age": g("PatientAge"),
        "birth_date": g("PatientBirthDate"),
        "study_date": g("StudyDate"),
        "modality": g("Modality"),
        "is_image": ("Rows" in ds and "Columns" in ds),  # pixel data itself is not loaded in this quick read
        "study_uid": g("StudyInstanceUID"), "series_uid": g("SeriesInstanceUID"),
        "instance_number": g("InstanceNumber"),
    }


def parse_da(v):
    try:
        return dt.datetime.strptime(str(v)[:8], "%Y%m%d").date()
    except Exception:
        return None


AGE_IN_NAME = re.compile(r"(\d{1,3})\s*(?:Y|YR|YRS|YEARS?)\b", re.I)


def age_from_name(name):
    """ImagePilot staff often type the age into the name: 'VIJAY 28YRS', 'ganga 40yrs'."""
    m = AGE_IN_NAME.search(str(name or ""))
    return int(m.group(1)) if m and 0 < int(m.group(1)) < 120 else None


def age_years(header):
    a = header.get("age", "")
    m = re.match(r"(\d{1,3})([YMWD])", a.upper()) if a else None
    if m:
        n, u = int(m.group(1)), m.group(2)
        return n if u == "Y" else n / {"M": 12, "W": 52, "D": 365}[u]
    b, s = parse_da(header.get("birth_date")), parse_da(header.get("study_date"))
    if b and s:
        return (s - b).days / 365.25
    return age_from_name(header.get("patient_name"))


def _shift_da(v, days):
    d = parse_da(v)
    return (d + dt.timedelta(days=days)).strftime("%Y%m%d") if d else ""


def _to_png_array(arr, ds):
    a = arr.astype(np.float32)
    if a.ndim == 3 and a.shape[-1] in (3, 4):
        a = a[..., :3].mean(axis=-1)
    elif a.ndim == 3:
        a = a[0]
    lo, hi = np.percentile(a, 0.5), np.percentile(a, 99.5)
    a = np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1)
    if str(getattr(ds, "PhotometricInterpretation", "")) == "MONOCHROME1":
        a = 1 - a
    return (a * 255).astype(np.uint8)


def find_text_boxes(img8):
    """OCR the image and return boxes around printed text. Returns (boxes, error)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return [], "pytesseract not installed"
    from .paths import tesseract_cmd
    cmd = tesseract_cmd()
    if not cmd:
        return [], "Tesseract OCR not installed"
    pytesseract.pytesseract.tesseract_cmd = cmd
    h, w = img8.shape
    scale = min(1.0, 2000 / max(h, w))
    im = Image.fromarray(img8)
    if scale < 1:
        im = im.resize((int(w * scale), int(h * scale)))
    boxes = []
    try:
        for variant in (im, Image.eval(im, lambda p: 255 - p)):  # light text on dark, and dark on light
            d = pytesseract.image_to_data(variant, output_type=pytesseract.Output.DICT, config="--psm 11")
            for i, t in enumerate(d["text"]):
                t = (t or "").strip()
                try:
                    conf = float(d["conf"][i])
                except Exception:
                    conf = -1
                alnum = re.sub(r"\W", "", t)
                # Real labels: 3+ characters with letters or several digits, confident, and not speck-sized.
                if (len(alnum) >= 3 and (re.search(r"[A-Za-z]{2}", alnum) or re.search(r"\d{3}", alnum))
                        and conf >= 65 and d["height"][i] >= 8):
                    x, y, bw, bh = (int(d[k][i] / scale) for k in ("left", "top", "width", "height"))
                    pad = max(4, int(bh * 0.4))
                    boxes.append((max(0, x - pad), max(0, y - pad), min(w, x + bw + pad), min(h, y + bh + pad)))
    except Exception as e:  # Tesseract binary missing, etc.
        return [], f"OCR failed: {e}"
    return boxes, None


TIMES = ["StudyTime", "SeriesTime", "AcquisitionTime", "ContentTime", "InstanceCreationTime",
         "PerformedProcedureStepStartTime"]


def _month_da(v):
    d = parse_da(v)
    return d.strftime("%Y%m01") if d else ""


def deidentify(path, out_path, anon_patient_id, vault, scrubber, cfg, png_path=None):
    """De-identify one DICOM file to the company's rules. Dates become the 1st of their month (YYYY-MM),
    times are removed, all UIDs are replaced via the hospital vault, burned-in text is masked.
    Returns a dict describing the study for the imaging index."""
    ds = pydicom.dcmread(str(path))
    info = {"masked_regions": 0, "ocr": "not run", "warnings": []}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    g = lambda k: str(getattr(ds, k, "") or "").strip()
    info.update({"modality": g("Modality"), "body_part": g("BodyPartExamined"), "view": g("ViewPosition"),
                 "contrast_agent": g("ContrastBolusAgent"), "study_month": "", "orig_series_uid": g("SeriesInstanceUID")})
    d = parse_da(g("StudyDate") or g("SeriesDate") or g("ContentDate"))
    info["study_month"] = d.strftime("%Y-%m") if d else ""

    # 1. Private data and overlays.
    ds.remove_private_tags()
    for elem in list(ds):
        gr = elem.tag.group
        if 0x6000 <= gr <= 0x601E and gr % 2 == 0:
            del ds[elem.tag]

    # 2. Identifying attributes.
    for kw in REMOVE + ["PatientAge", "PatientSize", "PatientWeight"]:
        if kw in ds:
            delattr(ds, kw)
    for kw in BLANK:
        if kw in ds:
            setattr(ds, kw, "")
    ds.PatientName = anon_patient_id
    ds.PatientID = anon_patient_id

    # 3. Dates generalised to month; times removed.
    for kw in DATES:
        if kw in ds and str(getattr(ds, kw)):
            setattr(ds, kw, _month_da(getattr(ds, kw)))
    for kw in DATETIMES:
        if kw in ds and str(getattr(ds, kw)):
            setattr(ds, kw, _month_da(str(getattr(ds, kw))[:8]))
    for kw in TIMES:
        if kw in ds:
            setattr(ds, kw, "")

    # 4. Everything else, including nested sequences.
    def walk(dataset, elem):
        if elem.keyword in ("PatientName", "PatientID"):
            return
        vr = elem.VR
        if vr == "PN":
            elem.value = ""
        elif vr == "UI" and elem.value:
            vals = elem.value if isinstance(elem.value, (list, tuple)) or type(elem.value).__name__ == "MultiValue" else [elem.value]
            new = [v if str(v).startswith(STANDARD_UID_ROOT) else vault.uid(str(v)) for v in vals]
            elem.value = new if len(new) > 1 else new[0]
        elif vr in TEXT_VRS and elem.value and isinstance(elem.value, str):
            elem.value = scrubber.scrub(elem.value)
        elif vr == "DA" and elem.value:
            elem.value = _month_da(elem.value)
        elif vr == "TM" and elem.value:
            elem.value = ""

    ds.walk(walk)
    fm = getattr(ds, "file_meta", None)
    if fm is not None and "MediaStorageSOPInstanceUID" in fm:
        fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    # 5. Burned-in text.
    xcfg = cfg.get("xray", {})
    if "PixelData" in ds and (xcfg.get("ocr_burned_in_text") or xcfg.get("mask_corners") or png_path):
        try:
            ts = getattr(fm, "TransferSyntaxUID", None) if fm is not None else None
            if ts is not None and getattr(ts, "is_compressed", False):
                ds.decompress()
            arr = ds.pixel_array.copy()
            img8 = _to_png_array(arr, ds)
            boxes = []
            if xcfg.get("ocr_burned_in_text"):
                boxes, err = find_text_boxes(img8)
                info["ocr"] = err or f"{len(boxes)} text region(s) found"
                if err:
                    info["warnings"].append(err)
            if xcfg.get("mask_corners"):
                h, w = img8.shape
                b = int(h * xcfg.get("corner_band_percent", 7) / 100)
                boxes += [(0, 0, w, b), (0, h - b, w, h)]
            if boxes:
                fill = arr.max() if str(getattr(ds, "PhotometricInterpretation", "")) == "MONOCHROME1" else arr.min()
                for x0, y0, x1, y1 in boxes:
                    if arr.ndim == 2:
                        arr[y0:y1, x0:x1] = fill
                    else:
                        arr[..., y0:y1, x0:x1] = fill
                ds.PixelData = arr.tobytes()
                info["masked_regions"] = len(boxes)
                img8 = _to_png_array(arr, ds)
            ds.BurnedInAnnotation = "NO" if xcfg.get("ocr_burned_in_text") and not info["warnings"] else "YES"
            if png_path:
                from PIL import Image
                Path(png_path).parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(img8).save(png_path)
        except Exception as e:
            info["warnings"].append(f"pixel step skipped: {e}")

    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "WF-DeID 2.0: DICOM basic profile, dates to month, UID remap, text scrub, OCR mask"
    ds.save_as(str(out_path))
    info["new_series_uid"] = str(getattr(ds, "SeriesInstanceUID", ""))
    return info


def header_strings(path):
    """Every text value in a DICOM file, for the leak check."""
    ds = pydicom.dcmread(str(path), stop_before_pixels=True)
    out = []

    def cb(_d, elem):
        if elem.VR in TEXT_VRS | {"PN", "DA", "DT", "CS", "AS"} and elem.value:
            out.append(str(elem.value))

    ds.walk(cb)
    return out
