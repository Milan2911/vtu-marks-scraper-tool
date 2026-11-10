"""Offline test: fake VTU result pages -> parser -> CSV -> analysis -> Excel. Run: python tests/test_pipeline.py"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from openpyxl import load_workbook
from vtutool.scraper import parse_result_page, parse_numbers, ScrapeConfig, save_rows
from vtutool.analysis import load_dataset, analyze, Credit, MissingCredits, grade_point

def cell(t): return f'<div class="divTableCell">{t}</div>'
def row(cells): return '<div class="divTableRow">' + ''.join(cell(c) for c in cells) + '</div>'
def page(usn, name, sems):
    h = ('<html><body><div><div><div>University Seat Number</div><div>:</div><div>%s</div></div>'
         '<div><div>Student Name</div><div>: %s</div></div>' % (usn, name))
    for sem, header, rows in sems:
        h += f'<div style="text-align:center;padding:5px;"><b>Semester : {sem}</b></div>'
        h += '<div><div class="divTable"><div class="divTableBody">' + row(header) + ''.join(row(r) for r in rows) + '</div></div></div>'
    return h + '</div></body></html>'

REG_H = ['Subject Code', 'Subject Name', 'Internal Marks', 'External Marks', 'Total', 'Result', 'Announced / Updated on']
RV_H = ['Subject Code', 'Subject Name', 'Old Marks', 'Old Result', 'Final Marks', 'Final Result', 'Announced / Updated on']
# (int, ext, result) for S1 S2 S3
MARKS = {
    1: [(45, 50, 'P'), (40, 45, 'P'), (48, 42, 'P')],      # 95, 85, 90
    2: [(30, 30, 'P'), (25, 20, 'P'), (30, 12, 'F')],      # 60, 45, F   (S3 fails; reval -> 30 ext)
    3: [(20, 10, 'F'), (30, 40, 'P'), (35, 30, 'P')],      # F, 70, 65   (S1 fails; reval '-' = awaited)
    4: [(0, 0, 'A'), (0, 0, 'A'), (0, 0, 'A')],            # fully absent
    5: [(40, 40, 'P *'), (40, 40, 'P'), (40, 40, 'P')],    # 80, 80, 80
}
SUBS = [('21XX51', 'Alpha'), ('21XX52', 'Beta'), ('21XX53', 'Gamma')]

def reg_page(n):
    rows = [[c, s, str(i), str(e), str(i + e), r, '2026-01-01'] for (c, s), (i, e, r) in zip(SUBS, MARKS[n])]
    return page(f'1AB24CS{n:03d}', f'STUDENT {n}', [('5', REG_H, rows)])

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    # ---------------- parser
    u, nm, rows = parse_result_page(reg_page(2))
    assert (u, nm) == ('1AB24CS002', 'STUDENT 2'), (u, nm)
    assert len(rows) == 3 and rows[2]['External Marks'] == '12', rows
    assert parse_numbers('1-3,5,2') == [1, 2, 3, 5]
    # ---------------- save CSVs (regular + one arrear row set + reval)
    cfg = ScrapeConfig('1AB24CS', [1], 5, 'https://results.vtu.ac.in/JJ26cbcs/index.php', tmp)
    all_rows = []
    for n in MARKS:
        all_rows += parse_result_page(reg_page(n))[2]
    # arrear semester 3 page for student 3
    arr = page('1AB24CS003', 'STUDENT 3', [('3', REG_H, [['21XX31', 'Old Sub', '20', '25', '45', 'P', 'x']])])
    all_rows += parse_result_page(arr)[2]
    files = save_rows(all_rows, cfg)
    names = sorted(p.name for p in files); print(names)
    assert any('Regular semester 5' in n for n in names) and any('Arrear semester 3' in n for n in names)

    rcfg = ScrapeConfig('1AB24CS', [1], 5, 'https://results.vtu.ac.in/RVJJ26cbcs/index.php', tmp)
    rv_rows = []
    rv_rows += parse_result_page(page('1AB24CS002', 'STUDENT 2', [('5', RV_H, [['21XX53', 'Gamma', '42', 'F', '30', 'P', 'x']])]))[2]   # ext 12 -> 30
    rv_rows += parse_result_page(page('1AB24CS003', 'STUDENT 3', [('5', RV_H, [['21XX51', 'Alpha', '10', 'F', '-', '-', 'x']])]))[2]       # awaited
    rv_rows += parse_result_page(page('1AB24CS001', 'STUDENT 1', [('5', RV_H, [['21XX52', 'Beta', '45', 'P', '50', 'P', 'x']])]))[2]       # 45 -> 50
    rfiles = save_rows(rv_rows, rcfg)
    print(sorted(p.name for p in rfiles))

    credit_csv = [p for p in files if p.name.startswith('Credit')][0]
    print(credit_csv.read_text())
    # fill credits like a user would
    cdf = pd.read_csv(credit_csv, dtype=str, keep_default_na=False)
    cdf['credits'] = ['4', '3', '2']
    cdf.to_csv(credit_csv, index=False)

    # ---------------- analysis without reval
    reg_files = [p for p in files if p.suffix == '.csv']
    ds = load_dataset(reg_files)
    assert not ds.credits_csv == {} and ds.credits_csv['21XX51'].credits == 4
    res = analyze(ds, ds.credits_csv)
    sg = res.sgpa_after.set_index('USN')['SGPA']
    # S1: gp 10,9,10 -> (40+27+20)/9 = 9.67 ; S2: 7,4,F0 -> (28+12+0)/9 = 4.44
    # S3: F0,8,7 -> (0+24+14)/9 = 4.22 ; S4 absent -> NaN ; S5: 9,9,9 -> 9.0 (note 'P *' treated as P)
    assert round(sg['1AB24CS001'], 2) == 9.67, sg
    assert round(sg['1AB24CS002'], 2) == 4.44, sg
    assert round(sg['1AB24CS003'], 2) == 4.22, sg
    assert pd.isna(sg['1AB24CS004']) and sg['1AB24CS005'] == 9.0, sg
    st = res.stats_after
    print(st)
    # eligible = 4 (S4 absent in all). passed all = S1, S5 -> 2 -> 50%
    assert st['Eligible students (appeared in at least one subject)'] == 4 and st['Passed in all subjects'] == 2 and st['Overall pass %'] == 50.0
    assert grade_point(45, 50) == 10 and grade_point(39, 100) == 0

    # ---------------- analysis WITH reval
    ds2 = load_dataset(reg_files + [p for p in rfiles if p.name.startswith('Raw')])
    res2 = analyze(ds2, ds2.credits_csv)
    cmp_ = res2.sgpa_compare.set_index('USN')
    # S2 Gamma ext 12 -> 30: total 30+30=60 -> gp 7 ; SGPA (28+12+14)/9 = 6.0 ; was 4.44
    assert round(cmp_.loc['1AB24CS002', 'SGPA After'], 2) == 6.0 and round(cmp_.loc['1AB24CS002', 'SGPA Before'], 2) == 4.44
    # S1 Beta 50 ext: total 40+50=90 -> gp 10 : (40+30+20)/9 = 10.0
    assert round(cmp_.loc['1AB24CS001', 'SGPA After'], 2) == 10.0
    # S3 awaited: unchanged
    assert cmp_.loc['1AB24CS003', 'Change'] == 0
    assert res2.stats_before['Overall pass %'] == 50.0 and res2.stats_after['Overall pass %'] == 75.0, (res2.stats_before, res2.stats_after)
    print(res2.reval_subject.to_string())
    print('\n'.join(res2.summary_lines))

    # ---------------- missing credits detection
    try:
        analyze(ds, {'21XX51': Credit(4)})
        raise SystemExit('should have raised')
    except MissingCredits as e:
        assert e.codes == ['21XX52', '21XX53']

    # ---------------- Excel
    from vtutool.excel_export import export_excel
    for r_, nm_ in ((res, 'plain.xlsx'), (res2, 'reval.xlsx')):
        out = export_excel(r_, tmp / nm_)
        wb = load_workbook(out)
        print(nm_, wb.sheetnames)
    ws = wb['Subject Analysis']; assert len(ws._charts) == 1
    ws = wb['SGPA Before vs After']; print([c.value for c in ws[1]])
    # no-credits path
    res3 = analyze(ds, None); export_excel(res3, tmp / 'nocred.xlsx')
    Path('/home/claude/sample_reval.xlsx').write_bytes((tmp / 'reval.xlsx').read_bytes())
print('ALL TESTS PASSED')
