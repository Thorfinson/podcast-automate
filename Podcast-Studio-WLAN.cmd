@echo off
rem Like Podcast-Studio.cmd, but also reachable from your home network (WLAN), for example from a phone. Not from the Internet.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The Python environment is missing. Please run the installation in README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m podcast_automate studio "%~dp0." --lan
if errorlevel 1 pause
