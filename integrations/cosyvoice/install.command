#!/bin/zsh
set -euo pipefail

HERE="${0:A:h}"
UV_BIN="${commands[uv]:-${HOME}/.local/bin/uv}"

if [[ ! -x "$UV_BIN" ]]; then
  echo "未找到 uv，请先安装：https://docs.astral.sh/uv/"
  exit 1
fi

if [[ ! -d "$HERE/CosyVoice/.git" ]]; then
  git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "$HERE/CosyVoice"
else
  git -C "$HERE/CosyVoice" submodule update --init --recursive
fi

if [[ ! -x "$HERE/.venv/bin/python" ]]; then
  "$UV_BIN" venv --python 3.10 "$HERE/.venv"
fi
"$UV_BIN" pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple --python "$HERE/.venv/bin/python" setuptools==80.9.0 numpy==1.26.4 cython wheel
"$UV_BIN" pip install --no-build-isolation --index-url https://pypi.tuna.tsinghua.edu.cn/simple --python "$HERE/.venv/bin/python" -r "$HERE/requirements-macos.txt"

cd "$HERE"
"$HERE/.venv/bin/python" -c "from modelscope import snapshot_download; snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir='models/Fun-CosyVoice3-0.5B')"
echo "CosyVoice 安装完成。"
