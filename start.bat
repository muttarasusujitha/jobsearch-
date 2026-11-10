@echo off
REM Finds, applies and tracks jobs automatically, starting a new round 1 minute after each one ends.
REM Daily limits in config/profile.toml still apply. Close this window to stop.
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m jobagent run --auto-apply --loop-minutes 1
pause
