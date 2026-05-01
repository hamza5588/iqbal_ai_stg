#!/usr/bin/env bash
# Download Piper ONNX + JSON configs into iqbal_ai_stg/piper_models/
# so TTS uses local Piper instead of the gTTS network fallback.
#
# Usage (from repo root or anywhere):
#   bash iqbal_ai_stg/scripts/download_piper_models.sh
#
# Requires: curl
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${ROOT}/piper_models"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"

mkdir -p "$DEST"

download_pair() {
  local relpath="$1"
  local out_base="$2"
  echo "Downloading ${out_base}..."
  curl -fL --progress-bar -o "${DEST}/${out_base}.onnx" "${BASE}/${relpath}.onnx"
  curl -fL --progress-bar -o "${DEST}/${out_base}.onnx.json" "${BASE}/${relpath}.onnx.json"
}

download_pair "en/en_US/lessac/medium/en_US-lessac-medium" "en_US-lessac-medium"
download_pair "ur/ur_PK/fasih/medium/ur_PK-fasih-medium" "ur_PK-fasih-medium"
download_pair "hi/hi_IN/rohan/medium/hi_IN-rohan-medium" "hi_IN-rohan-medium"

echo ""
echo "Done. Files are in: ${DEST}"
echo "Restart the app (gunicorn/uwsgi) so Piper reloads voices."
