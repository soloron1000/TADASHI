@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PYEXE="
for /r "%~dp0language/python" %%P in (python.exe) do (
    if not defined PYEXE set "PYEXE=%%P"
)

if not defined PYEXE (
    echo.
    echo TADASHI could not find a WinPython runtime.
    echo Put your extracted WinPython folder here:
    echo.
    echo   TADASHI\WinPython\...
    echo.
    pause
    exit /b 1
)

"%PYEXE%" "%~dp0tadashi_vf.py"
if errorlevel 1 pause
