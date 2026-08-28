#!/bin/zsh
set -euo pipefail

HERE="${0:A:h}"
UV_BIN="${commands[uv]:-${HOME}/.local/bin/uv}"
COSYVOICE_COMMIT="074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
MODEL_REVISION="9f9c56f2514700ef79d64fd0afb693e0d672373b"
WETEXT_REVISION="dba0737b6bc1093e108eb5bc512babf4a7e157b4"

if [[ ! -x "$UV_BIN" ]]; then
  echo "未找到 uv，请先安装：https://docs.astral.sh/uv/"
  exit 1
fi

if [[ ! -d "$HERE/CosyVoice/.git" ]]; then
  git clone --no-checkout https://github.com/FunAudioLLM/CosyVoice.git "$HERE/CosyVoice"
fi
git -C "$HERE/CosyVoice" fetch origin "$COSYVOICE_COMMIT"
git -C "$HERE/CosyVoice" checkout --detach "$COSYVOICE_COMMIT"
git -C "$HERE/CosyVoice" submodule update --init --recursive

if [[ ! -x "$HERE/.venv/bin/python" ]]; then
  "$UV_BIN" venv --python 3.10 "$HERE/.venv"
fi
"$UV_BIN" pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple --python "$HERE/.venv/bin/python" setuptools==80.9.0 numpy==1.26.4 cython wheel
"$UV_BIN" pip install --no-build-isolation --index-url https://pypi.tuna.tsinghua.edu.cn/simple --python "$HERE/.venv/bin/python" -r "$HERE/requirements-macos.txt"

cd "$HERE"
"$HERE/.venv/bin/python" -c "from modelscope import snapshot_download; snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', revision='$MODEL_REVISION', local_dir='models/Fun-CosyVoice3-0.5B')"
if [[ ! -d "$HERE/models/wetext/.git" ]]; then
  if [[ -e "$HERE/models/wetext" ]]; then
    echo "models/wetext 已存在但不是可验证的 Git 仓库，请先移动该目录后重试。"
    exit 1
  fi
  git clone https://www.modelscope.cn/pengzhendong/wetext.git "$HERE/models/wetext"
fi
git -C "$HERE/models/wetext" fetch origin "$WETEXT_REVISION"
git -C "$HERE/models/wetext" checkout --detach "$WETEXT_REVISION"
git -C "$HERE/models/wetext" lfs pull
PYTHONPATH="$HERE:$HERE/CosyVoice:$HERE/CosyVoice/third_party/Matcha-TTS" \
  "$HERE/.venv/bin/python" -c "import server; server._load_model(); assert server.model is not None, server.model_error; print('CosyVoice 本地依赖验证完成')"
echo "CosyVoice 安装完成。"
