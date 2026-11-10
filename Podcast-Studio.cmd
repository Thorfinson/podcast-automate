@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The Python environment is missing. Please run the installation in README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m podcast_automate studio "%~dp0."
if errorlevel 1 pause
