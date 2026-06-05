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
echo 2. Real instruments using config/lab_defaults.json
echo 3. Real lasers with emulated spectrometer/power meter
echo.

choice /c 123 /n /m "Choose mode [1/2/3]: "

if errorlevel 3 goto real_lasers_only
if errorlevel 2 goto real_all
if errorlevel 1 goto emulated_all

:emulated_all
echo Starting emulated GUI...
"%PYTHON%" "%GUI%" --config "%CONFIG%" --emulate --laser-mode emulated
goto end

:real_all
echo Starting real-instrument GUI...
"%PYTHON%" "%GUI%" --config "%CONFIG%" --real
goto end

:real_lasers_only
echo Starting emulated QEPro/Newport with real OBIS lasers...
"%PYTHON%" "%GUI%" --config "%CONFIG%" --emulate --laser-mode real
goto end

:end
pause
