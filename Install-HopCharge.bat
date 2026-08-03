@echo off
REM ============================================================
REM   HopCharge HR Dashboard - One-Click Installer (Windows)
REM   Double-click this file to install. No coding tools needed.
REM ============================================================
setlocal EnableDelayedExpansion
title HopCharge HR Dashboard - Installer

REM --- Always run from the folder this script lives in ---
cd /d "%~dp0"

echo(
echo ============================================================
echo            HopCharge HR Dashboard - Installer
echo ============================================================
echo(
echo  This will set up the dashboard on this computer.
echo  It may take a few minutes the first time. Please wait.
echo(

REM ============================================================
REM  STEP 1 - Find a usable Python (3.10-3.12 preferred)
REM ============================================================
set "PYEXE="

REM Try the py launcher first (most reliable on Windows)
for %%V in (3.12 3.11 3.10) do (
    if not defined PYEXE (
        py -%%V -c "import sys" >nul 2>&1
        if !errorlevel! == 0 set "PYEXE=py -%%V"
    )
)

REM Fall back to plain python on PATH
if not defined PYEXE (
    python --version >nul 2>&1
    if !errorlevel! == 0 set "PYEXE=python"
)

REM ============================================================
REM  STEP 2 - If no Python, install it silently (per-user)
REM ============================================================
if not defined PYEXE (
    echo  Python was not found. Installing it now ^(one time only^)...
    echo(
    set "PYINSTALLER=%TEMP%\python-hopcharge-setup.exe"
    set "PYURL=https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"

    powershell -NoProfile -Command ^
      "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '!PYURL!' -OutFile '!PYINSTALLER!' -UseBasicParsing } catch { exit 1 }"

    if not exist "!PYINSTALLER!" (
        echo(
        echo  [ERROR] Could not download Python automatically.
        echo  Please install Python 3.12 manually from python.org,
        echo  tick "Add Python to PATH", then run this installer again.
        echo(
        pause
        exit /b 1
    )

    REM Per-user silent install, adds to PATH, includes py launcher
    "!PYINSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1
    del "!PYINSTALLER!" >nul 2>&1

    REM Re-detect after install
    for %%V in (3.12 3.11 3.10) do (
        if not defined PYEXE (
            py -%%V -c "import sys" >nul 2>&1
            if !errorlevel! == 0 set "PYEXE=py -%%V"
        )
    )
    if not defined PYEXE (
        python --version >nul 2>&1
        if !errorlevel! == 0 set "PYEXE=python"
    )
)

if not defined PYEXE (
    echo(
    echo  [ERROR] Python is still not available. A restart may be needed.
    echo  Please restart the computer and run this installer again.
    echo(
    pause
    exit /b 1
)

echo  Using Python: !PYEXE!
echo(

REM ============================================================
REM  STEP 3 - Create a private virtual environment
REM ============================================================
echo  Creating a private environment for the app...
!PYEXE! -m venv "%~dp0.venv"
if !errorlevel! neq 0 (
    echo  [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

echo  Upgrading installer tools...
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel >nul 2>&1

REM ============================================================
REM  STEP 4 - Install the app's dependencies
REM ============================================================
echo  Installing dependencies (this is the slow part)...
echo(
if exist "%~dp0requirements.txt" (
    "%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
) else (
    echo  [WARN] requirements.txt not found - installing core packages only.
    "%VENV_PY%" -m pip install fastapi "uvicorn[standard]" python-multipart
)
if !errorlevel! neq 0 (
    echo(
    echo  [ERROR] Some dependencies failed to install. See messages above.
    pause
    exit /b 1
)

REM Download the spaCy model used by the resume parser (best-effort)
echo(
echo  Setting up the resume language model...
"%VENV_PY%" -m spacy download en_core_web_sm >nul 2>&1

REM ============================================================
REM  STEP 5 - Prepare the user data folder
REM ============================================================
set "DATAHOME=%USERPROFILE%\HopchargeHR"
if not exist "%DATAHOME%" mkdir "%DATAHOME%"
if not exist "%DATAHOME%\credentials" mkdir "%DATAHOME%\credentials"
if not exist "%DATAHOME%\output" mkdir "%DATAHOME%\output"
if not exist "%DATAHOME%\input_resumes" mkdir "%DATAHOME%\input_resumes"

REM ============================================================
REM  STEP 6 - Write the launch script (what the shortcut runs)
REM ============================================================
set "RUNVBS=%~dp0Run-HopCharge.vbs"
>  "%RUNVBS%" echo ' Auto-generated launcher. Starts the dashboard with no console window.
>> "%RUNVBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%RUNVBS%" echo appDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
>> "%RUNVBS%" echo sh.CurrentDirectory = appDir
>> "%RUNVBS%" echo cmd = """" ^& appDir ^& ".venv\Scripts\pythonw.exe"" """ ^& appDir ^& "launcher_win.py"""
>> "%RUNVBS%" echo sh.Run cmd, 0, False

REM ============================================================
REM  STEP 7 - Create Start Menu + Desktop shortcuts
REM ============================================================
echo  Creating Start Menu and Desktop shortcuts...

set "ICON=%~dp0appicon.ico"
set "SHORTCUT_NAME=HopCharge HR Dashboard.lnk"
set "STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
set "DESKTOP=%USERPROFILE%\Desktop"

for %%T in ("%STARTMENU%\%SHORTCUT_NAME%" "%DESKTOP%\%SHORTCUT_NAME%") do (
    powershell -NoProfile -Command ^
      "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%%~T');" ^
      "$s.TargetPath='%SystemRoot%\System32\wscript.exe';" ^
      "$s.Arguments='\"%RUNVBS%\"';" ^
      "$s.WorkingDirectory='%~dp0';" ^
      "if (Test-Path '%ICON%') { $s.IconLocation='%ICON%' };" ^
      "$s.Description='HopCharge HR BAU Automation Dashboard';" ^
      "$s.Save()"
)

echo(
echo ============================================================
echo                    INSTALLATION COMPLETE
echo ============================================================
echo(
echo  A shortcut named "HopCharge HR Dashboard" is now on the
echo  Desktop and in the Start Menu. Double-click it to open.
echo(
echo  IMPORTANT - before first use, place these files in:
echo     %DATAHOME%
echo        - neon.env                ^(your database connection^)
echo     %DATAHOME%\credentials
echo        - your Google service-account .json files
echo(
echo  The dashboard opens in your web browser automatically.
echo  To stop it, close the small app window that appears.
echo(
pause
endlocal
