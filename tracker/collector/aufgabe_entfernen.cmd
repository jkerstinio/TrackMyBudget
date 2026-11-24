@echo off
schtasks /Delete /F /TN "TrackMyBudget Sammler taeglich"
schtasks /Delete /F /TN "TrackMyBudget Sammler Anmeldung" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TrackMyBudget Sammler.lnk" 2>nul
echo Automatischer Abruf entfernt.
pause
