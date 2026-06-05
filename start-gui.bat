:: Name:    start-gui.bat
:: Purpose: Starts the spectroscopy GUI with WinPython

@echo off

title Spectroscopy GUI
color 0B
echo %date%
:: echo %time%

setlocal enableextensions
set me=%~n0
set parent=%~dp0

:: echo Starting the GUI...

set "target_file=%~dp0..\WinPython\python\python.exe"

:: Provide a Y/N choice, defaulting to Y after 5 seconds
choice /c YN /N /T 5 /D Y /M "Emulate spectrometer, power meter, and lasers? [Y/N]"
goto:sub_%ERRORLEVEL%

:sub_1
echo Starting the GUI with emulation...
%target_file% %~dp0gui.py --emulate --laser-mode emulated
goto :end

:sub_2
echo Starting the GUI with real instruments...
%target_file% %~dp0gui.py --real --obis-ports COM3 COM5
goto :end

:end
pause

:: echo %target_file%
