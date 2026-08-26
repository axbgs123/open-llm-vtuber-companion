#!/bin/zsh
set -euo pipefail

APP_DIR="${0:A:h}"
cd "$APP_DIR"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "工作区有未提交修改。请先提交或暂存，再同步上游。"
  read -k 1 "?按任意键关闭……"
  exit 1
fi

echo "正在创建升级前加密备份…"
uv run python -c 'from src.open_llm_vtuber.backup_manager import create_backup; print(create_backup(scope="global", reason="before_upstream_sync")["filename"])'

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream https://github.com/Open-LLM-VTuber/Open-LLM-VTuber.git
fi
git fetch upstream main
echo "准备合并 upstream/main…"
if ! git merge --no-commit --no-ff upstream/main; then
  git merge --abort || true
  echo "上游存在冲突，已自动中止合并；当前文件保持原状。"
  read -k 1 "?按任意键关闭……"
  exit 1
fi

git commit -m "chore: sync upstream Open-LLM-VTuber"
uv sync --locked
uv run python -c 'from src.open_llm_vtuber.data_migrations import run_migrations; print(run_migrations())'
uv run python -m unittest discover -s tests -v
echo "上游同步完成。"
read -k 1 "?按任意键关闭……"
