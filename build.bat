@echo off
rem Builds release\RockID.exe from source (needs Python 3 + install.bat done once)
cd /d "%~dp0"
python -m pip install -q pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name RockID ^
  --icon "%~dp0assets\rockid.ico" --add-data "%~dp0data.json;." --collect-all winrt --exclude-module numpy --exclude-module charset_normalizer ^
  --distpath "%~dp0release" --workpath "%TEMP%\rockid-build" --specpath "%TEMP%\rockid-build" ^
  "%~dp0rockid.py"
echo.
echo Done: release\RockID.exe
pause
