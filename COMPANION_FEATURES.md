# Local Companion Suite

This branch extends Open-LLM-VTuber with a local-first companion layer while
keeping upstream chat, Live2D, ASR and TTS behavior intact.

## Start and stop

- Double-click `start-companion.command`. It starts Ollama, starts the server,
  and opens the companion console.
- GPT-SoVITS starts only when a cloned voice is previewed or used.
- Double-click `stop-companion.command` to stop the server, unload local model
  weights, stop GPT-SoVITS, and quit Ollama.
- Resource behavior can be changed from **Companion Console → Local compute**.

## Companion console

Open `http://127.0.0.1:12393/companion/`.

### Character management

Create and edit character override YAML files without replacing the upstream
configuration system. Removing a character archives its YAML under
`characters/.trash`; chat history and memories remain recoverable.

### Attributable long-term memory

- Core memory remains a short prompt injected per character.
- Structured records live at
  `chat_history/<conf_uid>/memory_records.json` and include category,
  confidence, source history, excerpt, timestamps and status.
- Stable claims such as name, preferred form of address, location, occupation,
  and preferences are conflict-checked. New conflicting claims stay pending
  until the owner chooses which fact to keep.
- Every sourced fact can open its original transcript.

### Hybrid history retrieval

- Keyword retrieval uses SQLite FTS5 trigram and requires no model.
- Optional semantic retrieval uses `qwen3-embedding:0.6b` through Ollama.
- The semantic model is downloaded only when enabled in the console.
- Keyword and semantic rankings are merged; new completed turns are appended
  incrementally after the first semantic index build.

### Proactive conversation policy

- Backend-owned idle timer; it does not depend on browser local settings.
- Recent-topic dedupe, daily cap and quiet hours.
- Adaptive cadence increases the delay after unanswered proactive messages and
  gently decreases it after repeated responses.

### Voice cloning

The integration wraps the official
[RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) local API.
Reference audio and voice profiles are stored per character. Only clone a voice
you own or have explicit permission to use.

## Data and privacy

The following remain local and are excluded from Git:

- `chat_history/`
- `companion_data/`
- `models/`
- `integrations/gpt_sovits/GPT-SoVITS/`
- `integrations/gpt_sovits/miniforge/`

The private repository contains integration code and install scripts, not model
weights, voice samples, API credentials, or conversations.

## Verification

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check src/open_llm_vtuber tests
git diff --check
```
