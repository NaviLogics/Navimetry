@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "VENV=.venv-build"
set "SPEC_FILE=Navimetry.spec"
set "DIST_ROOT=dist"
set "APP_DIR=%DIST_ROOT%\Navimetry"
set "BUILD_DIR=build"
set "RELEASE_DIR=portable_release"
set "ARCHIVE=%RELEASE_DIR%\Navimetry_Windows_x64_Portable.zip"
set "CHECKSUM=%ARCHIVE%.sha256.txt"

echo.
echo ==========================================
echo Navimetry 0.2 portable build
echo ==========================================
echo Project directory: %CD%
echo.

if not exist "%SPEC_FILE%" ( echo ERROR: %SPEC_FILE% was not found. & goto :error )
if not exist "requirements-build.txt" ( echo ERROR: requirements-build.txt was not found. & goto :error )
if not exist "main.py" ( echo ERROR: main.py was not found. & goto :error )

if not exist "%VENV%\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.11 -m venv "%VENV%"
    if errorlevel 1 goto :error
)
call "%VENV%\Scripts\activate.bat"
if errorlevel 1 goto :error
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :error
python -m pip install --no-cache-dir -r requirements-build.txt
if errorlevel 1 goto :error
python -m pip check
if errorlevel 1 goto :error
python -m compileall -q main.py bathymetry ui tests
if errorlevel 1 goto :error
python -m pytest -q
if errorlevel 1 goto :error
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_ROOT%" rmdir /s /q "%DIST_ROOT%"
if exist "%RELEASE_DIR%" rmdir /s /q "%RELEASE_DIR%"
mkdir "%RELEASE_DIR%"
if errorlevel 1 goto :error
python -m PyInstaller --noconfirm --clean "%SPEC_FILE%"
if errorlevel 1 goto :error
if not exist "%APP_DIR%\Navimetry.exe" ( echo ERROR: Navimetry.exe was not created. & goto :error )
if exist "README_PORTABLE.txt" copy /y "README_PORTABLE.txt" "%APP_DIR%\README_PORTABLE.txt" > nul
if exist "sample_data" xcopy /e /i /y "sample_data" "%APP_DIR%\sample_data" > nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "$projDb = Get-ChildItem -LiteralPath 'dist\Navimetry' -Recurse -Filter 'proj.db' -ErrorAction SilentlyContinue; if (-not $projDb) { Write-Error 'proj.db was not found'; exit 1 }; $gdalData = Get-ChildItem -LiteralPath 'dist\Navimetry' -Recurse -Filter 'gdalvrt.xsd' -ErrorAction SilentlyContinue; if (-not $gdalData) { Write-Error 'gdalvrt.xsd was not found'; exit 1 }"
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Location 'dist\Navimetry'; $env:QT_QPA_PLATFORM='offscreen'; $p=Start-Process -FilePath '.\Navimetry.exe' -ArgumentList '--self-test' -Wait -PassThru -WindowStyle Hidden; if ($p.ExitCode -ne 0) { exit $p.ExitCode }"
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Resolve-Path 'dist\Navimetry').Path; $files=Get-ChildItem -LiteralPath $root -Recurse -File | %% { [PSCustomObject]@{ path=$_.FullName.Substring($root.Length+1); size_bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } }; [PSCustomObject]@{application='Navimetry'; version='0.2'; platform='Windows x64'; generated_at_utc=(Get-Date).ToUniversalTime().ToString('o'); files=$files} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath 'dist\Navimetry\build_manifest.json' -Encoding UTF8"
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -LiteralPath 'dist\Navimetry' -DestinationPath '%ARCHIVE%' -Force"
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -Command "$hash=(Get-FileHash -LiteralPath '%ARCHIVE%' -Algorithm SHA256).Hash; $name=[IO.Path]::GetFileName('%ARCHIVE%'); Set-Content -LiteralPath '%CHECKSUM%' -Value ($hash+'  '+$name) -Encoding ASCII"
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -Command "$line=Get-Content -LiteralPath '%CHECKSUM%' | Select-Object -First 1; $expected=($line -split '\s+')[0].ToUpperInvariant(); $actual=(Get-FileHash -LiteralPath '%ARCHIVE%' -Algorithm SHA256).Hash.ToUpperInvariant(); if ($expected -ne $actual) { exit 1 }"
if errorlevel 1 goto :error
copy /y "%ARCHIVE%" "%RELEASE_DIR%\Navimetry_Windows_x64_Portable_latest.zip" > nul
echo.
echo ==========================================
echo Build completed successfully
echo ==========================================
echo Application: %APP_DIR%\Navimetry.exe
echo Archive: %ARCHIVE%
echo SHA256: %CHECKSUM%
echo Manifest: %APP_DIR%\build_manifest.json
if /i not "%CI%"=="true" pause
exit /b 0
:error
echo.
echo ==========================================
echo Build failed
echo ==========================================
if /i not "%CI%"=="true" pause
exit /b 1
