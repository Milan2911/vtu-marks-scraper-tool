"""Result analysis: pass statistics, SGPA, and revaluation before/after comparison."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# VTU 2022-scheme grade table: (minimum percentage of marks, grade point). Below 40% (or fail) = 0.
# If your batch follows a different table, edit this list.
GRADE_SCALE = [(90, 10), (80, 9), (70, 8), (60, 7), (55, 6), (50, 5), (40, 4)]

SOURCES = ("regular", "arrear", "reval_regular", "reval_arrear")


class DataError(Exception):
    """Problem with the selected files (shown to the user as-is)."""


class MissingCredits(Exception):
    def __init__(self, codes):
        super().__init__("Credits missing for: " + ", ".join(codes))
        self.codes = list(codes)


@dataclass
class Credit:
    credits: float
    max_marks: float = 100.0


# =============================================================================== helpers
def grade_point(total, max_marks=100.0) -> float:
    if pd.isna(total) or not max_marks:
        return np.nan
    pct = float(total) / float(max_marks) * 100
    for floor, gp in GRADE_SCALE:
        if pct >= floor - 1e-9:
            return float(gp)
    return 0.0


def clean_result(value) -> str:
    s = str(value).strip().upper()
    if s in ("", "NAN", "-", "NONE"):
        return ""
    for letter in "PFAWX":
        if s.startswith(letter):          # 'P *' -> 'P', 'ABSENT' -> 'A' ...
            return letter
    return s


def _num(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _txt(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].fillna("").astype(str).str.strip()


def _pick(cols, must, avoid=()):
    for c in cols:
        low = str(c).lower()
        if all(m in low for m in must) and not any(a in low for a in avoid):
            return c
    return None


def _label(code: str, subject: str) -> str:
    return f"{subject} ({code})"


# =============================================================================== loading
def _normalize_results(raw: pd.DataFrame) -> pd.DataFrame:
    cols = list(raw.columns)
    c_int = _pick(cols, ["internal"])
    c_ext = _pick(cols, ["external"], avoid=["old", "final", "new"])
    c_tot = _pick(cols, ["total"], avoid=["old", "final"])
    c_res = _pick(cols, ["result"], avoid=["old", "final"])
    missing = [n for n, c in (("Internal", c_int), ("External", c_ext), ("Total", c_tot), ("Result", c_res)) if c is None]
    if missing:
        raise DataError(f"Could not find the {', '.join(missing)} column(s) in the CSV. Columns found: {cols}")
    out = pd.DataFrame({
        "usn": _txt(raw, "usn").str.upper(),
        "name": _txt(raw, "student_name").str.upper(),
        "sem": _txt(raw, "sem"),
        "code": _txt(raw, "subject_code").str.upper(),
        "subject": _txt(raw, "subject_name"),
        "internal": _num(raw[c_int]),
        "external": _num(raw[c_ext]),
        "total": _num(raw[c_tot]),
        "result": raw[c_res].map(clean_result),
    })
    missing_total = out["total"].isna() & out["internal"].notna() & out["external"].notna()
    out.loc[missing_total, "total"] = out.loc[missing_total, "internal"] + out.loc[missing_total, "external"]
    return out.drop_duplicates(subset=["usn", "code", "sem"], keep="last").reset_index(drop=True)


def _normalize_reval(raw: pd.DataFrame) -> pd.DataFrame:
    cols = list(raw.columns)
    c_fm = _pick(cols, ["final", "mark"]) or _pick(cols, ["reval", "mark"]) or _pick(cols, ["new", "mark"])
    c_fr = _pick(cols, ["final", "result"]) or _pick(cols, ["reval", "result"]) or _pick(cols, ["new", "result"])
    if c_fm is None or c_fr is None:
        raise DataError("The revaluation CSV has no 'Final Marks' / 'Final Result' columns. "
                        f"Columns found: {cols}")
    c_or = _pick(cols, ["old", "result"])
    out = pd.DataFrame({
        "usn": _txt(raw, "usn").str.upper(),
        "sem": _txt(raw, "sem"),
        "code": _txt(raw, "subject_code").str.upper(),
        "final_marks": _num(raw[c_fm]),          # '-' or blank (result awaited / no change) -> NaN, never crashes
        "final_result": raw[c_fr].map(clean_result),
        "old_result": raw[c_or].map(clean_result) if c_or else "",
    })
    return out.drop_duplicates(subset=["usn", "code", "sem"], keep="last").reset_index(drop=True)


@dataclass
class Dataset:
    regular: pd.DataFrame
    arrear: pd.DataFrame
    reval: pd.DataFrame
    credits_csv: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def regular_subjects(self) -> list[tuple[str, str]]:
        d = self.regular.drop_duplicates("code").sort_values(["sem", "code"])
        return list(zip(d["code"], d["subject"]))


def load_dataset(paths) -> Dataset:
    frames: dict[str, list] = {s: [] for s in SOURCES}
    credits_csv: dict[str, Credit] = {}
    for p in paths:
        p = Path(p)
        try:
            df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except Exception as exc:
            raise DataError(f"Could not read {p.name}: {exc}") from exc
        cols = set(df.columns)
        if {"subject_code", "credits"} <= cols and "usn" not in cols:
            for _, r in df.iterrows():
                try:
                    c = float(str(r["credits"]).strip())
                except ValueError:
                    continue
                try:
                    mm = float(str(r.get("max_marks", "100")).strip() or 100)
                except ValueError:
                    mm = 100.0
                credits_csv[str(r["subject_code"]).strip().upper()] = Credit(c, mm)
        elif {"usn", "source", "sem", "subject_code"} <= cols:
            for src, g in df.groupby("source"):
                if src not in frames:
                    raise DataError(f"{p.name}: unknown data type '{src}'.")
                frames[src].append(g)
        else:
            raise DataError(f"{p.name} was not created by this tool (or is an old-version file). "
                            "Please collect the data again with the Collect tab.")

    notes = []
    if not frames["regular"] and frames["arrear"]:
        # Makeup / backlog exams: there is no 'regular' file, so analyse the arrear results as the main data.
        frames["regular"], frames["arrear"] = frames["arrear"], []
        notes.append("No Raw Regular file was selected, so the Raw Arrear results were analysed as the main data.")
    if not frames["regular"]:
        raise DataError("Select at least one 'Raw Regular' or 'Raw Arrear' result file (reval / credit files alone are not enough).")

    regular = _normalize_results(pd.concat(frames["regular"], ignore_index=True))
    arrear = (_normalize_results(pd.concat(frames["arrear"], ignore_index=True))
              if frames["arrear"] else pd.DataFrame(columns=regular.columns))
    rv = frames["reval_regular"] + frames["reval_arrear"]
    reval = _normalize_reval(pd.concat(rv, ignore_index=True)) if rv else pd.DataFrame()
    return Dataset(regular, arrear, reval, credits_csv, notes)


# =============================================================================== reval
def apply_reval(base: pd.DataFrame, reval: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (after_df, details). details has one row per subject that has a reval record."""
    if base.empty or reval.empty:
        return base.copy(), pd.DataFrame()
    m = base.merge(reval.rename(columns={"final_result": "rv_result", "old_result": "rv_old_result",
                                         "final_marks": "rv_marks"}),
                   on=["usn", "code", "sem"], how="left", indicator=True)
    has_rec = m["_merge"] == "both"
    changed = has_rec & m["rv_marks"].notna()
    after = base.copy()
    after.loc[changed.values, "external"] = m.loc[changed, "rv_marks"].values
    after.loc[changed.values, "total"] = (after.loc[changed.values, "internal"].fillna(0)
                                          + after.loc[changed.values, "external"]).values
    has_res = changed & (m["rv_result"].fillna("") != "")
    after.loc[has_res.values, "result"] = m.loc[has_res, "rv_result"].values

    det = pd.DataFrame({
        "USN": m["usn"], "Student Name": m["name"], "Sem": m["sem"],
        "Subject Code": m["code"], "Subject Name": m["subject"], "Internal": m["internal"],
        "External Before": m["external"], "External After": after["external"],
        "Total Before": m["total"], "Total After": after["total"],
        "Result Before": m["result"], "Result After": after["result"],
        "_has": has_rec.values, "_changed": changed.values,
    })
    det = det[det["_has"]].copy()
    det["Marks Change"] = det["External After"] - det["External Before"]
    det["Status"] = np.where(~det["_changed"], "Result awaited / no data",
                             np.where(det["Marks Change"] > 0, "Marks increased",
                                      np.where(det["Marks Change"] < 0, "Marks decreased", "No change")))
    return after, det.drop(columns=["_has", "_changed"]).reset_index(drop=True)


