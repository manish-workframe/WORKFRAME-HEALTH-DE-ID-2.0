"""PDF output: de-identified discharge summaries (PDF/A-2b) and the documentation PDFs."""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .paths import base

FONTS = base() / "fonts"
pdfmetrics.registerFont(TTFont("DV", str(FONTS / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DVB", str(FONTS / "DejaVuSans-Bold.ttf")))

INK = colors.HexColor("#1c2733")
MUTED = colors.HexColor("#5a666b")
RULE = colors.HexColor("#c9ccc6")
S = {
    "title": ParagraphStyle("t", fontName="DVB", fontSize=15, leading=19, textColor=INK, spaceAfter=2),
    "sub": ParagraphStyle("s", fontName="DV", fontSize=8, leading=11, textColor=MUTED, spaceAfter=8),
    "h": ParagraphStyle("h", fontName="DVB", fontSize=9.5, leading=13, textColor=INK, spaceBefore=8, spaceAfter=3),
    "p": ParagraphStyle("p", fontName="DV", fontSize=8.6, leading=12, textColor=INK, alignment=TA_LEFT),
    "small": ParagraphStyle("sm", fontName="DV", fontSize=7, leading=9.5, textColor=MUTED),
    "cell": ParagraphStyle("c", fontName="DV", fontSize=7.8, leading=10, textColor=INK),
    "cellb": ParagraphStyle("cb", fontName="DVB", fontSize=7.8, leading=10, textColor=INK),
}


def esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _table(rows, widths, header=True):
    data = [[Paragraph(esc(c), S["cellb"] if (header and i == 0) else S["cell"]) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
          ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
          ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st += [("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    t.setStyle(TableStyle(st))
    return t


def to_pdfa(path, title):
    """Add PDF/A-2b identification (XMP) and an sRGB output intent. Fonts are already embedded by ReportLab."""
    try:
        import pikepdf
        from PIL import ImageCms
    except ImportError:
        return False
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["pdfaid:part"] = "2"
            meta["pdfaid:conformance"] = "B"
            meta["dc:title"] = title
        intent = pikepdf.Dictionary(Type=pikepdf.Name.OutputIntent, S=pikepdf.Name.GTS_PDFA1,
                                    OutputConditionIdentifier=pikepdf.String("sRGB IEC61966-2.1"),
                                    DestOutputProfile=pdf.make_stream(icc, N=3))
        pdf.Root.OutputIntents = pikepdf.Array([intent])
        pdf.save(str(path))
    return True


def _doc(path, title, pagesize=A4):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return SimpleDocTemplate(str(path), pagesize=pagesize, leftMargin=16 * mm, rightMargin=16 * mm,
                             topMargin=14 * mm, bottomMargin=14 * mm, title=title, author="Workframe Health De-ID",
                             subject="Anonymised dataset", creator="Workframe Health De-ID 2.0")


def discharge_summary(path, d):
    """d: de-identified content of one admission."""
    doc = _doc(path, "Discharge Summary (de-identified)")
    st = [Paragraph("Discharge Summary", S["title"]),
          Paragraph("De-identified copy. Names, hospital numbers, dates, contact details and clinician identities removed.", S["sub"])]
    st.append(_table([["Anonymised patient ID", d["anon_patient_id"]], ["Anonymised encounter ID", d["anon_encounter_id"]]],
                     [48 * mm, 130 * mm], header=False))
    st.append(_table([["Sex / age band", f"{d['sex']} / {d['age_band']}", "Admission month", d["encounter_date_month"]],
                      ["Encounter type", d["encounter_type"], "Length of stay", f"{d['los']} day(s)" if d["los"] != "" else ""],
                      ["Ward type", d["ward_type"], "Specialty", d["specialty"]]],
                     [30 * mm, 60 * mm, 30 * mm, 58 * mm], header=False))

    def sec(title, body):
        if body:
            st.append(Paragraph(title, S["h"]))
            if isinstance(body, list):
                for b in body:
                    st.append(Paragraph("– " + esc(b), S["p"]))
            else:
                st.append(Paragraph(esc(body), S["p"]))

    sec("Brief History", d["complaints"])
    ex = []
    if d["vitals"]:
        ex.append(", ".join(d["vitals"]))
    ex += d["exam"]
    sec("Clinical Examination", ex)
    sec("Investigations", d["investigations"])
    sec("Treatment Given", d["treatment"])
    dx = list(d["diagnosis_text"]) + [f"{c} {t}" for c, t in d["diagnosis_codes"]]
    if d.get("years_since_diagnosis") not in (None, ""):
        dx.append(f"Diagnosed {d['years_since_diagnosis']} year(s) before this admission")
    sec("Diagnosis Details", dx)
    sec("Surgery", d["surgery"])
    st.append(Paragraph("Consultant Details", S["h"]))
    st.append(Paragraph(f"Specialty: {esc(d['specialty'] or 'not recorded')}. Consultant name and contact removed.", S["p"]))
    if d["discharge_meds"]:
        st.append(Paragraph("Advice on Discharge: Medication / Prescription", S["h"]))
        st.append(_table([["Medication", "Strength", "Dosage", "Days"]] +
                         [[m["name"], m["strength"], m["dosage"], m["days"]] for m in d["discharge_meds"]],
                         [80 * mm, 30 * mm, 35 * mm, 33 * mm]))
    sec("Advice", d["advice"])
    st.append(Spacer(1, 10))
    st.append(Paragraph("Generated by Workframe Health De-ID from the hospital record. Signature removed.", S["small"]))
    doc.build(st)
    to_pdfa(path, "Discharge Summary (de-identified)")


def text_pdf(path, title, subtitle, blocks, pagesize=A4):
    """blocks: list of ('h', text) | ('p', text) | ('table', rows, widths_mm) | ('small', text)."""
    doc = _doc(path, title, pagesize)
    st = [Paragraph(esc(title), S["title"]), Paragraph(esc(subtitle), S["sub"])]
    for b in blocks:
        if b[0] == "table":
            st.append(_table(b[1], [w * mm for w in b[2]]))
            st.append(Spacer(1, 4))
        else:
            st.append(Paragraph(esc(b[1]), S[b[0]]))
    doc.build(st)
    to_pdfa(path, title)


def erd_pdf(path, modules):
    """Entity relationship diagram: modules as boxes on a grid, joined by orthogonal connector lines (drawn behind the boxes)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    W, H = landscape(A4)
    c = canvas.Canvas(str(path), pagesize=(W, H))
    c.setTitle("Entity Relationship Diagram")
    c.setFont("DVB", 15)
    c.setFillColor(INK)
    c.drawString(16 * mm, H - 15 * mm, "Entity Relationship Diagram")
    c.setFont("DV", 7.5)
    c.setFillColor(MUTED)
    c.drawString(16 * mm, H - 20 * mm, "PK = primary key of the file. FK = foreign key. Every record carries anon_patient_id; clinical records also carry "
                                       "anon_encounter_id; imaging links through anon_imaging_study_id.")
    own = {"patient_demographics": "anon_patient_id", "encounters": "anon_encounter_id", "imaging_studies": "anon_imaging_study_id",
           "imaging_reports": "anon_imaging_report_id", "document_index": "anon_document_id"}
    bw, gap, rowgap, top = 62 * mm, 9 * mm, 11 * mm, H - 27 * mm
    colx = [14 * mm + k * (bw + gap) for k in range(4)]
    grid = [["", "patient_demographics", "imaging_studies", "_dicom"],
            ["", "encounters", "imaging_reports", ""],
            ["diagnoses", "procedures", "medications", "document_index"],
            ["vitals", "laboratory_results", "laboratory_tests_ordered", ""]]
    lh = 3.1 * mm

    def lines_for(m):
        if m == "_dicom":
            return "Imaging Objects (DICOM)", [("", "dicom/<anon_imaging_study_id>/"), ("", "series_NNN/instance_NNNN.dcm"),
                                               ("", "headers carry anon_patient_id only")]
        f = [x["name"] for x in modules[m]["fields"]]
        keys = [(("PK" if x == own.get(m) else "FK"), x) for x in f if x.startswith("anon_")]
        rest = [("", x) for x in f if not x.startswith("anon_")]
        if len(rest) > 5:
            rest = rest[:4] + [("", f"+ {len(rest) - 4} more fields")]
        return modules[m]["title"], keys + rest

    heights = [max((6 * mm + len(lines_for(m)[1]) * lh) for m in row if m) for row in grid]
    box = {}
    y = top
    for r, row in enumerate(grid):
        for k, m in enumerate(row):
            if m:
                box[m] = (colx[k], y - heights[r], bw, heights[r])
        y -= heights[r] + rowgap
    L = lambda m: box[m][0]
    R = lambda m: box[m][0] + box[m][2]
    T = lambda m: box[m][1] + box[m][3]
    B = lambda m: box[m][1]
    CX = lambda m: box[m][0] + box[m][2] / 2
    CY = lambda m: box[m][1] + box[m][3] / 2

    c.setStrokeColor(MUTED)
    c.setLineWidth(0.7)

    def path_(*pts):
        p = c.beginPath()
        p.moveTo(*pts[0])
        for q in pts[1:]:
            p.lineTo(*q)
        c.drawPath(p, stroke=1, fill=0)

    def label(x, y, t):
        c.setFont("DV", 5.6)
        c.setFillColor(MUTED)
        c.drawString(x, y, t)

    # patient - encounters
    path_((CX("encounters"), T("encounters")), (CX("encounters"), B("patient_demographics")))
    label(CX("encounters") + 1.5 * mm, (T("encounters") + B("patient_demographics")) / 2 - 1 * mm, "anon_patient_id")
    # patient - imaging studies
    yp = CY("patient_demographics") + 4 * mm
    path_((R("patient_demographics"), yp), (L("imaging_studies"), yp))
    # encounters - imaging studies (through the column gap)
    gx = L("imaging_studies") - gap / 2
    ye = CY("patient_demographics") - 6 * mm
    path_((R("encounters"), CY("encounters")), (gx, CY("encounters")), (gx, ye), (L("imaging_studies"), ye))
    # imaging studies - reports, studies - DICOM
    path_((CX("imaging_studies"), B("imaging_studies")), (CX("imaging_studies"), T("imaging_reports")))
    label(CX("imaging_studies") + 1.5 * mm, (B("imaging_studies") + T("imaging_reports")) / 2 - 1 * mm, "anon_imaging_study_id")
    path_((R("imaging_studies"), CY("_dicom")), (L("_dicom"), CY("_dicom")))
    # encounters - row 2 bus
    bus1 = T("diagnoses") + rowgap / 2
    path_((CX("encounters"), B("encounters")), (CX("encounters"), bus1))
    path_((CX("diagnoses"), bus1), (CX("document_index"), bus1))
    for m in ("diagnoses", "procedures", "medications", "document_index"):
        path_((CX(m), bus1), (CX(m), T(m)))
    label(CX("encounters") + 1.5 * mm, bus1 + 1.2 * mm, "anon_encounter_id")
    # row 3 bus, fed down the left margin
    bus2 = T("vitals") + rowgap / 2
    xm = 8 * mm
    path_((CX("diagnoses"), bus1), (xm, bus1), (xm, bus2), (CX("laboratory_tests_ordered"), bus2))
    for m in ("vitals", "laboratory_results", "laboratory_tests_ordered"):
        path_((CX(m), bus2), (CX(m), T(m)))

    # boxes on top of the lines
    for m, (x, y0, w, h) in box.items():
        title, rows = lines_for(m)
        c.setStrokeColor(INK)
        c.setLineWidth(0.8)
        c.setFillColor(colors.white)
        c.rect(x, y0, w, h, stroke=1, fill=1)
        size = 7.6
        while stringWidth(title, "DVB", size) > w - 4 * mm and size > 5.5:
            size -= 0.2
        c.setFillColor(INK)
        c.setFont("DVB", size)
        c.drawString(x + 2 * mm, y0 + h - 4.3 * mm, title)
        for k, (tag, f) in enumerate(rows):
            yy = y0 + h - 8.4 * mm - k * lh
            c.setFont("DVB" if tag else "DV", 6)
            c.setFillColor(INK if tag else MUTED)
            if tag:
                c.drawString(x + 2 * mm, yy, tag)
            c.drawString(x + 8 * mm, yy, f)
    c.save()
    to_pdfa(path, "Entity Relationship Diagram")
