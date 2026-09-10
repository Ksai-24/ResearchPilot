@echo off
setlocal enabledelayedexpansion
title Aira - AI Research Assistant
cd /d "%~dp0"

echo.
echo  ==============================================
echo    AIRA  -  AI Research and Debug Assistant
echo  ==============================================
echo.

REM ---- 1/4: virtual environment ----
if not exist ".venv\Scripts\python.exe" (
    echo  [1/4] First run: creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo  ERROR: could not create the virtual environment.
        echo  Install Python from https://python.org then run this file again.
        pause
        exit /b 1
    )
) else (
    echo  [1/4] Environment OK
)

REM ---- 2/4: API key ----
if not exist ".env" (
    if exist ".env.example" copy /y ".env.example" ".env" >nul
)
findstr /b /c:"AIRA_API_KEY=sk-" ".env" >nul 2>&1
if errorlevel 1 (
    echo  [2/4] WARNING: no API key found in .env
) else (
    echo  [2/4] API key OK
)

REM ---- 3/4: find a free port (8000-8010) ----
set PORT=8000
:findport
netstat -ano 2>nul | findstr /c:":%PORT% " | findstr /c:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    set /a PORT+=1
    if !PORT! gtr 8010 (
        echo.
        echo  ERROR: ports 8000-8010 are all in use.
        echo  Close other applications and run this file again.
        pause
        exit /b 1
    )
    goto findport
)
echo  [3/4] Port %PORT% is free

REM ---- 4/4: start server, wait until healthy ----
echo  [4/4] Starting server...
start "" /b cmd /c ".venv\Scripts\python.exe -m uvicorn aira.server:app --host 127.0.0.1 --port %PORT% >aira-server.log 2>&1"

set /a tries=0
:waitloop
%SystemRoot%\System32\timeout.exe /t 1 /nobreak >nul 2>&1
curl -s -o nul http://127.0.0.1:!PORT!/api/health >nul 2>&1
if not errorlevel 1 goto ready
set /a tries+=1
if !tries! lss 20 goto waitloop
echo.
echo  ERROR: server did not start within 20 seconds.
echo  ---- aira-server.log ----
type aira-server.log
pause
exit /b 1

:ready
cls
color 0b
echo.
echo  ==============================================
echo.
echo          AIRA IS RUNNING
echo.
echo    Link  :  http://localhost:%PORT%
echo.
echo    Your browser opens automatically.
echo    Keep this window open while using Aira.
echo    Press any key (or close this window) to STOP.
echo.
echo  ==============================================
echo.
start "" http://localhost:%PORT%
pause >nul

REM ---- user pressed a key: stop the server ----
echo  Stopping Aira...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /c:":%PORT% " ^| findstr /c:"LISTENING"') do taskkill /f /pid %%a >nul 2>&1
echo  Aira stopped. You can close this window.
%SystemRoot%\System32\timeout.exe /t 3 >nul 2>&1
