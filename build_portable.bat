@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "VENV=.venv-build"
set "DIST_DIR=dist\BathymetryMVP"
set "BUILD_DIR=build"
set "RELEASE_DIR=portable_release"
set "ARCHIVE=%RELEASE_DIR%\BathymetryMVP_Portable.zip"
set "CHECKSUM=%ARCHIVE%.sha256.txt"
echo.
echo Bathymetry MVP portable build
echo Project directory: %CD%
echo.
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.11 -m venv "%VENV%"
    if errorlevel 1 goto :error
)
call "%VENV%\Scripts\activate.bat"
if errorlevel 1 goto :error
echo Upgrading build tools...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :error
echo Installing project dependencies...
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error
echo Running syntax check...
python -m compileall -q main.py bathymetry ui tests
if errorlevel 1 goto :error
echo Running tests...
python -m pytest -q
if errorlevel 1 goto :error
echo Cleaning previous build directories...
if exist "%BUILD_DIR%" (
    rmdir /s /q "%BUILD_DIR%"
    if errorlevel 1 goto :error
)
if exist "dist" (
    rmdir /s /q "dist"
    if errorlevel 1 goto :error
)
if exist "%RELEASE_DIR%" (
    rmdir /s /q "%RELEASE_DIR%"
    if errorlevel 1 goto :error
)
mkdir "%RELEASE_DIR%"
if errorlevel 1 goto :error
echo Building PyInstaller distribution...
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    BathymetryMVP.spec
if errorlevel 1 goto :error
if not exist "%DIST_DIR%\BathymetryMVP.exe" (
    echo ERROR: BathymetryMVP.exe was not created.
    goto :error
)
if not exist "README_PORTABLE.txt" (
    echo ERROR: README_PORTABLE.txt was not found.
    goto :error
)
if not exist "sample_data" (
    echo ERROR: sample_data directory was not found.
    goto :error
)
echo Copying sample data...
xcopy /e /i /y ^
    "sample_data" ^
    "%DIST_DIR%\sample_data" > nul
if errorlevel 1 goto :error
echo Copying README...
copy /y ^
    "README_PORTABLE.txt" ^
    "%DIST_DIR%\README_PORTABLE.txt" > nul
if errorlevel 1 goto :error
echo Checking bundled PROJ and GDAL resources...
powershell -NoProfile -ExecutionPolicy Bypass ^
    -Command "$projDb = Get-ChildItem -Path 'dist\BathymetryMVP' -Recurse -Filter 'proj.db' -ErrorAction SilentlyContinue; if (-not $projDb) { Write-Error 'proj.db was not found in the portable distribution'; exit 1 }; $gdalData = Get-ChildItem -Path 'dist\BathymetryMVP' -Recurse -Filter 'gdalvrt.xsd' -ErrorAction SilentlyContinue; if (-not $gdalData) { Write-Error 'gdalvrt.xsd was not found in the portable distribution'; exit 1 }; Write-Host 'PROJ and GDAL data found.'"
if errorlevel 1 goto :error
echo Creating portable ZIP archive...
powershell -NoProfile -ExecutionPolicy Bypass ^
    -Command "Compress-Archive -Path 'dist\BathymetryMVP' -DestinationPath '%ARCHIVE%' -Force"
if errorlevel 1 goto :error
if not exist "%ARCHIVE%" (
    echo ERROR: Portable archive was not created.
    goto :error
)
echo Creating SHA256 checksum...
powershell -NoProfile -ExecutionPolicy Bypass ^
    -Command "$hash = (Get-FileHash -LiteralPath '%ARCHIVE%' -Algorithm SHA256).Hash; $fileName = [System.IO.Path]::GetFileName('%ARCHIVE%'); Set-Content -LiteralPath '%CHECKSUM%' -Value ($hash + '  ' + $fileName) -Encoding ASCII"
if errorlevel 1 goto :error
if not exist "%CHECKSUM%" (
    echo ERROR: SHA256 checksum file was not created.
    goto :error
)
echo Verifying SHA256 checksum...
powershell -NoProfile -ExecutionPolicy Bypass ^
    -Command "$checksumLine = Get-Content -LiteralPath '%CHECKSUM%' | Select-Object -First 1; $expectedHash = $checksumLine.Trim().Split(' ')[0].ToUpperInvariant(); $actualHash = (Get-FileHash -LiteralPath '%ARCHIVE%' -Algorithm SHA256).Hash.ToUpperInvariant(); if ($expectedHash -ne $actualHash) { Write-Error 'SHA256 checksum verification failed'; exit 1 }; Write-Host 'SHA256 checksum is valid.'"
if errorlevel 1 goto :error
echo Creating latest archive copy...
copy /y ^
    "%ARCHIVE%" ^
    "%RELEASE_DIR%\BathymetryMVP_Portable_latest.zip" > nul
if errorlevel 1 goto :error
echo.
echo Build completed successfully.
echo Portable directory: %DIST_DIR%
echo Archive: %ARCHIVE%
echo SHA256: %CHECKSUM%
echo.
if /i not "%CI%"=="true" pause
exit /b 0
:error
echo.
echo Build failed.
echo.
if /i not "%CI%"=="true" pause
exit /b 1