@echo off
rem Wie Podcast-Studio.cmd, aber auch im Heimnetz erreichbar, etwa vom Handy im WLAN. Aus dem Internet nicht.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Die Python-Umgebung fehlt. Bitte zuerst die Installation in README.md ausfuehren.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m podcast_automate studio "%~dp0." --lan
if errorlevel 1 pause
