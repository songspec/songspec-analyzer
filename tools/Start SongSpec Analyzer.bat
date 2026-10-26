@echo off
rem SongSpec Analyzer - double-click to start. First run installs what it needs into this folder, then the test page opens.
setlocal
cd /d "%~dp0"
title SongSpec Analyzer

rem 1. the environment from an earlier run
if exist ".venv\.songspec-installed" if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\launch.py
    goto :end
)

rem 2. a Python 3.12 to create it with (always resolved to the full path of python.exe)
set "PY="
if defined SONGSPEC_PYTHON set "PY=%SONGSPEC_PYTHON%"
if not defined PY (
    for /f "delims=" %%i in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%i"
)
if not defined PY (
    for /f "delims=" %%i in ('uv python find 3.12 2^>nul') do set "PY=%%i"
)
if not defined PY if exist "%USERPROFILE%\.local\bin\uv.exe" (
    for /f "delims=" %%i in ('"%USERPROFILE%\.local\bin\uv.exe" python find 3.12 2^>nul') do set "PY=%%i"
)
if not defined PY (
    echo.
    echo  SongSpec Analyzer needs Python 3.12, and it is not installed.
    echo  It can install a private copy now with "uv" ^(by Astral, https://astral.sh/uv, about 50 MB, no admin rights^).
    echo  Or close this window, install Python 3.12 from https://www.python.org and start again.
    echo.
    choice /c YN /m "Install Python 3.12 with uv now"
    if errorlevel 2 goto :end
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    "%USERPROFILE%\.local\bin\uv.exe" python install 3.12
    for /f "delims=" %%i in ('"%USERPROFILE%\.local\bin\uv.exe" python find 3.12 2^>nul') do set "PY=%%i"
)
if not defined PY (
    echo Could not find or install Python 3.12.
    pause
    goto :end
)
"%PY%" tools\launch.py

:end
endlocal
