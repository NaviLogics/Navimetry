@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "VENV=.venv-build"
set "DIST_DIR=dist\BathymetryMVP"
set "BUILD_DIR=build"
set "RELEASE_DIR=portable_release"
set "ARCHIVE=%RELEASE_DIR%\BathymetryMVP_Portable.zip"
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.11 -m venv "%VENV%"
    if errorlevel 1 goto :error
)
call "%VENV%\Scripts\activate.bat"
if errorlevel 1 goto :error
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :error
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error
echo Running syntax check...
python -m compileall main.py bathymetry ui
if errorlevel 1 goto :error
echo Running tests...
python -m pytest -q
if errorlevel 1 goto :error
if exist "%BUILD_DIR%" (
    rmdir /s /q "%BUILD_DIR%"
)
if exist "dist" (
    rmdir /s /q "dist"
)
if exist "%RELEASE_DIR%" (
    rmdir /s /q "%RELEASE_DIR%"
)
mkdir "%RELEASE_DIR%"
if errorlevel 1 goto :error
python -m PyInstaller --noconfirm --clean BathymetryMVP.spec
if errorlevel 1 goto :error
if not exist "%DIST_DIR%\BathymetryMVP.exe" (
    echo ERROR: executable was not created.
    goto :error
)
if not exist "README_PORTABLE.txt" (
    echo ERROR: README_PORTABLE.txt was not found.
    goto :error
)
xcopy /e /i /y ^
    "sample_data" ^
    "%DIST_DIR%\sample_data" > nul
copy /y ^
    "README_PORTABLE.txt" ^
    "%DIST_DIR%\README_PORTABLE.txt" > nul
powershell -NoProfile -ExecutionPolicy Bypass ^
    -Command "Compress-Archive -Path 'dist\BathymetryMVP' -DestinationPath '%ARCHIVE%' -Force"
if errorlevel 1 goto :error
echo.
echo Build completed successfully.
echo Portable directory: %DIST_DIR%
echo Archive: %ARCHIVE%
echo SHA256: %ARCHIVE%.sha256.txt
if /i not "%CI%"=="true" pause
exit /b 0
:error
echo.
echo Build failed.
if /i not "%CI%"=="true" pause
exit /b 1