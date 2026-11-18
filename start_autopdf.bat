@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python virtual environment .venv was not found.
  echo Run setup_autopdf.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" autopdf_app.py
if errorlevel 1 pause

