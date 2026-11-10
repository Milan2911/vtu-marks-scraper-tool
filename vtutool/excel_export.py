"""Write an AnalysisResult to a formatted Excel workbook (openpyxl only - no matplotlib needed)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .analysis import AnalysisResult, sgpa_bands, to_wide

HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
SUB_FILL = PatternFill("solid", fgColor="D9E1F2")
BAD_FILL = PatternFill("solid", fgColor="F8CBAD")
GOOD_FILL = PatternFill("solid", fgColor="C6E0B4")
HEAD_FONT = Font(bold=True, color="FFFFFF")
BOLD = Font(bold=True)


def _py(v):
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if pd.isna(v) else float(v)
    if isinstance(v, str) or isinstance(v, (int, bool)):
        return v
    return None if pd.isna(v) else v


def _flat(ws, df: pd.DataFrame, row: int = 1, title: str | None = None) -> int:
    """Write a DataFrame with a styled header. Returns the next free row."""
    if title:
        ws.cell(row=row, column=1, value=title).font = Font(bold=True, size=12)
        row += 1
    for j, c in enumerate(df.columns, 1):
        cell = ws.cell(row=row, column=j, value=str(c))
        cell.font, cell.fill = HEAD_FONT, HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    for i, rec in enumerate(df.itertuples(index=False), row + 1):
        for j, v in enumerate(rec, 1):
            ws.cell(row=i, column=j, value=_py(v))
    return row + len(df) + 2


def _autosize(ws, cap: int = 42):
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value is not None and not (c.coordinate in ws.merged_cells):
                widths[c.column] = max(widths.get(c.column, 0), min(len(str(c.value)), cap))
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(8, w + 2)


def _wide(ws, wide: pd.DataFrame):
    """Two-row header: subject name (merged over its 4 columns) + field name."""
    cols = list(wide.columns)
    j = 1
    while j <= len(cols):
        top, sub = cols[j - 1]
        span = 1
        while j + span <= len(cols) and cols[j + span - 1][0] == top and top not in ("USN", "Student Name", "Overall Total"):
            span += 1
        c = ws.cell(row=1, column=j, value=top)
        c.font, c.fill = HEAD_FONT, HEAD_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if span > 1:
            ws.merge_cells(start_row=1, start_column=j, end_row=1, end_column=j + span - 1)
        for k in range(span):
            s = ws.cell(row=2, column=j + k, value=cols[j + k - 1][1] or None)
            s.font, s.fill = BOLD, SUB_FILL
            s.alignment = Alignment(horizontal="center")
        j += span
    for i, rec in enumerate(wide.itertuples(index=False), 3):
        for j, v in enumerate(rec, 1):
            cell = ws.cell(row=i, column=j, value=_py(v))
            if cols[j - 1][1] == "Result" and v in ("F", "A"):
                cell.fill = BAD_FILL
    ws.freeze_panes = "C3"


def _chart(ws, anchor: str, n_rows: int, header_row: int, cat_col: int, val_cols: list[int], title: str):
    ch = BarChart()
    ch.type, ch.title = "col", title
    ch.y_axis.title, ch.x_axis.title = "Pass %", "Subject code"
    ch.y_axis.scaling.min, ch.y_axis.scaling.max = 0, 100
    for vc in val_cols:
        ch.add_data(Reference(ws, min_col=vc, min_row=header_row, max_row=header_row + n_rows), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=cat_col, min_row=header_row + 1, max_row=header_row + n_rows))
    ch.width, ch.height = 26, 11
    ws.add_chart(ch, anchor)


def export_excel(res: AnalysisResult, path: str | Path) -> Path:
    path = Path(path)
    wb = Workbook()

    # ------------------------------------------------------------------ Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = f"VTU result analysis: {res.first_usn} to {res.last_usn}"
    ws["A1"].font = Font(bold=True, size=14)
    r = 3
    if res.has_reval:
        r = _flat(ws, res.comparison, r, "Before vs After Revaluation")
    else:
        kv = pd.DataFrame({"Metric": list(res.stats_after.keys()), "Value": list(res.stats_after.values())})
        r = _flat(ws, kv, r, "Overall statistics")
    if res.sgpa_after is not None:
        r = _flat(ws, sgpa_bands(res.sgpa_after["SGPA"]), r, "SGPA distribution" + (" (after reval)" if res.has_reval else ""))
        top = res.sgpa_after.dropna(subset=["SGPA"]).sort_values("SGPA", ascending=False).head(10)
        r = _flat(ws, top[["Rank", "USN", "Student Name", "SGPA"]], r, "Top 10 by SGPA")
        cred = pd.DataFrame([(c, res.credits[c].credits, res.credits[c].max_marks)
                             for c in sorted(res.credits) if c in set(res.before["code"])],
                            columns=["Subject Code", "Credits used", "Max marks used"])
        r = _flat(ws, cred, r, "Credits used for SGPA")
    _autosize(ws)

    # ------------------------------------------------------------------ Results
    w = wb.create_sheet("Student Results" + (" (After Reval)" if res.has_reval else ""))
    _wide(w, to_wide(res.after))
    _autosize(w, 28)
    if res.has_reval:
        w = wb.create_sheet("Results (Before Reval)")
        _wide(w, to_wide(res.before))
        _autosize(w, 28)
    if not res.arrear_after.empty:
        w = wb.create_sheet("Arrear Results")
        _wide(w, to_wide(res.arrear_after))
        _autosize(w, 28)

    # ------------------------------------------------------------------ Subject analysis (+ chart)
    ws = wb.create_sheet("Subject Analysis")
    if res.has_reval:
        t = res.subj_after.copy()
        t.insert(8, "Pass % Before Reval", res.subj_before["Subject Pass %"].values)
        t = t.rename(columns={"Subject Pass %": "Pass % After Reval"})
        t["Pass % Change"] = (t["Pass % After Reval"] - t["Pass % Before Reval"]).round(2)
        _flat(ws, t, 1)
        pcols = [t.columns.get_loc("Pass % Before Reval") + 1, t.columns.get_loc("Pass % After Reval") + 1]
        title = "Subject-wise pass % before vs after reval"
    else:
        t = res.subj_after
        _flat(ws, t, 1)
        pcols = [t.columns.get_loc("Subject Pass %") + 1]
        title = "Subject-wise pass %"
    _autosize(ws)
    ws.freeze_panes = "C2"
    _chart(ws, f"A{len(t) + 4}", len(t), 1, 1, pcols, title)

    # ------------------------------------------------------------------ SGPA
    if res.sgpa_after is not None:
        ws = wb.create_sheet("SGPA")
        t = res.sgpa_after.copy()
        gp = res.sgpa_detail_after.pivot_table(index="usn", columns="code", values="gp", aggfunc="last")
        gp.columns = [f"GP {c}" for c in gp.columns]
        t = t.merge(gp, left_on="USN", right_index=True, how="left")
        _flat(ws, t, 1)
        _autosize(ws)
        ws.freeze_panes = "C2"

    # ------------------------------------------------------------------ Reval sheets
    if res.has_reval:
        ws = wb.create_sheet("Reval Summary")
        r = _flat(ws, res.reval_subject, 1, "Revaluation outcome per subject") if len(res.reval_subject) else 1
        _autosize(ws)
        ws = wb.create_sheet("Reval Details")
        d = res.reval_details
        if res.credits is not None and len(d):
            from .analysis import grade_point
            for lab in ("Before", "After"):
                d[f"Grade Point {lab}"] = [
                    (0.0 if rs in ("F", "A") else grade_point(tot, res.credits[c].max_marks if c in res.credits else 100))
                    for rs, tot, c in zip(d[f"Result {lab}"], d[f"Total {lab}"], d["Subject Code"])]
        _flat(ws, d, 1)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if c.value == "Marks increased":
                    c.fill = GOOD_FILL
        _autosize(ws)
        ws.freeze_panes = "C2"
        if res.sgpa_compare is not None:
            ws = wb.create_sheet("SGPA Before vs After")
            _flat(ws, res.sgpa_compare, 1)
            for row in ws.iter_rows(min_row=2):
                c = row[-1]
                if isinstance(c.value, (int, float)) and c.value > 0:
                    c.fill = GOOD_FILL
            _autosize(ws)
            ws.freeze_panes = "C2"

    wb.save(path)
    return path
