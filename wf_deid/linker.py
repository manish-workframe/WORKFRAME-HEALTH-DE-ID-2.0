"""Link X-ray studies to spreadsheet rows (one row = one hospital admission)."""
import datetime as dt
import re
from difflib import SequenceMatcher

from .textscrub import TITLES


AGE_TOKEN = re.compile(r"^\d+(Y|YR|YRS|YEAR|YEARS|M|MTH|MONTHS?|D|DAYS?)?$|^(Y|YR|YRS|YEARS?)$")


def norm_name(n):
    toks = [t for t in re.split(r"[\s.,^()/'-]+", str(n).upper()) if t and t not in TITLES and not AGE_TOKEN.match(t)]
    return " ".join(sorted(toks))


def norm_id(v):
    s = re.sub(r"[\s-]", "", str(v).upper())
    return s.lstrip("0") or s


def norm_sex(v):
    s = str(v).strip().upper()[:1]
    return s if s in ("M", "F") else ""


def name_score(a, b):
    """0..1. Token-wise fuzzy match, so word order and a dropped or changed letter are tolerated."""
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return 0.0
    ta, tb = a.split(), b.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    def tok(t, u):
        if len(t) == 1 or len(u) == 1:           # initials: "S" for "SUBRAMANYA"
            return 0.9 if t[0] == u[0] else 0.0
        return SequenceMatcher(None, t, u).ratio()
    per = [max(tok(t, u) for u in long_) for t in short]
    coverage = (len(short) / len(long_)) ** 0.5
    return round(sum(per) / len(per) * coverage, 3)


def link(rows, headers, cfg):
    """rows: list of dicts with keys pid, name, sex, age, admit (date), discharge (date), row_index.
    headers: list of DICOM header dicts. Returns (assignments, review, unmatched).
    assignments: {header_path: (row_index, score, method)}"""
    L = cfg.get("linking", {})
    before = dt.timedelta(days=int(L.get("days_before_admission", 2)))
    after = dt.timedelta(days=int(L.get("days_after_discharge", 2)))
    auto, review_t = float(L.get("auto", 0.92)), float(L.get("review", 0.75))

    from .xray import parse_da, age_years

    def in_window(row, d):
        if d is None or row["admit"] is None:
            return False
        end = row["discharge"] or row["admit"]
        return row["admit"] - before <= d <= end + after

    by_pid = {}
    for r in rows:
        if r["pid"]:
            by_pid.setdefault(norm_id(r["pid"]), []).append(r)

    assignments, review, unmatched = {}, [], []
    for h in headers:
        d = parse_da(h["study_date"])
        # 1. Exact patient number + date inside the admission window.
        cands = [r for r in by_pid.get(norm_id(h["patient_id"]), []) if in_window(r, d)] if h["patient_id"] else []
        if len(cands) == 1:
            assignments[h["path"]] = (cands[0]["row_index"], 1.0, "patient number + date")
            continue
        # 2. Fuzzy: name, sex, age, and the date must fall inside the admission window.
        hage = age_years(h)
        best, best_s = None, 0.0
        for r in rows:
            if not in_window(r, d):
                continue
            ns = name_score(h["patient_name"], r["name"])
            sx = 1.0 if norm_sex(h["sex"]) and norm_sex(h["sex"]) == norm_sex(r["sex"]) else 0.0
            ag = 0.5
            if hage is not None and r["age"] is not None:
                ag = 1.0 if abs(hage - r["age"]) <= 1.5 else 0.0
            s = 0.6 * ns + 0.2 * sx + 0.2 * ag
            if ns < 0.97:            # spelling differs: never automatic, a person confirms it
                s = min(s, auto - 0.01)
            if s > best_s:
                best, best_s = r, s
        if best is not None and best_s >= auto:
            assignments[h["path"]] = (best["row_index"], round(best_s, 3), "name + sex + age + date")
        elif best is not None and best_s >= review_t:
            review.append({"xray_file": h["path"], "xray_name": h["patient_name"], "xray_id": h["patient_id"],
                           "xray_date": h["study_date"], "study_uid": h.get("study_uid", ""), "suggested_row_index": best["row_index"],
                           "suggested_name": best["name"], "score": round(best_s, 3)})
        else:
            unmatched.append({"xray_file": h["path"], "xray_name": h["patient_name"], "xray_id": h["patient_id"],
                              "xray_date": h["study_date"], "best_score": round(best_s, 3)})
    return assignments, review, unmatched