# =============================================================================== statistics
def subject_stats(df: pd.DataFrame) -> pd.DataFrame:
    order = df.drop_duplicates("code").sort_values(["sem", "code"])
    rows = []
    for _, o in order.iterrows():
        g = df[df["code"] == o["code"]]
        cnt = g["result"].value_counts()
        passed, failed, withheld = (int(cnt.get(k, 0)) for k in ("P", "F", "W"))
        appeared = passed + failed + withheld
        att = g[g["result"].isin(["P", "F", "W"])]
        rows.append({
            "Subject Code": o["code"], "Subject Name": o["subject"], "Registered": len(g),
            "Absent": int(cnt.get("A", 0)), "Not Eligible": int(cnt.get("X", 0)), "Withheld": withheld,
            "Failed": failed, "Passed": passed, "Eligible and Appeared": appeared,
            "Subject Pass %": round(passed / appeared * 100, 2) if appeared else 0.0,
            "Average Total": round(float(att["total"].mean()), 2) if att["total"].notna().any() else np.nan,
            "Highest Total": float(g.loc[g["result"] == "P", "total"].max()) if (g["result"] == "P").any() else np.nan,
        })
    return pd.DataFrame(rows)


def student_stats(df: pd.DataFrame) -> dict:
    ct = pd.crosstab(df["usn"], df["result"]).reindex(columns=list("PFAWX"), fill_value=0)
    eligible = ct[["P", "F", "W"]].sum(axis=1) > 0
    n_elig = int(eligible.sum())
    passed_all = eligible & (ct[["F", "A", "W", "X"]].sum(axis=1) == 0)
    fails = ct["F"] + ct["A"]
    return {
        "Total students": int(len(ct)),
        "Eligible students (appeared in at least one subject)": n_elig,
        "Passed in all subjects": int(passed_all.sum()),
        "Failed / absent in at least 1 subject": int((eligible & (fails > 0)).sum()),
        "Failed / absent in exactly 1 subject": int((eligible & (fails == 1)).sum()),
        "Students with withheld results": int((ct["W"] > 0).sum()),
        "Overall pass %": round(int(passed_all.sum()) / n_elig * 100, 2) if n_elig else 0.0,
    }


