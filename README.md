# Workframe Health · Clinical Data De-identification 2.1

Turns a hospital's own export into the receiving team's anonymised dataset (*Anonymised EHR and Imaging Dataset – Technical Data Provision Reference*, adapted to Indian standards). It runs **entirely on the hospital's computer**: no server, no account, no upload.

| Input | Required? | Accepted formats |
|---|---|---|
| Hospital records export (admissions list and/or discharge summaries) | **Required** | Excel .xlsx/.xls, CSV, discharge-summary PDF printout. Any column names. |
| Radiology reports | Optional | Report list (Excel/CSV, one row per report) and/or report documents (Word .docx, PDF; several reports per file allowed) |
| Imaging | Optional | Folder of DICOM files |
| Laboratory results | Optional | Excel/CSV, one row per result |

## What it does, in order
1. **Read** the export, whatever its layout. Columns are recognised by meaning, and a mapping report shows how each was read.
2. **Link** each radiology report and image to the patient and admission:
   - IP/admission number first, then patient number, then name + sex + age + date.
   - Ages typed into names ("VIJAY 28YRS") are understood.
   - Uncertain matches are **held for review, not released**.
   - A report and an image of the same patient, date and modality become one imaging study.
   - A report without an image becomes a **report-only study** (metadata from the report, 0 instances).
   - Images without a report are kept as normal.
3. **De-identify:**
   - names, UHID/MRN/IP numbers, ABHA, phone, address, relatives and doctors are removed
   - dates become months, and PIN codes become states
   - free text and report text are scrubbed
   - DICOM headers are rewritten, and text burned into images is masked by OCR
   - small groups are suppressed
4. **Structure** the output exactly as the reference asks. Folder `anonymised_dataset_v1.0`:
   - `00_documentation` to `06_governance`
   - FHIR R4 NDJSON with a parallel CSV
   - PDF/A documents
   - DICOM stored by anonymised study/series/instance
5. **Verify and package:**
   - the 13-point pre-transfer checklist
   - a leak scan of every released file
   - schema and value-set validation
   - a file inventory
   - a zip archive with SHA-256

## Running it at a hospital (Windows)
Use the **portable app**: one folder containing `WF-DeID.exe`, with Python, every library and Tesseract OCR built in. Nothing to install, no admin rights, no internet. See `packaging/HOW_TO_USE.txt` (also inside the portable folder).

**Getting the portable app:** every push to `main` builds it on GitHub (Actions → *Build portable Windows app* → artifact **WF-DeID-Portable-Windows**). The build also runs the full self-test with the real DICOM library and the bundled OCR before packaging. Tag a version (`v2.1.0`) to attach the zip to a GitHub Release.

## Where data lives
| Item | Location | May leave the hospital? |
|---|---|---|
| Original exports, reports, images | Where the hospital saved them | No |
| Identity vault (encrypted; keeps anonymised IDs stable across batches) | `KEEP_INSIDE_HOSPITAL/id_vault.enc` | No |
| Review lists, column mapping, previews, run report | `output/batch_…/KEEP_INSIDE_HOSPITAL` | No |
| Anonymised dataset | `output/batch_…/anonymised_dataset_v1.0` | Only via the archive |
| **TRANSFER archive + .sha256** | `output/batch_…/TRANSFER` | **Yes, by the agreed secure route** |

Each hospital has its own installation, its own vault password (set by the hospital administrator) and its own patient IDs.

## Running from source (development, Mac or Windows)
```
python -m venv .venv
.venv\Scripts\activate          # Mac: source .venv/bin/activate
pip install -r requirements.txt
python selftest.py
python app.py
```
`setup_windows.bat` / `setup_mac_linux.sh` do the same.

## Configuration
- `config/settings.yaml`: dataset version, linking thresholds, small-cell threshold (default 5), OCR, plausible vital ranges, hospital-number patterns.
- `config/column_overrides.yaml`: fix a misread column in one line.
- `config/governance/`: signed permitted-use statement, data sharing agreement reference and DPIA summary, copied into every batch.
- `data/`: editable coding tables (ICD-10 rules, brand → ATC, LOINC, specialties, procedures, PIN prefixes, header names).

## Limits
- Free-text redaction is rule-based, so a sample of documents must be reviewed (checklist item 3).
- OCR can miss faint burned-in text, so every image preview must be checked (item 2).
- Old Word `.doc` reports must be saved as `.docx` or PDF.
- Scanned (image-only) PDF reports are not read; ask for text PDFs or Word files.
