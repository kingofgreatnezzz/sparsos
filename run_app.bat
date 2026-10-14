@echo off
rem ===========================================================================
rem  run_app.bat - quick start for defence day.
rem
rem  Skips the setup steps (the database is assumed to exist already) and just
rem  launches Streamlit. Use this to restart the app in a few seconds if
rem  anything goes wrong on the podium.
rem
rem  Usage:  run_app.bat            -> auto-selects a free port
rem          run_app.bat 8505       -> forces port 8505
rem ===========================================================================

setlocal
cd /d "%~dp0"
title Spar Enugu Emergency Alert System - running

set "PYEXE="
if exist "%~dp0..\myenv\Scripts\python.exe" set PYEXE="%~dp0..\myenv\Scripts\python.exe"
if not defined PYEXE if exist "%~dp0.venv\Scripts\python.exe" set PYEXE="%~dp0.venv\Scripts\python.exe"
if not defined PYEXE set PYEXE=python

set "PORT=%~1"
if not "%PORT%"=="" goto :port_ready

set /a PORT=8501
:find_port
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if errorlevel 1 goto :port_ready
echo   Port %PORT% is already in use - trying the next port ...
set /a PORT+=1
if %PORT% GTR 8510 goto :port_fail
goto :find_port

:port_fail
echo [ERROR] Ports 8501-8510 are all busy. Run:  run_app.bat 8520
pause
exit /b 1

:port_ready
echo Starting the Spar Enugu Emergency Alert System on port %PORT% ...
echo   Dashboard (laptop) : http://localhost:%PORT%/?mode=dashboard
echo   Shopper SOS (phone): http://YOUR-LAN-IP:%PORT%/?mode=sos
echo   Login              : spar_security / spar2026
echo   Press CTRL+C to stop.
echo.

start "" "http://localhost:%PORT%/?mode=dashboard"
%PYEXE% -m streamlit run app.py --server.port %PORT% --server.address 0.0.0.0 --server.headless true

pause
endlocal
