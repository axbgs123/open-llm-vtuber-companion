#!/bin/zsh
set -euo pipefail

INTEGRATION_DIR="${0:A:h}"
source "$INTEGRATION_DIR/miniforge/etc/profile.d/conda.sh"
conda activate gpt-sovits
cd "$INTEGRATION_DIR/GPT-SoVITS"
exec python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
