#!/bin/zsh
set -euo pipefail

APP_DIR="${0:A:h}"
cd "$APP_DIR"
UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
  echo "未找到 uv。请先安装：https://docs.astral.sh/uv/"
  read -k 1 "?按任意键关闭……"
  exit 1
fi

if ! curl -sS --max-time 1 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  echo "正在启动 Ollama…"
  open -a Ollama
  for _ in {1..30}; do
    curl -sS --max-time 1 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
    sleep 1
  done
  if ! curl -sS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "Ollama 未能在 30 秒内启动，请先检查 Ollama 安装或日志。"
    exit 1
  fi
fi

stop_on_exit=true
if [[ -f companion_data/runtime.json ]] && command -v jq >/dev/null; then
  stop_on_exit="$(jq -r '.stop_services_on_exit // true' companion_data/runtime.json)"
fi

server_pid=""
server_pid_file="$APP_DIR/companion_data/server.pid"
cleaning=false
cleanup() {
  [[ "$cleaning" == "true" ]] && return
  cleaning=true
  trap - EXIT INT TERM
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill -INT "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -f "$server_pid_file"
  if [[ "$stop_on_exit" == "true" ]]; then
    echo "正在释放本地模型与声音服务…"
    curl -sS --max-time 8 -X POST http://127.0.0.1:11434/api/generate \
      -H 'Content-Type: application/json' \
      -d '{"model":"qwen2.5:3b","keep_alive":0}' >/dev/null 2>&1 || true
    curl -sS --max-time 8 -X POST http://127.0.0.1:11434/api/embed \
      -H 'Content-Type: application/json' \
      -d '{"model":"qwen3-embedding:0.6b","input":"","keep_alive":0}' >/dev/null 2>&1 || true
    curl -sS --max-time 3 -X POST 'http://127.0.0.1:50000/shutdown' >/dev/null 2>&1 || true
    osascript -e 'tell application "Ollama" to quit' >/dev/null 2>&1 || true
    sleep 1
    pkill -TERM -f '/Applications/Ollama.app/Contents/' >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

"$UV_BIN" run run_server.py &
server_pid=$!
mkdir -p "$APP_DIR/companion_data"
echo "$server_pid" > "$server_pid_file"
(
  for _ in {1..60}; do
    if curl -fsS --max-time 1 http://127.0.0.1:12393/api/companion/runtime >/dev/null 2>&1; then
      open "http://127.0.0.1:12393/"
      exit 0
    fi
    kill -0 "$server_pid" 2>/dev/null || exit 1
    sleep 0.5
  done
  echo "后端未能在 30 秒内就绪，请查看终端日志。"
) &
wait "$server_pid"
