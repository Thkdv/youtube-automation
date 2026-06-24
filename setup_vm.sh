#!/usr/bin/env bash
# Run once on the VM after uploading youtube-automation.zip
# scp youtube-automation.zip VM:~ && ssh VM "bash ~/youtube-automation/setup_vm.sh"

set -euo pipefail

PROJECT_DIR="$HOME/youtube-automation"

echo "==> Unzipping project..."
unzip -o youtube-automation.zip -d "$HOME"
cd "$PROJECT_DIR"

echo "==> Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "==> Installing Python dependencies..."
pip install --quiet -r requirements.txt

echo "==> Creating log directory..."
mkdir -p "$HOME/logs"

echo "==> Making run.sh executable..."
chmod +x "$PROJECT_DIR/run.sh"

echo ""
echo "======================================"
echo " Setup complete!"
echo ""
echo " Manual test:"
echo "   cd $PROJECT_DIR && source venv/bin/activate && python3 main.py"
echo "======================================"
