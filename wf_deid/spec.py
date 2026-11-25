"""The delivered dataset, field by field, following the company's Technical Data Provision Reference
(sections 2, 3 and 6), adapted to Indian standards. Used to write files in a fixed column order, and to
generate the data dictionary, schema and README automatically, so the documentation always matches the files.
"""

UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
MONTH = r"^\d{4}-\d{2}$"
AGE_BANDS = ["0-17", "18-39", "40-59", "60-79", "80+", "Unknown", "Suppressed"]
SEXES = ["Male", "Female", "Other", "Unknown"]
ENC_TYPES = ["Inpatient", "Outpatient", "Emergency", "Day care", "Virtual", "Other"]
STATUSES = ["Final", "Amended", "Preliminary", "Cancelled"]
PAYERS = ["Self-pay", "Private insurance", "PM-JAY", "Government scheme", "Corporate", "Other", ""]
WARDS = ["General ward", "Semi-private", "Private room", "Critical care", "Day care", "Other", ""]

# field: (name, type, format/values, description, anonymisation rule, source, example, required, pattern, enum, additional)
def F(name, typ, fmt, desc, rule, source, example, required=True, pattern=None, enum=None, additional=False):
    return dict(name=name, type=typ, format=fmt, description=desc, rule=rule, source=source, example=example,
                required=required, pattern=pattern, enum=enum, additional=additional)


PID = F("anon_patient_id", "string", "UUID", "Random anonymised patient identifier; primary linkage key across all modules",
        "Newly generated random UUID. Hospital UHID/MRN, ABHA and admission numbers removed. Same patient keeps the same ID across batches through an encrypted vault held only by the hospital.",
        "Generated", "3f2b8c1e-5d4a-4c7e-9b1f-2a6d8e0c4b71", pattern=UUID)
EID = F("anon_encounter_id", "string", "UUID", "Anonymised encounter (hospital stay) identifier",
        "Newly generated random UUID. Admission / IP number not released.", "Generated", "8d1c2e4f-7a3b-4e6d-a1c9-5b2f0e8d7c63", pattern=UUID)
SID = F("anon_imaging_study_id", "string", "UUID", "Anonymised imaging study identifier",
        "Newly generated random UUID. Original accession number and Study Instance UID not released.", "Generated",
        "c47e9a2d-1b6f-4d38-8e5a-0f3c2b9d6a14", pattern=UUID)

