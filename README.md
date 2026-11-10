# VTU Marks Scraper Tool V1.0

Collects VTU results, analyses them, calculates **SGPA**, and compares **before / after revaluation**.
Improved rewrite of *vtu-marks-scraper-analyzer 2.7.8* (GPL-2.0, original by Nithin H M - see LICENSE).

## New modern interface (V1.0)
Dark sidebar navigation, card-based pages, rounded buttons, toggle switch, live progress with Completed / Speed / Time-left
tiles, colour-coded timestamped activity log, toast notifications, field validation highlights, a searchable SGPA table with
green/red change colouring, a restyled credits dialog, high-DPI scaling and a new app icon. Built with plain tkinter - no extra packages.

## What was wrong in the old version (found by reading the code)
| Problem | Effect | Fix |
|---|---|---|
| `sys._MEIPASS2` typo in `captcha_handler.py` | packaged .exe could never find Tesseract | correct lookup + several fallback locations |
| `Tesseract-OCR\tesseract.exe` as a *relative* path | crash if launched from another folder | absolute paths |
| Exact-version `requirements.txt` (numpy 1.26, pandas 2.2.1, lxml 5.1...) | `pip install` fails on Python 3.13 | loose minimum versions, lxml dropped |
| Tk windows/dialogs updated from worker threads, several `Tk()` roots + nested `mainloop()` | random freezes / crashes | one window, thread-safe message queue |
| "Internet check" opened Chrome on a Caltech physics website | false "no internet", driver crash on retry | simple socket test to the VTU server |
| One absolute XPath for the name/USN | breaks on any site change | finds "University Seat Number" / "Student Name" text; saves a debug page if layout changes |
| `int('-')` on reval marks, divisions by zero, results `A`/`W` | analyzer crashes | safe numeric parsing, zero-safe maths |
| Data saved only at the very end | a crash lost everything | autosave every 10 students + save in `finally` |
| Slow pixel-by-pixel captcha clean-up | slow | numpy, plus 2 OCR fallbacks |
| Silent crashes in windowed exe | no idea what happened | everything logged to `~/VTU Marks Scraper Tool/logs/app.log` |

## New features
* **SGPA** per student (rank, credits earned, backlogs); credits are asked once in a small table and remembered.
* **Revaluation before/after**: per-subject outcome (marks increased, failed -> pass, conversion %), per-student marks change,
  overall pass % before vs after, **SGPA before vs after** for every student.
* Excel output with native charts, colour-coded F/A results, summary + top-10 sheet.
* Optional "show browser" mode, Stop button that saves what was collected, polite pacing to avoid IP blocks.

## Build the .exe (Windows)
1. Install **Python 3.11 or 3.12** (python.org). Tick **"Add python.exe to PATH"**. Install **Google Chrome**.
2. Unzip this project. Copy the **`Tesseract-OCR`** folder from your old project zip into this folder
   (next to `build_exe.bat`) - or install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki.
3. Double-click **`build_exe.bat`** (first run downloads packages, takes a few minutes).
4. Your program: **`dist\VTU Marks Scraper Tool V1.0\VTU Marks Scraper Tool V1.0.exe`**. Zip the whole `VTU Marks Scraper Tool V1.0` folder to share it.
   Keep the `Tesseract-OCR` folder next to the exe.

If the exe misbehaves, run `build_exe.bat debug` (shows a console with errors) and check `~/VTU Marks Scraper Tool/logs/app.log`.
To run without building: double-click `run_from_source.bat`.

## Using it
1. **Collect tab** - USN prefix (e.g. `1CR24EC`), numbers (`1-120`), semester, result URL -> *Start collecting*.
   When revaluation results are published, collect again using the reval link (contains `RV`).
2. **Analyze tab** - add the `Raw Regular` file (+ `Raw Arrear`, `Raw Reval`, `Credit info` if you have them) -> *Analyze*.
   Enter credits when asked. Open the Excel file.

First run needs internet so Selenium can fetch the matching chromedriver. Windows SmartScreen / antivirus may warn about an
unsigned exe you built yourself - that is normal for PyInstaller programs ("More info" -> "Run anyway").

## Notes
* Old CSV files from version 2.x use a different layout; collect again with this version.
* SGPA uses the VTU 2022-scheme grade table (`GRADE_SCALE` at the top of `vtutool/analysis.py`) - verify it matches your scheme.
  Failed/absent subjects count as grade point 0; withheld (`W`) results make the SGPA blank.
* Tests (no Chrome needed): `python tests/test_pipeline.py`, `python tests/test_scrape_flow.py`.

* <img width="1202" height="832" alt="image" src="https://github.com/user-attachments/assets/7365e098-d351-4e65-912f-54232ba28c96" />

<img width="1481" height="832" alt="image" src="https://github.com/user-attachments/assets/5781c90e-6a53-470a-a4bb-4e1946c89b16" />


