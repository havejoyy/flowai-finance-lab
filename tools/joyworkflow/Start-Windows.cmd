@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"
  if errorlevel 1 goto missing
  py -3 app.py
  pause
  exit /b
)
where python >nul 2>nul
if errorlevel 1 goto missing
python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"
if errorlevel 1 goto missing
python app.py
pause
exit /b
:missing
echo Please install Python 3.9+ from https://www.python.org/downloads/ then retry.
pause
exit /b 1
