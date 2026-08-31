@echo off
REM Build a standalone Windows .exe (no Python needed to run it afterward).
REM Usage:  double-click this file, or run it from a command prompt.
cd /d "%~dp0"

python -m pip install --upgrade pyinstaller pikepdf
if errorlevel 1 goto :err

python -m PyInstaller ^
  --noconfirm --clean ^
  --onefile ^
  --windowed ^
  --name "PDF Password Remover" ^
  pdf_password_remover.py
if errorlevel 1 goto :err

echo.
echo Done. App is at: dist\PDF Password Remover.exe
echo.
echo This .exe is UNSIGNED. Windows SmartScreen may warn on first launch:
echo click "More info" then "Run anyway".
goto :eof

:err
echo.
echo Build failed. Make sure Python 3 is installed and on your PATH.
exit /b 1
