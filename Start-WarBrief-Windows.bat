@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_windows.ps1" %*
if errorlevel 1 (
  echo.
  echo WarBrief 启动失败。请查看上方错误信息。
  pause
)
endlocal
