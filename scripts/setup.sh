#!/bin/bash
# Setup script for Price Monitor on Linux/macOS/Git Bash
# Run: bash scripts/setup.sh

set -e

echo "=== Price Monitor Setup ==="
echo ""

# Python dependencies
echo "[1/3] Installing Python packages..."
pip install -r requirements.txt

# Playwright browser
echo "[2/3] Installing Chromium for Playwright..."
python -m playwright install chromium

# Create data directories
echo "[3/3] Creating data directories..."
mkdir -p data/reports data/snapshots

# Set default user price if not exists
if [ ! -f data/user_price.json ]; then
    echo '{"price_rub": 1590}' > data/user_price.json
    echo '  Created data/user_price.json (default: 1590 rub)'
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Run: python -m monitor.cli --run"
echo "Or in Claude Code: /price-check"
