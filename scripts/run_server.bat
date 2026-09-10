@echo off
REM ============================================================
REM  QMS production launcher (multi-worker)
REM  Usage: double-click, or add to Windows startup folder
REM  Put this file in the project root next to main.py
REM ============================================================
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
echo Starting QMS (production mode, 4 workers)...
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo   LAN access: http://%%a:8000
echo   Local access: http://localhost:8000
echo.
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
pause
