@echo off
setlocal
title Aira - Stop Server
cd /d "%~dp0"
echo Stopping Aira (ports 8000-8010)...
set FOUND=0
for /l %%p in (8000,1,8010) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /c:":%%p " ^| findstr /c:"LISTENING"') do (
        taskkill /f /pid %%a >nul 2>&1 && set FOUND=1
    )
)
if "%FOUND%"=="1" (echo Aira stopped.) else (echo No running Aira server found.)
pause
