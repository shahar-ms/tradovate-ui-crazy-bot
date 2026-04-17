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
echo   Tradovate UI crazy bot
echo =====================================================
echo   1. Calibrate         (mark regions + click points)
echo   2. Validate          (check calibration)
echo   3. Price debug       (live price stream, no trading)
echo   4. Paper mode        (strategy on, clicks OFF)
echo   5. ARMED mode        (live clicks -- requires confirm)
echo   6. Replay synthetic  (300 synth ticks through engine)
echo   7. Overlay preview   (draw calibrated points on screen)
echo   8. Run tests         (pytest)
echo   9. Install / upgrade Python deps
echo   0. Exit
echo -----------------------------------------------------
set /p "choice=Select: "

if "%choice%"=="1" goto calibrate
if "%choice%"=="2" goto validate
if "%choice%"=="3" goto pricedebug
if "%choice%"=="4" goto paper
if "%choice%"=="5" goto armed
if "%choice%"=="6" goto replay
if "%choice%"=="7" goto overlay
if "%choice%"=="8" goto tests
if "%choice%"=="9" goto deps
if "%choice%"=="0" goto end
goto menu

:calibrate
"%PY%" -m app.calibration.calibrator
pause
goto menu

:validate
"%PY%" -m app.calibration.validator
pause
goto menu

:pricedebug
"%PY%" -m app.orchestrator.runbot --mode PRICE_DEBUG
pause
goto menu

:paper
"%PY%" -m app.orchestrator.runbot --mode PAPER
pause
goto menu

:armed
echo.
echo !!! ARMED MODE: the bot will perform REAL clicks on your screen.  !!!
echo !!! Make sure you are on a SIM account, one contract size, and    !!!
echo !!! you are supervising the screen. Move cursor to a corner to    !!!
echo !!! trigger PyAutoGUI failsafe if anything goes wrong.             !!!
echo.
set /p "confirm=Type ARM to proceed (anything else cancels): "
if /I not "%confirm%"=="ARM" (
    echo cancelled.
    pause
    goto menu
)
"%PY%" -m app.orchestrator.runbot --mode ARMED
pause
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
