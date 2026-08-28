#!/bin/zsh
set -u

APP_DIR="${0:A:h}"
pid_file="$APP_DIR/companion_data/server.pid"
open_pid=""
if [[ -f "$pid_file" ]]; then
  candidate="$(tr -cd '0-9' < "$pid_file")"
  command_line="$(ps -p "$candidate" -o command= 2>/dev/null || true)"
  if [[ -n "$candidate" && "$command_line" == *"run_server.py"* ]]; then
    open_pid="$candidate"
  fi
fi
if [[ -z "$open_pid" ]]; then
  candidate="$(lsof -nP -t -iTCP:12393 -sTCP:LISTEN 2>/dev/null | head -n 1)"
  command_line="$(ps -p "$candidate" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"run_server.py"* ]] && open_pid="$candidate"
fi

[[ -n "$open_pid" ]] && kill -INT "$open_pid" 2>/dev/null || true
for _ in {1..40}; do
  [[ -z "$open_pid" ]] || ! kill -0 "$open_pid" 2>/dev/null && break
  sleep 0.25
done
rm -f "$pid_file"
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
sleep 2
echo "AI 伴侣、声音克隆和本地模型均已关闭。"
