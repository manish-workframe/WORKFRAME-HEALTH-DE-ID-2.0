"""Converts hospital text into standard codes: ICD-10 diagnoses, ATC medicines, LOINC observations,
local specialty and procedure codes, PIN-prefix regions and payer categories.

All tables live in data/ and can be extended by the hospital without changing code.
Anything not found in a table is reported as 'unmapped'; nothing is guessed.
"""
import csv
import re
from pathlib import Path

from .paths import base

DATA = base() / "data"


def norm(s):
    s = str(s or "").upper()
    s = re.sub(r"[^A-Z0-9%./]+", " ", s)
    return " ".join(s.split())


def _load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Coder:
    def __init__(self):
        self.icd = [(re.compile(r["pattern"]), r["code"], r["description"]) for r in _load("icd10_starter.csv")]
        self.drugs = [(re.compile(r["match"]), r) for r in _load("drugs_india_starter.csv")]
        self.loinc = [(re.compile(r["match"]), r) for r in _load("loinc_starter.csv")]
        self.spec = [(re.compile(r["match"]), r["code"], r["display"]) for r in _load("specialties.csv")]
        self.proc = [(re.compile(r["match"]), r["code"], r["display"]) for r in _load("procedures_starter.csv")]
        self.pin = {r["prefix"]: r["region"] for r in _load("pin_prefix_states.csv")}
        self.pin_dir = {}
        p = DATA / "pincode_directory.csv"  # optional: India Post All-India Pincode Directory (data.gov.in)
        if p.exists():
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    k = {c.lower(): v for c, v in r.items()}
                    pc = str(k.get("pincode", "")).strip()
                    if pc and pc not in self.pin_dir:
                        self.pin_dir[pc] = f"{(k.get('statename') or k.get('state') or '').title()} / {(k.get('district') or k.get('districtname') or '').title()}".strip(" /")
        self.icd_desc = {c: d for _, c, d in self.icd}
        self.spec_desc = {c: d for _, c, d in self.spec}
        self.proc_desc = {c: d for _, c, d in self.proc}

    # ------------------------------------------------------------------ diagnoses
    def diagnoses(self, text):
        """Return (list of (code, description)), unmapped_text."""
        t = norm(text)
        if not t:
            return [], ""
        found, cats = [], set()
        for rx, code, desc in self.icd:
            if rx.search(t) and code[:3] not in cats:
                found.append((code, desc))
                cats.add(code[:3])
        # symptoms (R codes) are dropped when a real diagnosis was found
        if any(not c.startswith("R") for c, _ in found):
            found = [(c, d) for c, d in found if not c.startswith("R")]
        return found, ("" if found else t)

    def icd_from_code(self, code):
        c = str(code or "").strip().upper()
        if re.fullmatch(r"[A-Z]\d{2}(\.\d{1,2})?", c):
            return c, self.icd_desc.get(c, "")
        return None, ""

    # ------------------------------------------------------------------ medicines
    FORM = {"TAB": "oral", "TABLET": "oral", "CAP": "oral", "CAPSULE": "oral", "SYP": "oral", "SYRUP": "oral",
            "SUSP": "oral", "POWDER": "oral", "SACHET": "oral", "DROPS": "oral", "INJ": "parenteral",
            "INJECTION": "parenteral", "IVF": "parenteral", "IV": "parenteral", "NEB": "inhalation",
            "INH": "inhalation", "ROTACAP": "inhalation", "OINT": "topical", "CREAM": "topical", "GEL": "topical",
            "LOTION": "topical", "SUPP": "rectal"}

    def split_medicines(self, text):
        """Split a cell or section into individual medicine strings."""
        parts = re.split(r"[;\n|]+|\s{2,}|[-\s]WITH[-\s]", str(text or ""), flags=re.I)
        out = []
        for p in parts:
            p = p.strip(" -•*\t")
            if not p or norm(p) in ("WITH", "AND", "+"):
                continue
            out.append(p)
        return out

    def medicine(self, item):
        """Parse one medicine string. Returns dict with atc, generic, route, strength, frequency, mapped."""
        raw = str(item)
        t = norm(raw.replace("-", " "))
        first = t.split(" ")[0] if t else ""
        route = self.FORM.get(first, "")
        freq = ""
        m = re.search(r"\b(OD|BD|BID|TDS|TID|QID|HS|SOS|STAT|PRN|QHS|Q\d+H)\b", t)
        if m:
            freq = m.group(1)
        strength = ""
        if not route and re.search(r"\b(IV|IM|SC|IVF)\b", t):
            route = "parenteral"
        m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(MG|MCG|GM|G|ML|IU|%)?(?![\d.])", t)
        if m:
            strength = m.group(1) + (m.group(2) or "")
        for rx, r in self.drugs:
            if rx.search(t):
                return {"atc": r["atc"], "generic": r["generic"], "atc_confidence": r["atc_confidence"],
                        "route": route, "strength": strength, "frequency": freq, "mapped": True, "raw": raw}
        return {"atc": "", "generic": "", "atc_confidence": "", "route": route, "strength": strength,
                "frequency": freq, "mapped": False, "raw": raw}

    # ------------------------------------------------------------------ observations
    def observation(self, name):
        t = norm(name)
        for rx, r in self.loinc:
            if rx.search(t):
                return r
        return None

    # ------------------------------------------------------------------ specialty, procedures
    def specialty(self, *texts):
        for t in texts:
            t = norm(t)
            if not t:
                continue
            for rx, code, disp in self.spec:
                if code != "OTHER" and rx.search(t):
                    return code, disp
        return "", ""

    def procedure(self, text):
        t = norm(text)
        if not t:
            return None
        for rx, code, disp in self.proc:
            if rx.search(t):
                return code, disp
        return None

    # ------------------------------------------------------------------ region, payer, ward
    def region(self, pin):
        p = re.sub(r"\D", "", str(pin or ""))
        if len(p) != 6:
            return ""
        if p in self.pin_dir:
            return self.pin_dir[p]
        return self.pin.get(p[:2], "")

    @staticmethod
    def payer(v):
        t = norm(v)
        if not t:
            return ""
        if re.search(r"PMJAY|PM JAY|AYUSHMAN|ABARK|ARK|ABPMJAY", t):
            return "PM-JAY"
        if re.search(r"CGHS|ECHS|\bESI|ESIC|GOVT|GOVERNMENT|STATE SCHEME|YESHASVINI|AROGYA", t):
            return "Government scheme"
        if re.search(r"INSUR|TPA|CASHLESS|MEDICLAIM|POLICY", t):
            return "Private insurance"
        if re.search(r"CORPORATE|PANEL|COMPANY", t):
            return "Corporate"
        if re.search(r"CASH|SELF|GENERAL|PRIVATE PAY|WALK", t):
            return "Self-pay"
        return "Other"

    @staticmethod
    def ward_type(v):
        t = norm(v)
        if not t:
            return ""
        if re.search(r"\b(ICU|CCU|NICU|PICU|HDU|SICU|MICU|ITU|CRITICAL)\b", t):
            return "Critical care"
        if re.search(r"SEMI", t):
            return "Semi-private"
        if re.search(r"PRIVATE|DELUXE|SUITE|SINGLE|SPECIAL", t):
            return "Private room"
        if re.search(r"DAY ?CARE", t):
            return "Day care"
        if re.search(r"GENERAL|WARD|MALE|FEMALE", t):
            return "General ward"
        return "Other"

    @staticmethod
    def age_band(age):
        if age is None:
            return "Unknown"
        a = float(age)
        if a < 18:
            return "0-17"
        if a < 40:
            return "18-39"
        if a < 60:
            return "40-59"
        if a < 80:
            return "60-79"
        return "80+"

    @staticmethod
    def sex(v):
        t = norm(v)
        if t.startswith("F") or "FEMALE" in t:
            return "Female"
        if t.startswith("M") or t == "MALE":
            return "Male"
        if t.startswith("T") or "OTHER" in t:
            return "Other"
        return "Unknown"
