#!/bin/zsh
set -euo pipefail

HERE="${0:A:h}"
export PYTHONPATH="$HERE/CosyVoice:$HERE/CosyVoice/third_party/Matcha-TTS"
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=4
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export MODELSCOPE_OFFLINE=1
exec "$HERE/.venv/bin/python" "$HERE/server.py"
