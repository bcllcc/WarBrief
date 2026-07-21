@echo off
setlocal
cd /d "%~dp0"
call "%~dp0Start-WarBrief-Windows.bat" -Demo
endlocal
