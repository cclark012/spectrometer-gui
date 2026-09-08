:: Name:    start-gui.bat
:: Purpose: Starts the spectroscopy GUI with WinPython

@echo off
setlocal enableextensions

title Spectroscopy GUI
color 0B

set "ROOT=%~dp0"
set "PYTHON=%ROOT%..\WinPython\python\python.exe"
set "GUI=%ROOT%gui.py"
set "CONFIG=%ROOT%config\lab_defaults.json"
set "LOCAL_CONFIG=%ROOT%config\lab_defaults.local.json"
if exist "%LOCAL_CONFIG%" set "CONFIG=%LOCAL_CONFIG%"

if not exist "%PYTHON%" (
    echo Python executable not found:
    echo   %PYTHON%
    pause
    exit /b 1
)

if not exist "%GUI%" (
    echo GUI script not found:
    echo   %GUI%
    pause
    exit /b 1
)

echo.
echo Spectroscopy GUI
echo.
echo 1. Emulated spectrometer, power meter, and lasers
echo 2. Real instruments; auto-detect QEPro or Andor
echo 3. Real instruments; require QEPro
echo 4. Real instruments; require Andor iDus + Kymera
echo 5. Real lasers with emulated spectrometer/power meter
echo.

choice /c 12345 /n /m "Choose mode [1/2/3/4/5]: "

if errorlevel 5 goto real_lasers_only
if errorlevel 4 goto real_andor
if errorlevel 3 goto real_qepro
if errorlevel 2 goto real_auto
if errorlevel 1 goto emulated_all

:emulated_all
echo Starting emulated GUI...
"%PYTHON%" "%GUI%" --config "%CONFIG%" --emulate --laser-mode emulated
goto end

:real_auto
echo Starting real-instrument GUI with spectrometer auto-detection...
"%PYTHON%" "%GUI%" --config "%CONFIG%" --real --spectrometer-backend auto
goto end

:real_qepro
echo Starting real-instrument GUI with QEPro...
"%PYTHON%" "%GUI%" --config "%CONFIG%" --real --spectrometer-backend qepro
goto end

:real_andor
echo Starting real-instrument GUI with Andor iDus + Kymera...
"%PYTHON%" "%GUI%" --config "%CONFIG%" --real --spectrometer-backend andor
goto end

:real_lasers_only
echo Starting emulated QEPro/Newport with real OBIS lasers...
"%PYTHON%" "%GUI%" --config "%CONFIG%" --emulate --laser-mode real
goto end

:end
pause