# =============================================================================== SGPA
def compute_sgpa(df: pd.DataFrame, credits: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (per-student summary, per-subject detail with grade points)."""
    d = df.copy()
    d["credits"] = d["code"].map(lambda c: credits[c].credits)
    d["max_marks"] = d["code"].map(lambda c: credits[c].max_marks)
    gps = []
    for res, tot, mm in zip(d["result"], d["total"], d["max_marks"]):
        if res == "P":
            gps.append(grade_point(tot, mm))
        elif res in ("F", "A"):
            gps.append(0.0)
        else:                                   # W / X: not counted
            gps.append(np.nan)
    d["gp"] = gps

    rows = []
    for usn, g in d.groupby("usn", sort=True):
        counted = g[g["gp"].notna()]
        den = float(counted["credits"].sum())
        num = float((counted["gp"] * counted["credits"]).sum())
        withheld = (g["result"] == "W").any()
        all_absent = (g["result"] == "A").all()
        remark = "Result withheld" if withheld else ("Absent in all subjects" if all_absent else "")
        sgpa = np.nan if (withheld or all_absent or den <= 0) else round(num / den, 2)
        rows.append({
            "USN": usn, "Student Name": g["name"].iloc[0],
            "Credits Registered": float(g["credits"].sum()),
            "Credits Earned": float(g.loc[g["result"] == "P", "credits"].sum()),
            "Backlogs (F/A)": int(g["result"].isin(["F", "A"]).sum()),
            "SGPA": sgpa, "Remark": remark,
        })
    summ = pd.DataFrame(rows)
    summ["Rank"] = summ["SGPA"].rank(method="min", ascending=False)
    summ["Rank"] = summ["Rank"].map(lambda v: int(v) if pd.notna(v) else None).astype(object)
    return summ, d


def sgpa_bands(sgpa: pd.Series) -> pd.DataFrame:
    s = sgpa.dropna()
    bands = [("9.00 and above", s >= 9), ("8.00 - 8.99", (s >= 8) & (s < 9)), ("7.00 - 7.99", (s >= 7) & (s < 8)),
             ("6.00 - 6.99", (s >= 6) & (s < 7)), ("5.00 - 5.99", (s >= 5) & (s < 6)), ("Below 5.00", s < 5)]
    return pd.DataFrame({"SGPA range": [b[0] for b in bands], "Students": [int(b[1].sum()) for b in bands]})


# =============================================================================== wide table
def to_wide(df: pd.DataFrame) -> pd.DataFrame:
    """One row per student; for each subject: Internal / External / Total / Result."""
    subs = df.drop_duplicates("code").sort_values(["sem", "code"])
    d = df.drop_duplicates(["usn", "code"], keep="last").set_index(["usn", "code"])
    students = df.drop_duplicates("usn").sort_values("usn")[["usn", "name"]].reset_index(drop=True)
    data = {("USN", ""): students["usn"].tolist(), ("Student Name", ""): students["name"].tolist()}
    totals = np.zeros(len(students))
    for _, s in subs.iterrows():
        lab = _label(s["code"], s["subject"])
        for field_name, col in (("Internal Marks", "internal"), ("External Marks", "external"),
                                ("Total", "total"), ("Result", "result")):
            vals = []
            for usn in students["usn"]:
                try:
                    vals.append(d.at[(usn, s["code"]), col])
                except KeyError:
                    vals.append(np.nan if col != "result" else "")
            data[(lab, field_name)] = vals
            if col == "total":
                totals += np.nan_to_num(np.array(vals, dtype=float))
    data[("Overall Total", "")] = totals.tolist()
    return pd.DataFrame(data)


# =============================================================================== main entry
@dataclass
class AnalysisResult:
    dataset: Dataset
    has_reval: bool
    before: pd.DataFrame
    after: pd.DataFrame
    arrear_before: pd.DataFrame
    arrear_after: pd.DataFrame
    subj_before: pd.DataFrame
    subj_after: pd.DataFrame
    stats_before: dict
    stats_after: dict
    credits: dict | None
    sgpa_before: pd.DataFrame | None
    sgpa_after: pd.DataFrame | None
    sgpa_detail_after: pd.DataFrame | None
    reval_details: pd.DataFrame
    reval_subject: pd.DataFrame
    comparison: pd.DataFrame
    sgpa_compare: pd.DataFrame | None
    summary_lines: list
    student_rows: list
    first_usn: str
    last_usn: str


def _reval_subject_summary(details: pd.DataFrame, subj_before: pd.DataFrame, subj_after: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, s in subj_before.iterrows():
        code = s["Subject Code"]
        d = details[details["Subject Code"] == code]
        if d.empty:
            continue
        inc = d[d["Status"] == "Marks increased"]
        failed_before = d[d["Result Before"].isin(["F", "A"])]
        converted = failed_before[failed_before["Result After"] == "P"]
        rows.append({
            "Subject Code": code, "Subject Name": s["Subject Name"],
            "Applied for Reval": len(d),
            "Result Awaited": int((d["Status"] == "Result awaited / no data").sum()),
            "Marks Increased": len(inc),
            "No Change": int((d["Status"] == "No change").sum()),
            "Marks Decreased": int((d["Status"] == "Marks decreased").sum()),
            "Average Gain (increased)": round(float(inc["Marks Change"].mean()), 2) if len(inc) else 0.0,
            "Highest Gain": float(inc["Marks Change"].max()) if len(inc) else 0.0,
            "Failed Before (among applied)": len(failed_before),
            "Changed to Pass": len(converted),
            "Conversion %": round(len(converted) / len(failed_before) * 100, 2) if len(failed_before) else 0.0,
        })
    return pd.DataFrame(rows)


def analyze(ds: Dataset, credits: dict | None = None) -> AnalysisResult:
    before = ds.regular
    has_reval = not ds.reval.empty
    after, details = apply_reval(before, ds.reval) if has_reval else (before, pd.DataFrame())
    arrear_before = ds.arrear
    arrear_after = apply_reval(arrear_before, ds.reval)[0] if (has_reval and not arrear_before.empty) else arrear_before

    if credits is not None:
        missing = sorted(set(before["code"]) - set(credits))
        if missing:
            raise MissingCredits(missing)

    subj_b, subj_a = subject_stats(before), subject_stats(after)
    st_b, st_a = student_stats(before), student_stats(after)

    sg_b = sg_a = detail_a = sg_cmp = None
    if credits is not None:
        sg_a, detail_a = compute_sgpa(after, credits)
        sg_b = compute_sgpa(before, credits)[0] if has_reval else sg_a
        if has_reval:
            sg_cmp = sg_b[["USN", "Student Name", "SGPA"]].rename(columns={"SGPA": "SGPA Before"}).merge(
                sg_a[["USN", "SGPA"]].rename(columns={"SGPA": "SGPA After"}), on="USN")
            sg_cmp["Change"] = (sg_cmp["SGPA After"] - sg_cmp["SGPA Before"]).round(2)

    comp_rows = [(k, st_b[k], st_a[k], round(st_a[k] - st_b[k], 2) if isinstance(st_a[k], (int, float)) else "")
                 for k in st_a]
    if sg_cmp is not None:
        comp_rows.append(("Average SGPA", round(float(sg_cmp["SGPA Before"].mean()), 2),
                          round(float(sg_cmp["SGPA After"].mean()), 2),
                          round(float(sg_cmp["SGPA After"].mean() - sg_cmp["SGPA Before"].mean()), 2)))
        comp_rows.append(("Students whose SGPA improved", "", int((sg_cmp["Change"] > 0).sum()), ""))
    comparison = pd.DataFrame(comp_rows, columns=["Metric", "Before Reval", "After Reval", "Change"])

    reval_subject = _reval_subject_summary(details, subj_b, subj_a) if has_reval else pd.DataFrame()

    # ---- text summary for the GUI
    lines = [f"Students: {st_a['Total students']}   Eligible: {st_a['Eligible students (appeared in at least one subject)']}",
             f"Passed in all subjects: {st_a['Passed in all subjects']}   Overall pass %: {st_a['Overall pass %']}"]
    if has_reval:
        lines.insert(0, "REVALUATION INCLUDED (figures below are AFTER reval)")
        lines.append(f"Overall pass % before reval: {st_b['Overall pass %']}  ->  after: {st_a['Overall pass %']}")
        lines.append(f"Reval records: {len(details)}   Marks increased: {int((details['Status'] == 'Marks increased').sum())}"
                     f"   Failed -> Pass: {int(reval_subject['Changed to Pass'].sum()) if len(reval_subject) else 0}")
    if sg_a is not None:
        valid = sg_a["SGPA"].dropna()
        if len(valid):
            lines.append(f"Average SGPA: {valid.mean():.2f}   Highest: {valid.max():.2f}")
        if sg_cmp is not None:
            lines.append(f"Average SGPA before reval: {sg_cmp['SGPA Before'].mean():.2f}  ->  after: {sg_cmp['SGPA After'].mean():.2f}"
                         f"   (improved for {int((sg_cmp['Change'] > 0).sum())} students)")
    else:
        lines.append("SGPA not calculated (no credits supplied).")
    worst = subj_a.sort_values("Subject Pass %").head(1)
    if len(worst):
        lines.append(f"Lowest subject pass %: {worst.iloc[0]['Subject Name']} ({worst.iloc[0]['Subject Code']}) "
                     f"- {worst.iloc[0]['Subject Pass %']}%")

    # ---- rows for the GUI table
    base = sg_a if sg_a is not None else pd.DataFrame({"USN": sorted(after["usn"].unique())})
    rows = []
    names = after.drop_duplicates("usn").set_index("usn")["name"].to_dict()
    for _, r in base.iterrows():
        usn = r["USN"]
        sb = sa = chg = ""
        if sg_a is not None:
            sa = "" if pd.isna(r["SGPA"]) else f"{r['SGPA']:.2f}"
            sb = sa
        if sg_cmp is not None:
            c = sg_cmp[sg_cmp["USN"] == usn]
            if len(c):
                sb = "" if pd.isna(c.iloc[0]["SGPA Before"]) else f"{c.iloc[0]['SGPA Before']:.2f}"
                chg = "" if pd.isna(c.iloc[0]["Change"]) else f"{c.iloc[0]['Change']:+.2f}"
        rows.append((usn, names.get(usn, ""), sb, sa, chg))

    usns = sorted(after["usn"].unique())
    return AnalysisResult(ds, has_reval, before, after, arrear_before, arrear_after, subj_b, subj_a, st_b, st_a,
                          credits, sg_b, sg_a, detail_a, details, reval_subject, comparison, sg_cmp,
                          lines, rows, usns[0], usns[-1])
