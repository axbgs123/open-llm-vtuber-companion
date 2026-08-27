#!/bin/zsh
set -u

open_pid="$(pgrep -f '/Open-LLM-VTuber/.venv/bin/python3 run_server.py' | head -n 1)"

[[ -n "$open_pid" ]] && kill -INT "$open_pid" 2>/dev/null || true
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
