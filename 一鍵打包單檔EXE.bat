@echo off
setlocal EnableExtensions

pushd "%~dp0"
if errorlevel 1 (
    echo ERROR: Cannot open the project directory.
    pause
    exit /b 1
)

set "PY_EXE=%CD%\portable_python\python.exe"
if not exist "%PY_EXE%" (
    echo ERROR: portable_python\python.exe was not found.
    echo Keep this BAT file in the extracted project folder.
    popd
    pause
    exit /b 1
)

if not exist "algorithm_config.json" (
    echo ERROR: build-time algorithm_config.json was not found.
    echo It is embedded into the EXE as the factory default.
    popd
    pause
    exit /b 1
)

echo Checking build dependencies...
"%PY_EXE%" -c "import PyInstaller,numpy,cv2,xlsxwriter"
if errorlevel 1 (
    echo ERROR: Missing PyInstaller, NumPy, OpenCV, or XlsxWriter.
    echo Run the dependency installation BAT first.
    popd
    pause
    exit /b 1
)

echo Validating build-time algorithm_config.json...
"%PY_EXE%" -c "import sys;sys.path.insert(0,r'app\backend');from config_loader import load_config;load_config();print('Config OK')"
if errorlevel 1 (
    echo ERROR: algorithm_config.json is invalid.
    popd
    pause
    exit /b 1
)

echo Building truly portable one-file EXE...
"%PY_EXE%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --console ^
    --noupx ^
    --name "XrayRegistration_v31" ^
    --paths "app\backend" ^
    --add-data "algorithm_config.json;." ^
    --add-data "app\frontend;app\frontend" ^
    --add-data "app\assets;app\assets" ^
    --collect-all cv2 ^
    --collect-all xlsxwriter ^
    --hidden-import numpy ^
    --hidden-import server ^
    "app\backend\launcher.py"

if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    popd
    pause
    exit /b 1
)

echo.
echo Done: dist\XrayRegistration_v31.exe
echo No JSON file is required beside the EXE.
echo First run creates D:\XrayRegistrationData\algorithm_config.json from the embedded build-time default.
echo Later runs reuse the existing JSON. Edit it and restart the EXE to apply changes.
popd
pause
exit /b 0
