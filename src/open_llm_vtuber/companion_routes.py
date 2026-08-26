"""Built-in companion management: characters, durable memory, proactive chat and cloned voices."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile
from loguru import logger
from ruamel.yaml import YAML
from starlette.responses import FileResponse, JSONResponse, Response

from . import memory_fts
from . import memory_records
from . import memory_semantic
from .chat_history_manager import get_history
from .config_manager.utils import read_yaml
from .memory_core import (
    CAP_CHARS,
    clear_core_memory,
    load_core_memory,
    save_core_memory,
)
from . import runtime_manager
from . import proactive_manager
from . import backup_manager
from . import data_migrations
from . import upstream_manager
from . import environment_awareness

ROOT = Path.cwd().resolve()
CHARACTERS_DIR = ROOT / "characters"
DATA_DIR = ROOT / "companion_data"
VOICE_DIR = DATA_DIR / "voice_references"
STATE_PATH = DATA_DIR / "state.json"
PROACTIVE_PROMPT_PATH = ROOT / "prompts" / "utils" / "proactive_speak_prompt.txt"

DEFAULT_PROACTIVE = {
    "enabled": True,
    "idle_seconds": 120,
    "cooldown_seconds": 300,
    "topics": ["最近在忙什么", "AI 与科技", "电影、音乐或游戏", "生活里的小发现"],
    "adaptive_enabled": True,
    "daily_limit": 6,
    "recent_topic_window": 4,
    "quiet_hours": {"enabled": True, "start": "23:00", "end": "08:00"},
    "environment": {
        "block_meeting_apps": True,
        "block_full_screen": True,
        "block_focus_mode": True,
        "block_when_locked": True,
        "block_when_away": True,
        "block_microphone": True,
        "away_after_seconds": 900,
        "blocked_apps": environment_awareness.DEFAULT_BLOCKED_APPS,
    },
}

_yaml = YAML()
_yaml.preserve_quotes = True


def _default_state() -> dict[str, Any]:
    return {
        "memory": {},
        "proactive": dict(DEFAULT_PROACTIVE),
        "voices": {},
        "active_voice": {},
    }


def _load_state() -> dict[str, Any]:
    state = _default_state()
    try:
        if STATE_PATH.is_file():
            loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in state:
                    if key in loaded and isinstance(loaded[key], dict):
                        state[key].update(loaded[key])
    except Exception as exc:
        logger.warning(f"[companion] state load failed: {exc}")
    return state


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, STATE_PATH)


def _local_only(request: Request) -> JSONResponse | None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        return JSONResponse({"ok": False, "error": "localhost only"}, status_code=403)
    return None


def _slug(value: str, fallback: str = "character") -> str:
    value = re.sub(r"[^\w\-]+", "_", (value or "").strip(), flags=re.UNICODE)
    value = value.strip("._-")[:80]
    return value or fallback


def _safe_character_file(filename: str) -> Path:
    if filename == "conf.yaml":
        return ROOT / filename
    if Path(filename).name != filename or not filename.endswith((".yaml", ".yml")):
        raise ValueError("invalid character filename")
    path = (CHARACTERS_DIR / filename).resolve()
    if path.parent != CHARACTERS_DIR.resolve():
        raise ValueError("invalid character path")
    return path


def _character_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    paths = [("conf.yaml", ROOT / "conf.yaml")]
    if CHARACTERS_DIR.is_dir():
        paths.extend(
            (path.name, path)
            for path in sorted(CHARACTERS_DIR.glob("*.yaml"))
            if path.is_file()
        )
    for filename, path in paths:
        try:
            config = read_yaml(str(path)) or {}
            char = config.get("character_config") or {}
            entries.append(
                {
                    "filename": filename,
                    "is_base": filename == "conf.yaml",
                    "conf_name": char.get("conf_name", path.stem),
                    "conf_uid": char.get("conf_uid", path.stem),
                    "character_name": char.get("character_name", ""),
                    "live2d_model_name": char.get("live2d_model_name", ""),
                    "avatar": char.get("avatar", ""),
                    "persona_prompt": char.get("persona_prompt", ""),
                    "tts_model": (char.get("tts_config") or {}).get("tts_model"),
                }
            )
        except Exception as exc:
            logger.warning(f"[companion] skip invalid character {path}: {exc}")
    return entries


def _find_character(conf_uid: str) -> tuple[str, Path] | None:
    for entry in _character_entries():
        if str(entry["conf_uid"]) == str(conf_uid):
            return entry["filename"], _safe_character_file(entry["filename"])
    return None


def _read_roundtrip(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return _yaml.load(handle) or {}


def _write_roundtrip(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        _yaml.dump(data, handle)
    os.replace(temp, path)


def get_memory_settings(
    conf_uid: str, character_config: Any | None = None
) -> dict[str, Any]:
    state = _load_state()
    fallback = {
        "enabled": getattr(character_config, "long_term_memory_enabled", True),
        "max_chars": getattr(character_config, "core_memory_max_chars", CAP_CHARS),
        "fts_enabled": getattr(character_config, "fts_memory_enabled", True),
        "semantic_enabled": False,
        "top_k": getattr(character_config, "fts_memory_top_k", 3),
        "consolidation_interval": getattr(
            character_config, "memory_consolidation_interval", 1
        ),
    }
    saved = state["memory"].get(str(conf_uid), {})
    if isinstance(saved, dict):
        fallback.update(saved)
    fallback["max_chars"] = max(500, min(8000, int(fallback["max_chars"])))
    fallback["top_k"] = max(1, min(10, int(fallback["top_k"])))
    interval = int(fallback["consolidation_interval"])
    fallback["consolidation_interval"] = interval if interval in (1, 3, 5) else 1
    return fallback


def get_proactive_settings() -> dict[str, Any]:
    state = _load_state()
    settings = dict(DEFAULT_PROACTIVE)
    settings.update(state.get("proactive", {}))
    quiet = dict(DEFAULT_PROACTIVE["quiet_hours"])
    if isinstance(settings.get("quiet_hours"), dict):
        quiet.update(settings["quiet_hours"])
    settings["quiet_hours"] = quiet
    environment = dict(DEFAULT_PROACTIVE["environment"])
    if isinstance(settings.get("environment"), dict):
        environment.update(settings["environment"])
    settings["environment"] = environment
    settings["idle_seconds"] = max(15, min(86400, int(settings["idle_seconds"])))
    settings["cooldown_seconds"] = max(
        settings["idle_seconds"], min(86400, int(settings["cooldown_seconds"]))
    )
    settings["topics"] = [
        str(topic).strip()[:120]
        for topic in settings.get("topics", [])[:30]
        if str(topic).strip()
    ]
    settings["daily_limit"] = max(1, min(50, int(settings.get("daily_limit", 6))))
    settings["recent_topic_window"] = max(
        1, min(10, int(settings.get("recent_topic_window", 4)))
    )
    return settings


def write_proactive_prompt(
    settings: dict[str, Any] | None = None, selected_topic: str | None = None
) -> str:
    settings = settings or get_proactive_settings()
    topics = settings.get("topics", [])
    topic_lines = (
        "\n".join(f"- {topic}" for topic in topics) or "- 关心对方最近在做什么"
    )
    selected_line = (
        f"\n这一次优先从这个方向切入：{selected_topic}。不要照抄题目。\n"
        if selected_topic
        else ""
    )
    content = (
        "使用者已经沉默了一段时间。请你以当前角色的口吻自然地主动开口，像朋友一样，"
        "不要提及计时器、系统提示或‘主动发言’。只说一到两句，避免重复上一次的话题。\n\n"
        "可以从下面的话题随机挑一个，也可以根据长期记忆自然关心对方：\n"
        f"{topic_lines}\n\n"
        f"{selected_line}"
        "用一个具体看法、轻松问题或真诚关心开启对话，不要像主持人报幕。"
    )
    PROACTIVE_PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PROACTIVE_PROMPT_PATH.with_suffix(".txt.tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, PROACTIVE_PROMPT_PATH)
    return content


def _loopback_api(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
    except Exception:
        return False


def _activate_voice(conf_uid: str, profile: dict[str, Any]) -> None:
    found = _find_character(conf_uid)
    if not found:
        raise ValueError("character not found")
    _, path = found
    data = _read_roundtrip(path)
    char = data.setdefault("character_config", {})
    tts = char.setdefault("tts_config", {})
    tts["tts_model"] = "gpt_sovits_tts"
    tts["gpt_sovits_tts"] = {
        "api_url": profile["api_url"],
        "text_lang": profile["text_lang"],
        "ref_audio_path": profile["ref_audio_path"],
        "prompt_lang": profile["prompt_lang"],
        "prompt_text": profile["prompt_text"],
        "text_split_method": "cut5",
        "batch_size": "1",
        "media_type": "wav",
        "streaming_mode": "false",
    }
    _write_roundtrip(path, data)


def init_companion_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/companion/status")
    async def status(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        return {
            "ok": True,
            "characters": len(_character_entries()),
            "proactive": get_proactive_settings(),
            "voice_backend": "GPT-SoVITS",
            "runtime": await runtime_manager.status(),
        }

    @router.get("/api/companion/backups")
    async def backups(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        return {"ok": True, "backups": backup_manager.list_backups()}

    @router.get("/api/companion/system/version")
    async def system_version(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        return {
            "ok": True,
            "schema": data_migrations.status(),
            "upstream": upstream_manager.status(fetch=False),
        }

    @router.post("/api/companion/system/migrate")
    async def migrate_system(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        return {"ok": True, "migration": data_migrations.run_migrations()}

    @router.post("/api/companion/system/upstream-check")
    async def upstream_check(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        result = await __import__("asyncio").to_thread(
            upstream_manager.status, fetch=True
        )
        return result

    @router.post("/api/companion/backups")
    async def create_backup(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        body = await request.json()
        try:
            record = await __import__("asyncio").to_thread(
                backup_manager.create_backup,
                scope=str(body.get("scope") or "global"),
                conf_uid=str(body.get("conf_uid") or ""),
                incremental=bool(body.get("incremental", False)),
                reason="manual",
            )
            return {"ok": True, "backup": record}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.post("/api/companion/backups/{filename}/restore")
    async def restore_backup(filename: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        if Path(filename).name != filename or not filename.endswith(".aicbackup"):
            return JSONResponse(
                {"ok": False, "error": "invalid filename"}, status_code=400
            )
        try:
            return await __import__("asyncio").to_thread(
                backup_manager.restore_backup, filename
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.get("/api/companion/backups/recovery-key/download")
    async def download_backup_key(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        backup_manager._key()
        return FileResponse(
            backup_manager.KEY_PATH,
            filename="companion-backup-recovery.key",
            media_type="application/octet-stream",
        )

    @router.get("/api/companion/backups/{filename}/download")
    async def download_backup(filename: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        path = backup_manager.BACKUP_DIR / Path(filename).name
        if Path(filename).name != filename or not path.is_file():
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return FileResponse(
            path, filename=path.name, media_type="application/octet-stream"
        )

    @router.get("/api/companion/runtime")
    async def runtime_status(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        return {"ok": True, **(await runtime_manager.status())}

    @router.put("/api/companion/runtime")
    async def update_runtime(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        body = await request.json()
        return {"ok": True, "settings": runtime_manager.save_settings(body)}

    @router.post("/api/companion/runtime/{service}/start")
    async def start_runtime_service(service: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        if service == "ollama":
            ok = await runtime_manager.ensure_ollama()
        elif service == "gpt-sovits":
            ok = await runtime_manager.ensure_gpt_sovits()
        else:
            return JSONResponse(
                {"ok": False, "error": "unknown service"}, status_code=404
            )
        return {"ok": ok, **(await runtime_manager.status())}

    @router.post("/api/companion/runtime/{service}/stop")
    async def stop_runtime_service(service: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        if service == "ollama-model":
            ok = await runtime_manager.unload_ollama_model()
        elif service == "ollama":
            ok = await runtime_manager.stop_ollama()
        elif service == "gpt-sovits":
            ok = await runtime_manager.stop_gpt_sovits()
        else:
            return JSONResponse(
                {"ok": False, "error": "unknown service"}, status_code=404
            )
        return {"ok": ok, **(await runtime_manager.status())}

    @router.get("/api/companion/characters")
    async def list_characters(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        return {"ok": True, "characters": _character_entries()}

    @router.post("/api/companion/characters")
    async def create_character(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        body = await request.json()
        name = str(
            body.get("conf_name") or body.get("character_name") or "新角色"
        ).strip()
        uid = _slug(str(body.get("conf_uid") or name), "character")
        existing = {entry["conf_uid"] for entry in _character_entries()}
        base_uid = uid
        suffix = 2
        while uid in existing:
            uid = f"{base_uid}_{suffix}"
            suffix += 1
        filename = _slug(str(body.get("filename") or name), uid) + ".yaml"
        path = _safe_character_file(filename)
        if path.exists():
            return JSONResponse(
                {"ok": False, "error": "filename exists"}, status_code=409
            )
        data = {
            "character_config": {
                "conf_name": name,
                "conf_uid": uid,
                "character_name": str(body.get("character_name") or name),
                "live2d_model_name": str(body.get("live2d_model_name") or "mao_pro"),
                "avatar": str(body.get("avatar") or ""),
                "persona_prompt": str(
                    body.get("persona_prompt")
                    or f"你是{name}。保持自然、真诚并延续自己的性格。"
                ),
            }
        }
        _write_roundtrip(path, data)
        return {"ok": True, "filename": filename, "conf_uid": uid}

    @router.put("/api/companion/characters/{filename}")
    async def update_character(filename: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        path = _safe_character_file(filename)
        if not path.is_file():
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        try:
            char_data = read_yaml(str(path)).get("character_config", {})
            backup_manager.create_safety_snapshot(
                str(char_data.get("conf_uid") or path.stem),
                "before_character_archive",
            )
        except Exception:
            pass
        body = await request.json()
        data = _read_roundtrip(path)
        char = data.setdefault("character_config", {})
        backup_manager.create_safety_snapshot(
            str(char.get("conf_uid") or path.stem), "before_character_edit"
        )
        for key in (
            "conf_name",
            "character_name",
            "live2d_model_name",
            "avatar",
            "persona_prompt",
        ):
            if key in body:
                char[key] = str(body[key])
        _write_roundtrip(path, data)
        return {"ok": True}

    @router.delete("/api/companion/characters/{filename}")
    async def archive_character(filename: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        if filename == "conf.yaml":
            return JSONResponse(
                {"ok": False, "error": "base character cannot be removed"},
                status_code=400,
            )
        path = _safe_character_file(filename)
        if not path.is_file():
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        trash = CHARACTERS_DIR / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = trash / f"{stamp}-{path.name}"
        os.replace(path, target)
        return {"ok": True, "archived_to": str(target.relative_to(ROOT))}

    @router.get("/api/companion/memory/{conf_uid}")
    async def get_memory(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        settings = get_memory_settings(conf_uid)
        return {
            "ok": True,
            "conf_uid": conf_uid,
            "content": load_core_memory(conf_uid),
            "settings": settings,
            "fts_indexed": memory_fts.index_exists(conf_uid),
        }

    @router.put("/api/companion/memory/{conf_uid}")
    async def put_memory(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        body = await request.json()
        state = _load_state()
        settings = get_memory_settings(conf_uid)
        incoming = body.get("settings")
        if isinstance(incoming, dict):
            settings.update({key: incoming[key] for key in settings if key in incoming})
        state["memory"][conf_uid] = settings
        _save_state(state)
        if "content" in body:
            backup_manager.create_safety_snapshot(conf_uid, "before_memory_edit")
            content = str(body.get("content") or "")
            memory_records.replace_manual_memory(conf_uid, content)
            content = memory_records.active_memory_text(conf_uid, settings["max_chars"])
            save_core_memory(conf_uid, content, settings["max_chars"])
        return {"ok": True, "settings": get_memory_settings(conf_uid)}

    @router.get("/api/companion/memory/{conf_uid}/records")
    async def get_memory_records(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        records = memory_records.load_records(conf_uid)
        return {
            "ok": True,
            "records": records,
            "conflicts": [r for r in records if r.get("status") == "pending_conflict"],
            "pending_confirmation": [
                r for r in records if r.get("status") == "pending_confirmation"
            ],
        }

    @router.post("/api/companion/memory/{conf_uid}/records/{record_id}/confirm")
    async def confirm_memory_record(conf_uid: str, record_id: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        body = await request.json()
        try:
            records = memory_records.confirm_record(
                conf_uid, record_id, bool(body.get("accept", True))
            )
            settings = get_memory_settings(conf_uid)
            content = memory_records.active_memory_text(conf_uid, settings["max_chars"])
            save_core_memory(conf_uid, content, settings["max_chars"])
            return {"ok": True, "records": records, "content": content}
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @router.put("/api/companion/memory/{conf_uid}/records/{record_id}")
    async def update_memory_record(conf_uid: str, record_id: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        body = await request.json()
        try:
            records = memory_records.update_record(
                conf_uid,
                record_id,
                importance=body.get("importance"),
                expires_at=body.get("expires_at"),
            )
            settings = get_memory_settings(conf_uid)
            content = memory_records.active_memory_text(conf_uid, settings["max_chars"])
            save_core_memory(conf_uid, content, settings["max_chars"])
            return {"ok": True, "records": records, "content": content}
        except (ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.post("/api/companion/memory/{conf_uid}/resolve/{winner_id}")
    async def resolve_memory_conflict(conf_uid: str, winner_id: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        try:
            records = memory_records.resolve_conflict(conf_uid, winner_id)
            settings = get_memory_settings(conf_uid)
            content = memory_records.active_memory_text(conf_uid, settings["max_chars"])
            save_core_memory(conf_uid, content, settings["max_chars"])
            return {"ok": True, "records": records, "content": content}
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @router.get("/api/companion/memory/{conf_uid}/source/{history_uid}")
    async def get_memory_source(conf_uid: str, history_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        try:
            return {"ok": True, "messages": get_history(conf_uid, history_uid)}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @router.post("/api/companion/memory/{conf_uid}/clear")
    async def clear_memory(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        backup_manager.create_safety_snapshot(conf_uid, "before_memory_clear")
        memory_records.forget_active_records(conf_uid)
        return {"ok": clear_core_memory(conf_uid)}

    @router.post("/api/companion/memory/{conf_uid}/reindex")
    async def reindex_memory(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        count = await __import__("asyncio").to_thread(
            memory_fts.rebuild_index, conf_uid
        )
        semantic_count = None
        settings = get_memory_settings(conf_uid)
        if settings.get("semantic_enabled") and await memory_semantic.model_installed():
            semantic_count = await memory_semantic.rebuild_index(conf_uid)
        return {"ok": True, "indexed": count, "semantic_indexed": semantic_count}

    @router.get("/api/companion/memory/{conf_uid}/search")
    async def search_memory(conf_uid: str, q: str, request: Request, k: int = 5):
        if forbidden := _local_only(request):
            return forbidden
        settings = get_memory_settings(conf_uid)
        if settings.get("semantic_enabled") and memory_semantic.index_exists(conf_uid):
            detailed = await memory_semantic.hybrid_search(conf_uid, q, k)
        else:
            detailed = memory_fts.search_detailed(conf_uid, q, k)
        return {
            "ok": True,
            "results": [item["snippet"] for item in detailed],
            "detailed": detailed,
        }

    @router.get("/api/companion/memory/{conf_uid}/semantic")
    async def semantic_status(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        return {
            "ok": True,
            "model": runtime_manager.get_settings()["semantic_model"],
            "model_installed": await memory_semantic.model_installed(),
            "index_exists": memory_semantic.index_exists(conf_uid),
        }

    @router.post("/api/companion/memory/{conf_uid}/semantic/install")
    async def install_semantic(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        installed = await memory_semantic.install_model()
        return {
            "ok": installed,
            "model": runtime_manager.get_settings()["semantic_model"],
        }

    @router.post("/api/companion/memory/{conf_uid}/semantic/reindex")
    async def reindex_semantic(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        try:
            count = await memory_semantic.rebuild_index(conf_uid)
            return {"ok": True, "indexed": count}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.get("/api/companion/proactive")
    async def get_proactive(request: Request, conf_uid: str = ""):
        if forbidden := _local_only(request):
            return forbidden
        return {
            "ok": True,
            **get_proactive_settings(),
            "runtime": proactive_manager.get_runtime(conf_uid) if conf_uid else {},
            "environment_status": environment_awareness.evaluate(
                get_proactive_settings()
            ),
        }

    @router.put("/api/companion/proactive")
    async def put_proactive(request: Request):
        if forbidden := _local_only(request):
            return forbidden
        body = await request.json()
        state = _load_state()
        settings = get_proactive_settings()
        for key in (
            "enabled",
            "idle_seconds",
            "cooldown_seconds",
            "topics",
            "adaptive_enabled",
            "daily_limit",
            "recent_topic_window",
            "quiet_hours",
            "environment",
        ):
            if key in body:
                settings[key] = body[key]
        state["proactive"] = settings
        _save_state(state)
        settings = get_proactive_settings()
        write_proactive_prompt(settings)
        return {"ok": True, **settings}

    @router.get("/api/companion/voices/{conf_uid}")
    async def list_voices(conf_uid: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        state = _load_state()
        return {
            "ok": True,
            "profiles": state["voices"].get(conf_uid, []),
            "active": state["active_voice"].get(conf_uid),
        }

    @router.post("/api/companion/voices/{conf_uid}")
    async def upload_voice(
        conf_uid: str,
        request: Request,
        audio: UploadFile = File(...),
        name: str = Form(...),
        prompt_text: str = Form(""),
        prompt_lang: str = Form("zh"),
        text_lang: str = Form("zh"),
        api_url: str = Form("http://127.0.0.1:9880/tts"),
    ):
        if forbidden := _local_only(request):
            return forbidden
        if not _loopback_api(api_url):
            return JSONResponse(
                {"ok": False, "error": "API must be loopback"}, status_code=400
            )
        suffix = Path(audio.filename or "reference.wav").suffix.lower()
        if suffix not in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
            return JSONResponse(
                {"ok": False, "error": "unsupported audio"}, status_code=400
            )
        raw = await audio.read()
        if not raw or len(raw) > 50 * 1024 * 1024:
            return JSONResponse(
                {"ok": False, "error": "audio must be 1B-50MB"}, status_code=400
            )
        profile_id = _slug(name, "voice") + "-" + dt.datetime.now().strftime("%H%M%S")
        folder = VOICE_DIR / _slug(conf_uid)
        folder.mkdir(parents=True, exist_ok=True)
        ref_path = folder / f"{profile_id}{suffix}"
        ref_path.write_bytes(raw)
        profile = {
            "id": profile_id,
            "name": name.strip(),
            "ref_audio_path": str(ref_path.resolve()),
            "prompt_text": prompt_text.strip(),
            "prompt_lang": prompt_lang.strip() or "zh",
            "text_lang": text_lang.strip() or "zh",
            "api_url": api_url.rstrip("/"),
            "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        state = _load_state()
        state["voices"].setdefault(conf_uid, []).append(profile)
        _save_state(state)
        return {"ok": True, "profile": profile}

    @router.post("/api/companion/voices/{conf_uid}/{profile_id}/activate")
    async def activate_voice(conf_uid: str, profile_id: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        state = _load_state()
        profile = next(
            (p for p in state["voices"].get(conf_uid, []) if p.get("id") == profile_id),
            None,
        )
        if not profile:
            return JSONResponse(
                {"ok": False, "error": "profile not found"}, status_code=404
            )
        backup_manager.create_safety_snapshot(conf_uid, "before_voice_activation")
        _activate_voice(conf_uid, profile)
        state["active_voice"][conf_uid] = profile_id
        _save_state(state)
        return {"ok": True, "restart_required": True}

    @router.delete("/api/companion/voices/{conf_uid}/{profile_id}")
    async def delete_voice(conf_uid: str, profile_id: str, request: Request):
        if forbidden := _local_only(request):
            return forbidden
        state = _load_state()
        if state["active_voice"].get(conf_uid) == profile_id:
            return JSONResponse(
                {"ok": False, "error": "active voice cannot be deleted"},
                status_code=409,
            )
        profiles = state["voices"].get(conf_uid, [])
        profile = next((p for p in profiles if p.get("id") == profile_id), None)
        if not profile:
            return JSONResponse(
                {"ok": False, "error": "profile not found"}, status_code=404
            )
        backup_manager.create_safety_snapshot(conf_uid, "before_voice_delete")
        path = Path(str(profile.get("ref_audio_path", ""))).resolve()
        voice_root = VOICE_DIR.resolve()
        if path.is_file() and (path == voice_root or voice_root in path.parents):
            path.unlink()
        state["voices"][conf_uid] = [p for p in profiles if p.get("id") != profile_id]
        _save_state(state)
        return {"ok": True}

    @router.get("/api/companion/voices/{conf_uid}/{profile_id}/preview")
    async def preview_voice(
        conf_uid: str,
        profile_id: str,
        request: Request,
        text: str = "你好，很高兴认识你。",
    ):
        if forbidden := _local_only(request):
            return forbidden
        state = _load_state()
        profile = next(
            (p for p in state["voices"].get(conf_uid, []) if p.get("id") == profile_id),
            None,
        )
        if not profile:
            return JSONResponse(
                {"ok": False, "error": "profile not found"}, status_code=404
            )
        if not await runtime_manager.ensure_gpt_sovits():
            return JSONResponse(
                {"ok": False, "error": "GPT-SoVITS could not start"}, status_code=503
            )
        runtime_manager.note_voice_activity()
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                result = await client.get(
                    profile["api_url"],
                    params={
                        "text": text[:500],
                        "text_lang": profile["text_lang"],
                        "ref_audio_path": profile["ref_audio_path"],
                        "prompt_lang": profile["prompt_lang"],
                        "prompt_text": profile["prompt_text"],
                        "text_split_method": "cut5",
                        "batch_size": 1,
                        "media_type": "wav",
                        "streaming_mode": False,
                    },
                )
                result.raise_for_status()
            return Response(result.content, media_type="audio/wav")
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"GPT-SoVITS unavailable: {exc}"},
                status_code=502,
            )

    @router.get("/api/companion/voice-backend/health")
    async def voice_health(
        request: Request, api_url: str = "http://127.0.0.1:9880/tts"
    ):
        if forbidden := _local_only(request):
            return forbidden
        if not _loopback_api(api_url):
            return JSONResponse(
                {"ok": False, "error": "API must be loopback"}, status_code=400
            )
        base = api_url.rsplit("/tts", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(base + "/docs")
            return {"ok": response.status_code < 500, "status": response.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    return router


write_proactive_prompt()
