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
    echo [1/10] Creating virtual environment...
    py -3.11 -m venv "%VENV%"
    if errorlevel 1 goto :error
)

echo [2/10] Activating build environment...
call "%VENV%\Scripts\activate.bat"
if errorlevel 1 goto :error

python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :error
python -m pip install --no-cache-dir -r requirements-build.txt
if errorlevel 1 goto :error
python -m pip check
if errorlevel 1 goto :error

echo [3/10] Checking Python sources...
python -m compileall -q main.py bathymetry ui tests
if errorlevel 1 goto :error

echo [4/10] Running tests...
python -m pytest -q
if errorlevel 1 goto :error

echo [5/10] Cleaning previous build outputs...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_ROOT%" rmdir /s /q "%DIST_ROOT%"
if exist "%RELEASE_DIR%" rmdir /s /q "%RELEASE_DIR%"
mkdir "%RELEASE_DIR%"
if errorlevel 1 goto :error

echo [6/10] Building PyInstaller distribution...
python -m PyInstaller --noconfirm --clean "%SPEC_FILE%"
if errorlevel 1 goto :error

if not exist "%APP_DIR%\Navimetry.exe" (
    echo ERROR: Navimetry.exe was not created.
    goto :error
)

if exist "README_PORTABLE.txt" copy /y "README_PORTABLE.txt" "%APP_DIR%\README_PORTABLE.txt" > nul
if exist "sample_data" xcopy /e /i /y "sample_data" "%APP_DIR%\sample_data" > nul

echo [7/10] Inspecting bundled geospatial resources...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$proj = Get-ChildItem -LiteralPath 'dist\Navimetry' -Recurse -Filter 'proj.db' -ErrorAction SilentlyContinue | Select-Object -First 1; if ($proj) { Write-Host ('PROJ database: ' + $proj.FullName) } else { Write-Warning 'proj.db was not found by static scan; frozen self-test will decide runtime validity.' }; $gdal = Get-ChildItem -LiteralPath 'dist\Navimetry' -Recurse -Filter 'gdalvrt.xsd' -ErrorAction SilentlyContinue | Select-Object -First 1; if ($gdal) { Write-Host ('GDAL data: ' + $gdal.FullName) } else { Write-Warning 'gdalvrt.xsd was not found by static scan; frozen self-test will decide runtime validity.' }"
if errorlevel 1 goto :error

echo [8/10] Running frozen executable self-test...
if exist "%APP_DIR%\navimetry_self_test.txt" del /q "%APP_DIR%\navimetry_self_test.txt"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:QT_QPA_PLATFORM='offscreen'; $p=Start-Process -FilePath 'dist\Navimetry\Navimetry.exe' -ArgumentList '--self-test' -Wait -PassThru -WindowStyle Hidden; Write-Host ('Frozen self-test exit code: ' + $p.ExitCode); if (Test-Path -LiteralPath 'dist\Navimetry\navimetry_self_test.txt') { Get-Content -LiteralPath 'dist\Navimetry\navimetry_self_test.txt' }; exit $p.ExitCode"
if errorlevel 1 goto :error
if not exist "%APP_DIR%\navimetry_self_test.txt" (
    echo ERROR: Frozen self-test did not create its report file.
    goto :error
)
findstr /c:"Navimetry self-test passed" "%APP_DIR%\navimetry_self_test.txt" > nul
if errorlevel 1 goto :error

echo [9/10] Creating build manifest...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Resolve-Path 'dist\Navimetry').Path; $files=Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object { [PSCustomObject]@{ path=$_.FullName.Substring($root.Length+1); size_bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } }; [PSCustomObject]@{application='Navimetry'; version='0.2'; platform='Windows x64'; generated_at_utc=(Get-Date).ToUniversalTime().ToString('o'); files=$files} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath 'dist\Navimetry\build_manifest.json' -Encoding UTF8"
if errorlevel 1 goto :error

if not exist "%APP_DIR%\build_manifest.json" (
    echo ERROR: build_manifest.json was not created.
    goto :error
)

echo [10/10] Creating portable ZIP and checksum...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -LiteralPath 'dist\Navimetry' -DestinationPath '%ARCHIVE%' -Force"
if errorlevel 1 goto :error
if not exist "%ARCHIVE%" ( echo ERROR: Portable ZIP was not created. & goto :error )

powershell -NoProfile -ExecutionPolicy Bypass -Command "$hash=(Get-FileHash -LiteralPath '%ARCHIVE%' -Algorithm SHA256).Hash; $name=[IO.Path]::GetFileName('%ARCHIVE%'); Set-Content -LiteralPath '%CHECKSUM%' -Value ($hash+'  '+$name) -Encoding ASCII"
if errorlevel 1 goto :error
if not exist "%CHECKSUM%" ( echo ERROR: SHA256 file was not created. & goto :error )

powershell -NoProfile -ExecutionPolicy Bypass -Command "$line=Get-Content -LiteralPath '%CHECKSUM%' | Select-Object -First 1; $expected=($line -split '\s+')[0].ToUpperInvariant(); $actual=(Get-FileHash -LiteralPath '%ARCHIVE%' -Algorithm SHA256).Hash.ToUpperInvariant(); if ($expected -ne $actual) { Write-Error 'SHA256 verification failed'; exit 1 }; Write-Host 'SHA256 verification passed.'"
if errorlevel 1 goto :error

copy /y "%ARCHIVE%" "%RELEASE_DIR%\Navimetry_Windows_x64_Portable_latest.zip" > nul
if errorlevel 1 goto :error

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
if exist "%APP_DIR%\navimetry_self_test.txt" (
    echo Frozen self-test report:
    type "%APP_DIR%\navimetry_self_test.txt"
)
if /i not "%CI%"=="true" pause
exit /b 1
