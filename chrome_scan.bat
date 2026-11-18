@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "import playwright.sync_api" 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install "playwright>=1.50,<2"
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" chrome_scan.py
pause
