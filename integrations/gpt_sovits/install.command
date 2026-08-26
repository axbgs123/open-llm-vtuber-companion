#!/bin/zsh
set -euo pipefail

INTEGRATION_DIR="${0:A:h}"
MINIFORGE_DIR="$INTEGRATION_DIR/miniforge"
BACKEND_DIR="$INTEGRATION_DIR/GPT-SoVITS"

if [[ ! -x "$MINIFORGE_DIR/bin/conda" ]]; then
  INSTALLER="$INTEGRATION_DIR/Miniforge3-MacOSX-arm64.sh"
  curl -L --fail --retry 5 -o "$INSTALLER" \
    "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh"
  bash "$INSTALLER" -b -p "$MINIFORGE_DIR"
  rm "$INSTALLER"
fi

if [[ ! -d "$BACKEND_DIR/.git" ]]; then
  git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS.git "$BACKEND_DIR"
fi

source "$MINIFORGE_DIR/etc/profile.d/conda.sh"
if ! conda env list | awk '{print $1}' | grep -qx "gpt-sovits"; then
  conda create -y -n gpt-sovits python=3.10
fi
conda activate gpt-sovits
conda install -y -q -c conda-forge wget
export PIP_INDEX_URL="https://mirrors.aliyun.com/pypi/simple"
cd "$BACKEND_DIR"
WORKFLOW=true bash install.sh --device MPS --source ModelScope
pip install torchcodec

echo ""
echo "GPT-SoVITS 安装完成。以后双击 start.command 启动声音克隆服务。"
read -k 1 "?按任意键关闭……"