MODULES = {
    "patient_demographics": dict(folder="01_patient", fhir="Patient", title="Patient Demographics", status="Required", fields=[
        PID,
        F("age_band", "string", "0-17, 18-39, 40-59, 60-79, 80+", "Patient age band at the latest admission in the batch",
          "Derived from age in years (or date of birth); exact age and date of birth not released. Suppressed if small-cell risk.",
          "Hospital export: Age / Gender-Age", "40-59", enum=AGE_BANDS),
        F("sex", "string", "Male, Female, Other, Unknown", "Recorded sex", "Suppressed to Unknown if small-cell risk.",
          "Hospital export: Gender", "Female", enum=SEXES),
        F("region", "string", "Indian state / union territory", "Generalised geographic area",
          "Derived from the first two digits of the PIN code (state level). PIN code and address not released. Suppressed if small-cell risk. Empty when the export has no PIN code.",
          "Hospital export: PIN code (patient registration)", "Karnataka", required=False),
        F("deprivation_decile", "integer", "1-10 (blank)", "Area deprivation measure",
          "Not applicable in India: the English IMD has no patient-level Indian equivalent. Left blank; see payer_category.",
          "Not available", "", required=False),
        F("payer_category", "string", "Self-pay, Private insurance, PM-JAY, Government scheme, Corporate, Other",
          "How the admission was paid for (Indian substitute for deprivation, by agreement)",
          "Derived from billing category; policy, card and scheme numbers not released.", "Hospital export: patient category / payer",
          "Self-pay", required=False, enum=PAYERS, additional=True),
    ]),
    "encounters": dict(folder="02_encounters", fhir="Encounter", title="Encounters", status="Required", fields=[
        EID, PID,
        F("encounter_date_month", "string", "YYYY-MM", "Month of admission", "Exact date generalised to month.",
          "Hospital export: Admission Date", "2026-11", pattern=MONTH),
        F("encounter_type", "string", "Inpatient, Outpatient, Emergency, Day care, Virtual, Other", "Encounter setting",
          "Coded. 'A&E' in the UK specification is 'Emergency' (casualty) in India; 'Day care' added.",
          "Hospital export: admission record", "Inpatient", enum=ENC_TYPES),
        F("specialty_code", "string", "Local specialty code (see value_sets)", "Clinical specialty code",
          "Coded from department / consultant type. Consultant name not released. Rare specialties grouped to OTHER.",
          "Hospital export: Consultant Type / department", "GENMED"),
        F("specialty", "string", "Specialty display name", "Clinical specialty", "As specialty_code.", "Derived", "General medicine"),
        F("length_of_stay_days", "integer", "0-365", "Days between admission and discharge",
          "Derived from exact dates before they were generalised; dates themselves not released.",
          "Hospital export: Admission and Discharge Date", "2", required=False, additional=True),
        F("ward_type", "string", "General ward, Semi-private, Private room, Critical care, Day care, Other", "Level of care",
          "Ward name generalised; bed number not released.", "Hospital export: Ward", "General ward", required=False, enum=WARDS, additional=True),
    ]),
    "diagnoses": dict(folder="02_encounters", fhir="Condition", title="Diagnoses", status="Conditional", fields=[
        PID, EID,
        F("diagnosis_code", "string", "ICD-10 (WHO)", "Coded diagnosis", "Coded from the discharge diagnosis; free text not released. Rare codes truncated to 3-character category, then suppressed.",
          "Hospital export: Diagnosis Details", "M06.0"),
        F("code_system", "string", "ICD-10", "Code system of diagnosis_code", "-", "Derived", "ICD-10"),
        F("diagnosis_display", "string", "ICD-10 title", "Standard ICD-10 title for the code", "Standard title, not hospital text.", "ICD-10", "Seronegative rheumatoid arthritis"),
        F("years_since_diagnosis", "integer", "0-99", "Years since the condition was first diagnosed",
          "Derived from the year of diagnosis; the year itself not released.", "Hospital export: Diagnosis Details (year)", "13",
          required=False, additional=True),
    ]),
    "procedures": dict(folder="02_encounters", fhir="Procedure", title="Procedures", status="Conditional", fields=[
        PID, EID,
        F("procedure_code", "string", "Local procedure code (WF-PROC); SNOMED CT mapping pending", "Coded procedure",
          "Coded from surgery name; free text and surgery notes not released. Rare codes suppressed.", "Hospital export: Surgery_Name", "WF-PROC-001"),
        F("code_system", "string", "WF-PROC", "Code system of procedure_code", "-", "Derived", "WF-PROC"),
        F("procedure_display", "string", "Standard procedure name", "Display name for the code", "Standard name, not hospital text.", "Derived", "Laparoscopic cholecystectomy"),
        F("procedure_month", "string", "YYYY-MM", "Month of the procedure", "Exact date generalised to month.", "Hospital export: Surgery_Date", "2026-11",
          required=False, pattern=MONTH, additional=True),
    ]),
    "medications": dict(folder="02_encounters", fhir="MedicationStatement", title="Medications", status="Conditional", fields=[
        PID, EID,
        F("medication_code", "string", "WHO ATC", "Coded medication", "Coded only: brand and drug names are not released (India has no dm+d; brand -> generic -> ATC).",
          "Hospital export: Treatment Given / Discharge Medication", "N02BE01"),
        F("code_system", "string", "ATC", "Code system of medication_code", "-", "Derived", "ATC"),
        F("medication_context", "string", "Inpatient administered, Discharge prescription", "Where the medicine appears in the record",
          "-", "Derived", "Discharge prescription", enum=["Inpatient administered", "Discharge prescription"], additional=True),
        F("route", "string", "oral, parenteral, inhalation, topical, rectal", "Route of administration", "Derived from dosage form.",
          "Hospital export: medicine line prefix (TAB, INJ, IVF...)", "oral", required=False, additional=True),
        F("strength", "string", "number with optional unit", "Strength as recorded", "-", "Hospital export: Strength", "650", required=False, additional=True),
        F("dose_pattern", "string", "morning-noon-night (e.g. 1-0-1)", "Daily dose pattern", "-", "Hospital export: Dosage", "1-0-1", required=False, additional=True),
        F("frequency", "string", "OD, BD, TDS, QID, HS, SOS, STAT", "Frequency abbreviation", "-", "Hospital export: medicine line", "BD", required=False, additional=True),
        F("duration_days", "integer", "days", "Prescribed duration", "-", "Hospital export: No_of_days", "5", required=False, additional=True),
        F("atc_confidence", "string", "high, medium", "Confidence of the brand-to-ATC mapping (medium = group-level or combination)",
          "-", "Mapping table", "high", required=False, additional=True),
    ]),
    "vitals": dict(folder="03_observations", fhir="Observation", title="Observations: vitals", status="Conditional", fields=[
        PID, EID,
        F("observation_code", "string", "LOINC", "Observation code", "Coded.", "Hospital export: Clinical Examination", "8480-6"),
        F("code_system", "string", "LOINC", "Code system", "-", "Derived", "LOINC"),
        F("observation_value", "number", "numeric", "Observation result", "Implausible values removed (see data quality statement).", "Hospital export: Clinical Examination", "130"),
        F("observation_units", "string", "UCUM", "Unit of measurement", "Standardised to UCUM (temperature converted from degF to Cel).", "Derived", "mm[Hg]"),
        F("result_status", "string", "Final, Amended, Preliminary, Cancelled", "Result status", "Coded. Vitals recorded on a signed discharge summary are Final.", "Derived", "Final", enum=STATUSES),
        F("observation_month", "string", "YYYY-MM", "Month of the observation", "Exact date generalised to month.", "Hospital export: Admission Date", "2026-11",
          pattern=MONTH, additional=True),
    ]),
    "laboratory_results": dict(folder="03_observations", fhir="Observation", title="Observations: laboratory results", status="Conditional", fields=[
        PID, EID,
        F("observation_code", "string", "LOINC or local (WF-LAB)", "Laboratory test code", "Coded.", "Laboratory export", "718-7"),
        F("code_system", "string", "LOINC, LOCAL", "Code system", "-", "Derived", "LOINC"),
        F("observation_value", "string", "numeric or coded text", "Result value", "Free-text results scrubbed for identifiers.", "Laboratory export", "11.2"),
        F("observation_units", "string", "UCUM or local", "Unit", "Standardised where possible.", "Laboratory export", "g/dL", required=False),
        F("result_status", "string", "Final, Amended, Preliminary, Cancelled", "Result status", "Coded; Final when not supplied.", "Laboratory export", "Final", enum=STATUSES),
        F("observation_month", "string", "YYYY-MM", "Month of the result", "Exact date generalised to month.", "Laboratory export", "2026-11",
          required=False, pattern=MONTH, additional=True),
    ]),
    "laboratory_tests_ordered": dict(folder="03_observations", fhir=None, title="Observations: tests ordered (no result values)", status="Additional", fields=[
        PID, EID,
        F("observation_code", "string", "LOINC or local (WF-LAB / WF-IMG / WF-DX)", "Test ordered during the stay", "Coded.", "Hospital export: Investigations", "58410-2"),
        F("code_system", "string", "LOINC, LOCAL", "Code system", "-", "Derived", "LOINC"),
        F("order_month", "string", "YYYY-MM", "Month of the stay", "Exact date generalised to month.", "Hospital export: Admission Date", "2026-11", pattern=MONTH),
    ]),
    "document_index": dict(folder="04_documents", fhir="DocumentReference", title="Clinical Documents", status="Conditional", fields=[
        F("anon_document_id", "string", "UUID", "Anonymised document identifier", "Newly generated.", "Generated", "5e8f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b", pattern=UUID),
        PID, EID,
        F("document_type", "string", "Referral, Discharge Summary, Letter, Consent Form", "Clinical document type", "Coded.", "Hospital export", "Discharge Summary",
          enum=["Referral", "Discharge Summary", "Letter", "Consent Form"]),
        F("document_type_code", "string", "LOINC", "LOINC document type code", "Coded.", "Derived", "18842-5"),
        F("document_month", "string", "YYYY-MM", "Month of the document", "Exact date generalised to month.", "Hospital export: Date of Discharge", "2026-11", pattern=MONTH),
        F("file", "string", "relative path", "Path of the PDF/A file", "File name contains no identifiers.", "Generated",
          "documents_pdfa/5e8f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b.pdf"),
        F("sha256", "string", "hex", "SHA-256 of the PDF/A file", "-", "Generated", "9f86d081884c7d65..."),
    ]),
    "imaging_studies": dict(folder="05_imaging", fhir="ImagingStudy", title="Imaging Studies and Metadata", status="Conditional", fields=[
        SID, PID,
        F("anon_encounter_id", "string", "UUID", "Encounter the study belongs to (blank for studies outside an admission)", "As anon_encounter_id.",
          "Linkage", "8d1c2e4f-7a3b-4e6d-a1c9-5b2f0e8d7c63", required=False, pattern=UUID),
        F("modality", "string", "DICOM modality code (CR, DX, CT, MR, US, NM...)", "Imaging modality", "Retained.", "DICOM header (Modality) or radiology report title", "CR"),
        F("body_region", "string", "DICOM Body Part Examined", "Imaged body region", "Retained where non-identifying.", "DICOM header (BodyPartExamined) or radiology report title", "CHEST", required=False),
        F("study_date_month", "string", "YYYY-MM", "Imaging study month", "Exact date generalised; DICOM dates set to the first of the month.", "DICOM header (StudyDate) or radiology report date", "2026-11", pattern=MONTH),
        F("contrast_used", "string", "Yes, No, Unknown", "Whether contrast was administered", "Retained (plain radiographs without contrast agent = No).",
          "DICOM ContrastBolusAgent, or report wording (CECT / NCCT / with contrast)", "No", enum=["Yes", "No", "Unknown"]),
        F("number_of_series", "integer", "count", "Series in the study (0 when only the report was supplied)", "-", "Derived", "1"),
        F("number_of_instances", "integer", "count", "Images in the study (0 when only the report was supplied)", "-", "Derived", "2"),
        F("view_position", "string", "DICOM View Position (PA, AP, LAT...)", "Projection", "Retained.", "DICOM ViewPosition or report title", "PA",
          required=False, additional=True),
        F("dicom_folder", "string", "relative path", "Folder holding the study's DICOM files (blank when only the report was supplied)",
          "Folder named only by anonymised IDs.", "Generated", "dicom/c47e9a2d-1b6f-4d38-8e5a-0f3c2b9d6a14", required=False),
    ]),
    "imaging_reports": dict(folder="05_imaging", fhir="DiagnosticReport", title="Imaging Reports", status="Conditional", fields=[
        F("anon_imaging_report_id", "string", "UUID", "Anonymised imaging report identifier", "Newly generated.", "Generated", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", pattern=UUID),
        SID, PID,
        F("report_text", "string", "free text", "Anonymised imaging report narrative", "All patient and clinician identifiers, dates and contact details redacted.",
          "Radiology report (export column, report list, or Word/PDF report)", "No active lung lesion. Cardiac size normal."),
        F("report_status", "string", "Preliminary, Final, Amended", "Imaging report status", "Retained; Final when not supplied.", "Hospital radiology report", "Final",
          enum=["Preliminary", "Final", "Amended"]),
    ]),
}

FILE_PATHS = {
    "patient_demographics": "01_patient/patient_demographics",
    "encounters": "02_encounters/encounters",
    "diagnoses": "02_encounters/diagnoses",
    "procedures": "02_encounters/procedures",
    "medications": "02_encounters/medications",
    "vitals": "03_observations/vitals",
    "laboratory_results": "03_observations/laboratory_results",
    "laboratory_tests_ordered": "03_observations/laboratory_tests_ordered",
    "document_index": "04_documents/document_index",
    "imaging_studies": "05_imaging/imaging_studies",
    "imaging_reports": "05_imaging/imaging_reports",
}


def columns(module):
    return [f["name"] for f in MODULES[module]["fields"]]
