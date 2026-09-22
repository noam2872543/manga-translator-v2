@echo off
REM =====================================================================
REM build_windows.bat
REM Thin wrapper around build_helper.py.
REM
REM Why a wrapper at all?
REM   `flet pack` needs the same Python that has the deps installed, and a
REM   few one-shot setup steps (pip install, asset check).  Doing the
REM   *argument construction* in Python (build_helper.py) sidesteps every
REM   CMD quoting trap (the old script hit ". was unexpected at this time."
REM   because of nested double-quotes inside `set` and a `for /f` Python
REM   one-liner).
REM
REM Usage (from the project folder, in a Windows Command Prompt):
REM     build_windows.bat            REM full  - bundles manga-ocr + EasyOCR (~2.5GB)
REM     build_windows.bat lite       REM ~190MB, no OCR bundled
REM     build_windows.bat dir        REM portable folder, OCR bundled
REM =====================================================================
setlocal

set "MODE=%~1"
if "%MODE%"=="" set "MODE=full"

echo ============================================================
echo  Manga Translator - Windows build (mode: %MODE%)
echo ============================================================

REM --- 0. Python on PATH? -------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.10-3.12 from https://python.org and re-run.
    exit /b 1
)

REM --- 1. Install dependencies -------------------------------------------
echo.
echo [1/2] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller flet flet-cli
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    exit /b 1
)

REM --- 2. Build via the Python helper (handles all quoting) ---------------
echo.
echo [2/2] Building the executable...
python build_helper.py "%MODE%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Build failed (exit %RC%).
    exit /b %RC%
)

echo.
echo ============================================================
echo  Done.  See dist\ for the output.
echo ============================================================
endlocal
