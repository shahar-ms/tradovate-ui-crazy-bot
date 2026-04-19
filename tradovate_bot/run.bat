@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

REM Tradovate UI bot launcher.
REM Run from anywhere; cd's to the folder containing this batch file.
cd /d "%~dp0"

REM Ensure Tesseract is reachable even if PATH does not include it.
if not defined TESSERACT_CMD set "TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe"

REM Prefer a local venv if one exists; otherwise use system python.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

:menu
cls
echo =====================================================
echo   Tradovate bot
echo =====================================================
echo   1. Launch app        (floating HUD, the whole UI)
echo   2. Replay synthetic  (300 synth ticks through engine)
echo   3. Overlay preview   (draw calibrated points on screen)
echo   4. Run tests         (pytest)
echo   I. Install / upgrade Python deps
echo   0. Exit
echo -----------------------------------------------------
set /p "choice=Select: "

if "%choice%"=="1" goto ui
if "%choice%"=="2" goto replay
if "%choice%"=="3" goto overlay
if "%choice%"=="4" goto tests
if /I "%choice%"=="I" goto deps
if "%choice%"=="0" goto end
goto menu

:ui
"%PY%" -m app.ui.run_ui
goto menu

:replay
"%PY%" -m app.strategy.replay --synth 300
pause
goto menu

:overlay
"%PY%" -m app.execution.overlay
pause
goto menu

:tests
"%PY%" -m pytest tests/ -v
pause
goto menu

:deps
"%PY%" -m pip install -r requirements.txt
pause
goto menu

:end
endlocal
