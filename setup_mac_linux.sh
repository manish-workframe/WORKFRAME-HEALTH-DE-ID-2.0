#!/usr/bin/env bash
# Workframe Health De-ID 2.0 setup for Mac / Linux (needs internet once).
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.11+ from python.org (Mac) and run this again."; exit 1
fi
python3 -m venv .venv && source .venv/bin/activate && python -m pip install --upgrade pip && pip install -r requirements.txt || exit 1
if ! command -v tesseract >/dev/null 2>&1; then
  echo "NOTE: Tesseract OCR is not installed. On Mac: brew install tesseract   (finds names printed inside X-rays)"
fi
echo "Running self-test..."
python selftest.py
echo
echo "Start the app with:  bash start_mac_linux.sh"
