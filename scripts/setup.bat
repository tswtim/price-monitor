@echo off
REM Setup script for Price Monitor on Windows
REM Run: scripts\setup.bat

echo === Price Monitor Setup ===

echo [1/3] Installing Python packages...
pip install -r requirements.txt

echo [2/3] Installing Chromium for Playwright...
python -m playwright install chromium

echo [3/3] Creating data directories...
if not exist data\reports mkdir data\reports
if not exist data\snapshots mkdir data\snapshots

if not exist data\user_price.json (
    echo {"price_rub": 1590} > data\user_price.json
    echo   Created data\user_price.json (default: 1590 rub)
)

echo.
echo === Setup complete! ===
echo.
echo Run: python -m monitor.cli --run
echo Or in Claude Code: /price-check
pause
