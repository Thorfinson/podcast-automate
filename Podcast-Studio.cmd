@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Die Python-Umgebung fehlt. Bitte zuerst die Installation in README.md ausfuehren.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m podcast_automate studio "%~dp0."
if errorlevel 1 pause
