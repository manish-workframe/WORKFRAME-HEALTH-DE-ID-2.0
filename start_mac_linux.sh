#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
echo "Workframe Health De-ID is running at http://127.0.0.1:8765  (press Ctrl+C to stop)"
python app.py
