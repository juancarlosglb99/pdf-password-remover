#!/usr/bin/env bash
# Build a standalone macOS .app (no Python needed to run it afterward).
# Usage:  ./build_macos.sh
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --upgrade pyinstaller pikepdf

python3 -m PyInstaller \
  --noconfirm --clean \
  --windowed \
  --name "PDF Password Remover" \
  pdf_password_remover.py

echo
echo "Done. App is at: dist/PDF Password Remover.app"
echo
echo "This app is UNSIGNED. The first time you open it, macOS Gatekeeper will"
echo "block it. To run it, either:"
echo "  * Right-click the app > Open, then click Open in the dialog, OR"
echo "  * Run:  xattr -dr com.apple.quarantine \"dist/PDF Password Remover.app\""
