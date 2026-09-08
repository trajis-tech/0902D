@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "HERE=%~dp0"
set "PY_EXE="
set "WHEELDIR="
set "TAR=%SystemRoot%\System32\tar.exe"
set "N=0"
set "FAIL=0"

if exist "%HERE%portable_python\python.exe" set "PY_EXE=%HERE%portable_python\python.exe"
if not defined PY_EXE if exist "%HERE%..\portable_python\python.exe" set "PY_EXE=%HERE%..\portable_python\python.exe"
if not defined PY_EXE if exist "%HERE%portable_python\Scripts\python.exe" set "PY_EXE=%HERE%portable_python\Scripts\python.exe"

if exist "%HERE%vendor\wheels\*.whl" set "WHEELDIR=%HERE%vendor\wheels"
if not defined WHEELDIR if exist "%HERE%wheels\*.whl" set "WHEELDIR=%HERE%wheels"
if not defined WHEELDIR if exist "%HERE%..\vendor\wheels\*.whl" set "WHEELDIR=%HERE%..\vendor\wheels"
if not defined WHEELDIR if exist "%HERE%..\wheels\*.whl" set "WHEELDIR=%HERE%..\wheels"

if not defined PY_EXE (
    echo ERROR: portable_python\python.exe not found.
    echo Extract python-3.11.9-embed-amd64.zip into portable_python\
    echo so this file exists: portable_python\python.exe
    pause
    exit /b 1
)

for %%I in ("%PY_EXE%") do set "PY_DIR=%%~dpI"
set "SITE=%PY_DIR%Lib\site-packages"

echo Using: %PY_EXE%
echo Site:  %SITE%
echo.

if not exist "%SITE%" mkdir "%SITE%"

set "PTH="
for %%F in ("%PY_DIR%python*._pth") do if exist "%%~fF" set "PTH=%%~fF"
if not defined PTH set "PTH=%PY_DIR%python311._pth"

set "ZIPNAME=python311.zip"
for %%F in ("%PY_DIR%python3*.zip") do if exist "%%~fF" set "ZIPNAME=%%~nxF"

echo Updating _pth: %PTH%
> "%PTH%" (
    echo %ZIPNAME%
    echo/.
    echo Lib\site-packages
    echo import site
)
echo ----- %PTH% -----
type "%PTH%"
echo -------------------
echo.

if not defined WHEELDIR (
    if not exist "%HERE%vendor\wheels" mkdir "%HERE%vendor\wheels"
    echo ERROR: no .whl files found.
    echo Put downloaded wheels here:
    echo   %HERE%vendor\wheels
    echo.
    echo Need Windows 64-bit files, for example:
    echo   numpy-[version]-cp311-cp311-win_amd64.whl
    echo   opencv_python_headless-[version]-cp37-abi3-win_amd64.whl
    echo   XlsxWriter-[version]-py3-none-any.whl
    echo   pyinstaller-[version]-cp311-cp311-win_amd64.whl
    echo   altgraph / packaging / pefile / pyinstaller_hooks_contrib / pywin32_ctypes wheels
    echo.
    echo _pth was still updated. Drop the .whl files and run this script again.
    pause
    exit /b 1
)

echo Wheels: %WHEELDIR%
echo.

for %%F in ("%WHEELDIR%\*.whl") do (
    if exist "%%~fF" (
        echo Extracting: %%~nxF
        if exist "%TAR%" (
            "%TAR%" -xf "%%~fF" -C "%SITE%"
        ) else (
            copy /y "%%~fF" "%TEMP%\xray_wheel.zip" >nul
            powershell -NoProfile -Command "Expand-Archive -LiteralPath ($env:TEMP + '\xray_wheel.zip') -DestinationPath '%SITE:\=\\%' -Force"
        )
        if errorlevel 1 (
            echo ERROR: failed %%~nxF
            set "FAIL=1"
        ) else (
            set /a N+=1
        )
        echo.
    )
)

if exist "%TEMP%\xray_wheel.zip" del /q "%TEMP%\xray_wheel.zip" >nul 2>nul

if !N! equ 0 (
    echo ERROR: nothing extracted.
    pause
    exit /b 1
)

echo Extracted !N! wheel(s) into:
echo   %SITE%
echo.

echo Import check:
"%PY_EXE%" -c "import numpy,cv2,xlsxwriter,PyInstaller; print('numpy', numpy.__version__); print('cv2', cv2.__version__); print('xlsxwriter', xlsxwriter.__version__); print('PyInstaller', PyInstaller.__version__)"
if errorlevel 1 (
    echo ERROR: extract done but import failed.
    echo Check wheel tags: need cp311 or abi3, and win_amd64.
    pause
    exit /b 1
)

if "!FAIL!"=="1" (
    echo WARNING: at least one wheel failed.
    pause
    exit /b 1
)

echo.
echo Done.
pause
exit /b 0
