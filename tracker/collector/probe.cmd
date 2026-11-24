@echo off
rem Test-Abruf bei der ING. Legt beim ersten Start eine Python-Umgebung unter %LOCALAPPDATA%\TrackMyBudget\venv an.
setlocal
set "VENV=%LOCALAPPDATA%\TrackMyBudget\venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo Richte Python-Umgebung ein ...
  python -m venv "%VENV%" || goto :err
  "%VENV%\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r "%~dp0requirements.txt" || goto :err
)
"%VENV%\Scripts\python.exe" "%~dp0probe.py" %*
echo.
pause
exit /b
:err
echo Einrichtung fehlgeschlagen.
pause
