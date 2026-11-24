@echo off
rem Stellt die Python-Umgebung unter %LOCALAPPDATA%\TrackMyBudget\venv bereit (außerhalb von OneDrive).
set "VENV=%LOCALAPPDATA%\TrackMyBudget\venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo Richte Python-Umgebung ein ...
  python -m venv "%VENV%" || exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r "%~dp0requirements.txt" || exit /b 1
