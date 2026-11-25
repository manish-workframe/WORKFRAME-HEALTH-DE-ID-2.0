"""Free-text scrubbing: removes known identifiers, phone numbers, IDs, emails and dates from text."""
import re

REDACT = "[REDACTED]"

PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
LANDLINE = re.compile(r"(?<!\d)0\d{2,4}[\s-]?\d{6,8}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
AADHAAR = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
ABHA_NO = re.compile(r"(?<!\d)\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
ABHA_ADDR = re.compile(r"[\w.+-]+@(?:abdm|sbx|ndhm)\b", re.I)
MLC = re.compile(r"\bMLC\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*[A-Z0-9/-]+", re.I)
RELATION = re.compile(r"\b[WSDHB]\s*/\s*O\s+(?:MR\.?|MRS\.?|MS\.?|SRI|SMT\.?)?\s*[A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*){0,3}", re.I)
HEXRUN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b[0-9a-f]{32,}\b|\b2\.25\.\d+\b")
KIN = re.compile(r"\b((?i:son|daughter|wife|husband|father|mother|brother|sister|attender|attendant|relative|guardian|grandson|granddaughter))"
                 r"(\s+(?i:of\s+)?(?:(?i:mr|mrs|ms|sri|smt)\.?\s*)?)((?!(?:HAS|HAD|IS|WAS|WITH|AND|OF|FOR|THE|ALSO|NOT|ON|IN|AT|TO)\b)[A-Z][A-Za-z]{2,})")
DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b")

TITLES = {"MR", "MRS", "MS", "MISS", "DR", "MASTER", "BABY", "SMT", "SRI", "SHRI", "KUM", "B/O", "W/O", "S/O", "D/O", "C/O"}

# Words that look like name tokens but are common clinical terms; never treated as names.
SAFE_WORDS = {
    "GENERAL", "WARD", "BED", "FEMALE", "MALE", "PAIN", "HEAD", "BODY", "LOWER", "LIMB", "TEST", "TAB", "INJ", "SYP",
    "PHYSICIAN", "SURGEON", "SPECIALIST", "CONSULTANT", "DOCTOR", "PATIENT", "YEARS", "YEAR", "DAYS", "NORMAL", "GOOD",
}


def name_tokens(name: str):
    """Split a person name into tokens worth searching for (3+ letters, not titles or clinical words)."""
    toks = re.split(r"[\s.,()/'-]+", str(name).upper())
    return {t for t in toks if len(t) >= 3 and t.isalpha() and t not in TITLES and t not in SAFE_WORDS}


class Scrubber:
    def __init__(self, id_patterns=None):
        self.id_patterns = [re.compile(p, re.I) for p in (id_patterns or [])]
        self.literals = set()      # exact values to remove (IDs, admission numbers, phones)
        self.name_toks = set()     # name tokens to remove as whole words
        self._name_re = None

    def add_literal(self, value):
        v = str(value).strip()
        if v and v.lower() not in ("nan", "none") and len(v) >= 3:
            self.literals.add(v)

    def add_name(self, value):
        self.name_toks |= name_tokens(value)

    def build(self):
        if self.name_toks:
            alt = "|".join(sorted((re.escape(t) for t in self.name_toks), key=len, reverse=True))
            self._name_re = re.compile(rf"\b(?:{alt})\b", re.I)
        self._lit = sorted(self.literals, key=len, reverse=True)
        return self

    def scrub(self, text):
        if text is None:
            return text
        s = str(text)
        if not s or s.lower() == "nan":
            return text
        for lit in self._lit:
            s = re.sub(re.escape(lit), REDACT, s, flags=re.I)
        for p in self.id_patterns:
            s = p.sub(REDACT, s)
        s = ABHA_ADDR.sub(REDACT, s)
        s = EMAIL.sub(REDACT, s)
        s = MLC.sub(REDACT, s)
        s = RELATION.sub(REDACT, s)
        s = KIN.sub(lambda m: m.group(1) + m.group(2) + REDACT, s)
        s = ABHA_NO.sub(REDACT, s)
        s = PHONE.sub(REDACT, s)
        s = LANDLINE.sub(REDACT, s)
        s = AADHAAR.sub(REDACT, s)
        s = PAN.sub(REDACT, s)
        s = DATE.sub("[DATE]", s)
        if self._name_re is not None:
            s = self._name_re.sub(REDACT, s)
        return s

    def leaks(self, text):
        """Return identifiers still present in text (used by the release check)."""
        found = []
        if text is None:
            return found
        s = HEXRUN.sub(" ", str(text))   # UUIDs, hashes and DICOM UIDs are not identifiers
        up = s.upper()
        for lit in self._lit:
            if lit.upper() in up:
                found.append(lit)
        if self._name_re is not None:
            found += [m.group(0) for m in self._name_re.finditer(s)]
        found += PHONE.findall(s)
        found += EMAIL.findall(s)
        return found
