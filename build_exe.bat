@echo off
REM Builds "VTU Marks Scraper Tool V1.0.exe".   Usage:  build_exe.bat          (normal, no console window)
REM                                         build_exe.bat debug    (shows a console - use if the exe misbehaves)
setlocal
cd /d "%~dp0"
set "MODE=--windowed"
if /i "%~1"=="debug" set "MODE=--console"

python --version >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or 3.12 from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

REM ---- find Tesseract: a Tesseract-OCR folder next to this file, or a normal install
set "TESS="
if exist "%~dp0Tesseract-OCR\tesseract.exe" set "TESS=%~dp0Tesseract-OCR"
if not defined TESS if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles%\Tesseract-OCR"
if not defined TESS (
  echo Tesseract OCR not found.
  echo Copy the Tesseract-OCR folder from the old project zip next to build_exe.bat,
  echo or install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki and run this again.
  pause
  exit /b 1
)
echo Using Tesseract from: %TESS%

if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Package installation failed. Check your internet connection and Python version 3.11 or 3.12.
  pause
  exit /b 1
)

pyinstaller --noconfirm --clean --onedir %MODE% --name "VTU Marks Scraper Tool V1.0" --collect-all selenium --icon "assets\icon.ico" --add-data "assets;assets" main.py
if errorlevel 1 (
  echo PyInstaller failed - see the messages above.
  pause
  exit /b 1
)

REM ---- copy Tesseract next to the exe (skipping training tools to save ~100 MB)
robocopy "%TESS%" "dist\VTU Marks Scraper Tool V1.0\Tesseract-OCR" /E /XD doc /XF lstmeval.exe lstmtraining.exe cntraining.exe mftraining.exe combine_lang_model.exe combine_tessdata.exe classifier_tester.exe ambiguous_words.exe dawg2wordlist.exe merge_unicharsets.exe set_unicharset_properties.exe shapeclustering.exe text2image.exe unicharset_extractor.exe wordlist2dawg.exe tesseract-uninstall.exe winpath.exe >nul
if errorlevel 8 (
  echo Copying Tesseract failed.
  pause
  exit /b 1
)

echo.
echo ===== DONE =====
echo Your program is in:  dist\VTU Marks Scraper Tool V1.0\VTU Marks Scraper Tool V1.0.exe
echo Zip the whole "VTU Marks Scraper Tool V1.0" folder to share it. Do not move the exe out of that folder.
pause
