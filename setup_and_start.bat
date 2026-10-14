@echo off
rem ===========================================================================
rem  setup_and_start.bat
rem  Spar Enugu Emergency Alert System
rem  "Design and Implementation of a Mobile-Based Emergency Alert System using
rem   GPS - A Case Study of Spar Hypermarket, Enugu State"
rem
rem  What this script does:
rem    1. Finds a Python interpreter (re-uses ..\myenv or .venv when present -
rem       it never creates a second environment unnecessarily).
rem    2. Installs ONLY the packages that are actually missing.
rem    3. Generates the alarm siren and creates + seeds data\spar_sos.db.
rem    4. Finds a free TCP port (auto-skips a busy 8501).
rem    5. Starts `streamlit run app.py` and opens the dashboard.
rem
rem  Usage:
rem      setup_and_start.bat            -> auto-selects a free port
rem      setup_and_start.bat 8505       -> forces port 8505
rem ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
title Spar Enugu Emergency Alert System - launcher

echo ===========================================================================
echo   SPAR ENUGU EMERGENCY ALERT SYSTEM
echo   Mobile-Based Emergency Alert System using GPS
echo   (A Case Study of Spar Hypermarket, Enugu State)
echo ===========================================================================
echo.

rem ---------------------------------------------------------------------------
rem 1. Locate a Python interpreter
rem ---------------------------------------------------------------------------
set "PYEXE="

if exist "%~dp0..\myenv\Scripts\python.exe" (
    set PYEXE="%~dp0..\myenv\Scripts\python.exe"
    echo [1/6] Python found : the existing environment at ..\myenv
    goto :python_ready
)

if exist "%~dp0.venv\Scripts\python.exe" (
    set PYEXE="%~dp0.venv\Scripts\python.exe"
    echo [1/6] Python found : the project virtual environment .venv
    goto :python_ready
)

where py >nul 2>&1
if not errorlevel 1 (
    set PYEXE=py -3
    echo [1/6] Python found : the Windows Python launcher ^(py -3^)
    goto :python_ready
)

where python >nul 2>&1
if not errorlevel 1 (
    set PYEXE=python
    echo [1/6] Python found : python on PATH
    goto :python_ready
)

echo [1/6] No Python interpreter was found.
echo.
echo   Install Python 3.10 or newer from https://www.python.org/downloads/
echo   and be sure to tick "Add python.exe to PATH" during installation.
echo.
pause
exit /b 1

:python_ready
echo.

rem ---------------------------------------------------------------------------
rem 2. Install only the missing dependencies
rem ---------------------------------------------------------------------------
%PYEXE% -c "import streamlit, pandas, folium, streamlit_folium, numpy" >nul 2>&1
if errorlevel 1 goto :install_deps

echo [2/6] Dependencies    : streamlit, pandas, folium, streamlit-folium, numpy
echo                         already installed - nothing to download.
goto :deps_ready

:install_deps
echo [2/6] Some packages are missing. Installing from requirements.txt ...
echo       ^(this needs an internet connection only for this one step^)
%PYEXE% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] pip could not install the dependencies.
    echo         Check the internet connection, then run this script again.
    pause
    exit /b 1
)

:deps_ready
echo.

rem ---------------------------------------------------------------------------
rem 3. Alarm siren + database
rem ---------------------------------------------------------------------------
echo [3/6] Generating the alarm siren and seeding the database ...
%PYEXE% setup_db.py
if errorlevel 1 (
    echo.
    echo [ERROR] The database could not be prepared. See the messages above.
    pause
    exit /b 1
)
echo.

rem ---------------------------------------------------------------------------
rem 4. Choose a free TCP port (Windows often keeps 8501 busy)
rem ---------------------------------------------------------------------------
set "PORT=%~1"
if not "%PORT%"=="" goto :port_given

set /a PORT=8501
:find_port
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if errorlevel 1 goto :port_ready
echo       Port %PORT% is already in use - trying the next port ...
set /a PORT+=1
if %PORT% GTR 8510 (
    echo [ERROR] Ports 8501-8510 are all in use. Close the other program or run:
    echo         setup_and_start.bat 8520
    pause
    exit /b 1
)
goto :find_port

:port_given
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [WARNING] Port %PORT% is already in use. Streamlit may refuse to start.
    echo           Press CTRL+C now and re-run with a different port if so.
    echo.
    timeout /t 5 >nul
)

:port_ready
echo [4/6] Server port ...... %PORT%
echo.

rem ---------------------------------------------------------------------------
rem 5. Open the dashboard in the default browser
rem ---------------------------------------------------------------------------
echo [5/6] Opening http://localhost:%PORT%/?mode=dashboard in your browser ...
start "" "http://localhost:%PORT%/?mode=dashboard"
echo.

rem ---------------------------------------------------------------------------
rem 6. Run Streamlit (this window stays open - close it to stop the server)
rem ---------------------------------------------------------------------------
echo [6/6] Starting the Streamlit server ...
echo.
echo   DASHBOARD (this laptop) : http://localhost:%PORT%/?mode=dashboard
echo   SHOPPER SOS (phone)     : http://YOUR-LAN-IP:%PORT%/?mode=sos
echo                             ^(the exact IP address is printed above^)
echo.
echo   Login: spar_security / spar2026
echo   Press CTRL+C in this window to stop the server.
echo ===========================================================================
echo.

%PYEXE% -m streamlit run app.py --server.port %PORT% --server.address 0.0.0.0 --server.headless true

echo.
echo The Streamlit server has stopped.
pause
endlocal
