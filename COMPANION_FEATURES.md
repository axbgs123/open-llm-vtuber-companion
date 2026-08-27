# Local Companion Suite

This branch extends Open-LLM-VTuber with a local-first companion layer while
keeping upstream chat and ASR behavior intact while adding a selectable VRM stage
and a bundled Chinese CosyVoice3 runtime.

## Start and stop

- Double-click `start-companion.command`. It starts Ollama, starts the server,
  and opens the companion console.
- CosyVoice3 starts only when a cloned voice is previewed or used.
- Double-click `stop-companion.command` to stop the server, unload local model
  weights, stop CosyVoice3, and quit Ollama.
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
- Memory records also carry importance, expiry, confirmation state and last
  confirmation time. Short-lived plans expire automatically; low-confidence
  notes are excluded from prompts until the owner confirms them.
- Stable extraction covers identity, preferred name, location, occupation,
  birthday, family relations, current projects, recurring habits and near-term
  plans. Near-duplicate facts merge their source trails.

### Hybrid history retrieval

- Keyword retrieval uses SQLite FTS5 trigram and requires no model.
- Optional semantic retrieval uses `qwen3-embedding:0.6b` through Ollama.
- The semantic model is downloaded only when enabled in the console.
- Keyword and semantic rankings are merged; new completed turns are appended
  incrementally after the first semantic index build.
- A deterministic reranker boosts direct lexical evidence and, for queries such
  as "recently" or "last time", favors appropriately recent conversations.

### Relationship continuity and commitments

- Each character keeps a small relationship journal containing conversation
  count, engagement trend, and recent expression tone. It is context, not a
  psychological diagnosis, and raw scores are never spoken to the user.
- Phrases such as "remind me" and near-term plans are converted into per-role
  commitments. They can also be created, completed, or dismissed in the console.
- Commitments due within 24 hours take priority in proactive conversation, while
  preserving the character's natural voice instead of announcing a system alert.

### Proactive conversation policy

- Backend-owned idle timer; it does not depend on browser local settings.
- Recent-topic dedupe, daily cap and quiet hours.
- Adaptive cadence increases the delay after unanswered proactive messages and
  gently decreases it after repeated responses.
- Read-only macOS presence signals suppress proactive speech during meetings,
  full-screen apps, Focus mode, lock screen, likely microphone use, or extended
  absence. No window titles or activity contents are stored.
- Upcoming commitments and the most recent expression tone can shape the topic;
  all existing dedupe, quiet-hour, daily-limit, and environment rules still apply.

### Encrypted backup vault

- A machine-local Fernet key encrypts `.aicbackup` archives. The key is mode
  `0600`, remains under ignored `companion_data/`, and is never pushed.
- Daily global backups are incremental after the first full snapshot.
- Character backups include that character's YAML, history, structured memory,
  voice profiles and reference files. Character state is merged on restore.
- Destructive character, memory and voice actions create a safety snapshot.
- Restore itself creates a pre-restore snapshot.
- Export `companion-backup-recovery.key` separately from the encrypted archives;
  it is required to decrypt backups after moving to another machine.
- Recovery preview lists files that will be created, overwritten, or removed
  before restore is allowed.
- Recovery keys from another installation can be imported into a local keyring;
  importing never replaces the current machine key.
- Configurable retention keeps the newest snapshots and safety snapshots while
  preserving every full backup required by a retained incremental chain.

### Versioned data and upstream updates

- `companion_data/schema.json` tracks companion-data schema migrations.
- Startup migrates old records after making a backup when appropriate.
- `sync-upstream.command` requires a clean worktree, creates an encrypted backup,
  fetches official upstream, aborts cleanly on conflicts, then runs migrations
  and tests.

### VRM body and performance

- Import VRM 0.x/1.0 avatars per character and switch between VRM and the
  upstream Live2D fallback from the companion console.
- Three.js and `@pixiv/three-vrm` provide humanoid bones, expressions, physics,
  automatic framing and procedural idle/emotion gestures.
- `three-vrm-lip-sync` performs real-time MFCC vowel classification and drives
  the VRM `aa/ih/ou/ee/oh` visemes from the exact audio element being played.
- Avatar settings and license notes are per character. Local VRM files are
  ignored by Git and included in encrypted character backups.

### Voice cloning

The integration wraps the official
[FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) CosyVoice3
0.5B model behind a loopback-only headless API.
Reference audio and voice profiles are stored per character. Only clone a voice
you own or have explicit permission to use.

- The same semantic expression drives VRM facial expression, procedural gesture,
  and a Chinese speaking-style instruction such as happy, sad, angry, surprised,
  shy, calm, or excited.
- The service is serialized for model safety, starts on demand, and unloads after
  the configured idle period. It listens only on `127.0.0.1:50000`.

### Live2D lip-sync bridge

- A local compatibility bridge follows the playback ordering used by
  [easy-live2d](https://github.com/Panzer-Jack/easy-live2d): decode the PCM used
  for lip sync first, then start browser audio and the Live2D clock together.
- It intercepts only generated `data:audio/wav` speech and leaves other browser
  media unchanged. If PCM preparation fails, normal playback continues.
- RMS gain, silence threshold, mouth-opening attack and closing release are
  configurable from **Companion Console → Voice cloning**.
- The upstream frontend submodule remains untouched; the bridge is injected by
  the local server and can be removed independently.

### Diagnostics and memory budget

The diagnostics center records only local duration, success, cache-hit and peak
process-memory metrics; it never stores conversation text.

- CosyVoice3 alone: approximately 4.1 GB RAM after one synthesis.
- Base local chat plus VRM: approximately 5–8 GB RAM depending on the LLM.
- Chat, VRM, CosyVoice3 and semantic retrieval together: reserve 12–18 GB.
- CosyVoice3 disk usage on Apple Silicon is approximately 10 GB including its
  isolated Python environment and the 0.5B model.

These are working-set estimates rather than additional disk downloads. The new
continuity, reminder, diagnostics and cache-management code itself normally adds
only tens of megabytes.

## Data and privacy

The following remain local and are excluded from Git:

- `chat_history/`
- `companion_data/`
- `models/`
- `integrations/cosyvoice/CosyVoice/`
- `integrations/cosyvoice/.venv/`
- `integrations/cosyvoice/models/`
- `integrations/vrm_web/node_modules/`

The private repository contains integration code and install scripts, not model
weights, voice samples, API credentials, or conversations.

## Verification

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check src/open_llm_vtuber tests
git diff --check
```
