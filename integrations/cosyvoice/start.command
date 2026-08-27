#!/bin/zsh
set -euo pipefail

HERE="${0:A:h}"
export PYTHONPATH="$HERE/CosyVoice:$HERE/CosyVoice/third_party/Matcha-TTS"
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=4
exec "$HERE/.venv/bin/python" "$HERE/server.py"
