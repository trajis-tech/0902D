@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set PYTHONUNBUFFERED=1

set "PY_EXE="
if exist "%CD%\portable_python\python.exe" set "PY_EXE=%CD%\portable_python\python.exe"
if not defined PY_EXE if exist "%CD%\portable_python\Scripts\python.exe" set "PY_EXE=%CD%\portable_python\Scripts\python.exe"
set "SERVER=%CD%\app\backend\launcher.py"

if defined PY_EXE (
    echo Using portable Python: %PY_EXE%
    "%PY_EXE%" "%SERVER%"
    set "ERR=%ERRORLEVEL%"
) else (
    echo portable_python\python.exe not found.
    echo Trying system python...
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: No portable Python and no system python in PATH.
        echo.
        echo Please install Python 3 with numpy, opencv-python-headless, XlsxWriter
        echo or place a portable runtime at portable_python\python.exe
        echo.
        pause
        exit /b 1
    )
    python "%SERVER%"
    set "ERR=%ERRORLEVEL%"
)

if not "%ERR%"=="0" (
    echo.
    echo ERROR: server exited with code %ERR%.
    echo Required runtime packages: numpy opencv-python-headless XlsxWriter
    echo.
)

pause
exit /b %ERR%
