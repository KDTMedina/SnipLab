@echo off
REM Builds dist\SnipLab.exe (run inside the activated virtual environment)
pip install -r requirements-dev.txt
pyinstaller --noconfirm --windowed --onefile --name SnipLab --paths src run_sniplab.py
echo.
echo Done: dist\SnipLab.exe
