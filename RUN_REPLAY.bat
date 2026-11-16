@echo off
cd /d "%~dp0"
set "PY=python"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo.
echo TRADING BOT - HISTORICAL REPLAY
"%PY%" replay_server.py
if errorlevel 1 (
  echo.
  echo Replay server failed. Read the error above or see README_REPLAY.txt
  pause
)