def link_reports(rows, reports, cfg):
    """Link radiology reports to admissions.
    rows: dicts with row_index, pid, adm, name, sex, age, admit, discharge.
    reports: dicts from radiology.read_reports.
    Returns (assignments {report_key: (row_index, score, method, encounter_linked)}, review, unmatched)."""
    L = cfg.get("linking", {})
    before = dt.timedelta(days=int(L.get("days_before_admission", 2)))
    after = dt.timedelta(days=int(L.get("days_after_discharge", 2)))
    auto, review_t = float(L.get("auto", 0.92)), float(L.get("review", 0.75))

    def in_window(row, d):
        if d is None or row["admit"] is None:
            return False
        return row["admit"] - before <= d <= (row["discharge"] or row["admit"]) + after

    by_adm, by_pid = {}, {}
    for r in rows:
        if r.get("adm"):
            by_adm.setdefault(norm_id(r["adm"]), []).append(r)
        if r["pid"]:
            by_pid.setdefault(norm_id(r["pid"]), []).append(r)
    assignments, review, unmatched = {}, [], []
    for rep in reports:
        d = rep.get("date")
        base = {"report_key": rep["key"], "report_source": rep["source"], "report_name": rep.get("name", ""),
                "report_id": rep.get("pid", "") or rep.get("adm", ""), "report_date": d or "", "report_study": rep.get("study", "") or rep.get("modality", "")}
        # 1. admission / IP number
        c = by_adm.get(norm_id(rep["adm"]), []) if rep.get("adm") else []
        if len(c) == 1:
            assignments[rep["key"]] = (c[0]["row_index"], 1.0, "admission number", True)
            continue
        # 2. patient number
        c = by_pid.get(norm_id(rep["pid"]), []) if rep.get("pid") else []
        if c:
            w = [r for r in c if in_window(r, d)]
            if len(w) == 1:
                assignments[rep["key"]] = (w[0]["row_index"], 1.0, "patient number + date", True)
            elif d is None and len(c) == 1:
                assignments[rep["key"]] = (c[0]["row_index"], 0.95, "patient number (only admission, report undated)", True)
            else:
                pick = min(c, key=lambda r: abs(((r["admit"] or d or dt.date.min) - (d or r["admit"] or dt.date.min)).days)
                           if (r["admit"] and d) else 10 ** 6)
                assignments[rep["key"]] = (pick["row_index"], 1.0, "patient number (outside admission)", False)
            continue
        # 3. name + sex + age, report date inside an admission
        best, best_s = None, 0.0
        if d is not None and rep.get("name"):
            for r in rows:
                if not in_window(r, d):
                    continue
                ns = name_score(rep["name"], r["name"])
                sx = 1.0 if norm_sex(rep.get("sex")) and norm_sex(rep.get("sex")) == norm_sex(r["sex"]) else 0.0
                ag = 0.5
                if rep.get("age") is not None and r["age"] is not None:
                    ag = 1.0 if abs(rep["age"] - r["age"]) <= 1.5 else 0.0
                s = 0.6 * ns + 0.2 * sx + 0.2 * ag
                if ns < 0.97:
                    s = min(s, auto - 0.01)
                if s > best_s:
                    best, best_s = r, s
        if best is not None and best_s >= auto:
            assignments[rep["key"]] = (best["row_index"], round(best_s, 3), "name + sex + age + date", True)
        elif best is not None and best_s >= review_t:
            review.append({**base, "suggested_row_index": best["row_index"], "suggested_name": best["name"], "score": round(best_s, 3)})
        else:
            why = "no date on report" if d is None else ("no name or patient number" if not rep.get("name") else "no matching admission")
            unmatched.append({**base, "best_score": round(best_s, 3), "reason": why})
    return assignments, review, unmatched
