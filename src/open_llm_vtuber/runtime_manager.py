"""Lifecycle and resource management for local companion services."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "companion_data"
SETTINGS_PATH = DATA_DIR / "runtime.json"
COSY_PID_PATH = DATA_DIR / "cosyvoice.pid"
COSY_LOG_PATH = DATA_DIR / "cosyvoice.log"
COSY_START = ROOT / "integrations" / "cosyvoice" / "start.command"

DEFAULTS: dict[str, Any] = {
    "ollama_model": "qwen2.5:3b",
    "llm_idle_unload_seconds": 600,
    "voice_idle_unload_seconds": 600,
    "stop_services_on_exit": True,
    "semantic_model": "qwen3-embedding:0.6b",
}

_last_activity = time.monotonic()
_last_voice_activity = time.monotonic()
_connected_clients = 0
_monitor_task: asyncio.Task | None = None
_llm_unloaded = False


def get_settings() -> dict[str, Any]:
    settings = dict(DEFAULTS)
    try:
        if SETTINGS_PATH.is_file():
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings.update({key: loaded[key] for key in DEFAULTS if key in loaded})
    except Exception as exc:
        logger.warning(f"[runtime] settings load failed: {exc}")
    settings["llm_idle_unload_seconds"] = max(
        60, min(86400, int(settings["llm_idle_unload_seconds"]))
    )
    settings["voice_idle_unload_seconds"] = max(
        60, min(86400, int(settings["voice_idle_unload_seconds"]))
    )
    return settings


def save_settings(incoming: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    for key in DEFAULTS:
        if key in incoming:
            settings[key] = incoming[key]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS_PATH.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, SETTINGS_PATH)
    return get_settings()


def note_activity() -> None:
    global _last_activity, _llm_unloaded
    _last_activity = time.monotonic()
    _llm_unloaded = False


def note_voice_activity() -> None:
    global _last_voice_activity
    _last_voice_activity = time.monotonic()
    note_activity()


def client_connected() -> None:
    global _connected_clients
    _connected_clients += 1
    note_activity()


def client_disconnected() -> None:
    global _connected_clients
    _connected_clients = max(0, _connected_clients - 1)


async def _get(url: str, timeout: float = 1.0) -> httpx.Response | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url)
    except Exception:
        return None


async def ollama_running() -> bool:
    response = await _get("http://127.0.0.1:11434/api/version")
    return bool(response and response.status_code == 200)


async def cosyvoice_running() -> bool:
    response = await _get("http://127.0.0.1:50000/health", timeout=3.0)
    if not response or response.status_code != 200:
        return False
    try:
        return bool(response.json().get("ok"))
    except Exception:
        return False


async def ensure_ollama(timeout: float = 30.0) -> bool:
    if await ollama_running():
        return True
    try:
        subprocess.Popen(
            ["open", "-a", "Ollama"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.warning(f"[runtime] could not launch Ollama: {exc}")
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await ollama_running():
            return True
        await asyncio.sleep(1)
    return False


async def unload_ollama_model(model: str | None = None) -> bool:
    settings = get_settings()
    models = (
        [model]
        if model
        else [
            str(settings["ollama_model"]),
            str(settings["semantic_model"]),
        ]
    )
    if not await ollama_running():
        return True
    success = True
    async with httpx.AsyncClient(timeout=15) as client:
        for target in dict.fromkeys(models):
            try:
                is_embedding = target == str(settings["semantic_model"])
                response = await client.post(
                    "http://127.0.0.1:11434/api/embed"
                    if is_embedding
                    else "http://127.0.0.1:11434/api/generate",
                    json={
                        "model": target,
                        "keep_alive": 0,
                        **({"input": ""} if is_embedding else {}),
                    },
                )
                success = success and response.status_code < 400
                if response.status_code < 400:
                    logger.info(f"[runtime] unloaded Ollama model {target}")
            except Exception as exc:
                logger.warning(f"[runtime] model unload failed for {target}: {exc}")
                success = False
    return success


async def ensure_cosyvoice(timeout: float = 240.0) -> bool:
    if await cosyvoice_running():
        note_voice_activity()
        return True
    if not COSY_START.is_file():
        return False
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = COSY_LOG_PATH.open("ab")
    try:
        process = subprocess.Popen(
            ["/bin/zsh", str(COSY_START)],
            cwd=ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        COSY_PID_PATH.write_text(str(process.pid), encoding="utf-8")
    finally:
        log_handle.close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await cosyvoice_running():
            note_voice_activity()
            logger.info("[runtime] CosyVoice started on demand")
            return True
        await asyncio.sleep(2)
    return False


def ensure_cosyvoice_blocking(timeout: float = 240.0) -> bool:
    """Thread-friendly wrapper used by the synchronous CosyVoice client."""
    return asyncio.run(ensure_cosyvoice(timeout))


async def stop_cosyvoice() -> bool:
    if await cosyvoice_running():
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post("http://127.0.0.1:50000/shutdown")
        except Exception:
            pass
        for _ in range(20):
            if not await cosyvoice_running():
                COSY_PID_PATH.unlink(missing_ok=True)
                return True
            await asyncio.sleep(0.5)
    try:
        pid = int(COSY_PID_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        pid = None
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning(f"[runtime] CosyVoice stop failed: {exc}")
    COSY_PID_PATH.unlink(missing_ok=True)
    return not await cosyvoice_running()


async def stop_ollama() -> bool:
    await unload_ollama_model()
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Ollama" to quit'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    await asyncio.sleep(1)
    if await ollama_running():
        subprocess.run(
            ["pkill", "-TERM", "-f", "/Applications/Ollama.app/Contents/"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(1)
    return not await ollama_running()


async def status() -> dict[str, Any]:
    ollama = await ollama_running()
    cosy = await cosyvoice_running()
    loaded_models: list[str] = []
    if ollama:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get("http://127.0.0.1:11434/api/ps")
            loaded_models = [
                item.get("name", "") for item in response.json().get("models", [])
            ]
        except Exception:
            pass
    return {
        "ollama": ollama,
        "cosyvoice": cosy,
        "loaded_models": loaded_models,
        "connected_clients": _connected_clients,
        "settings": get_settings(),
    }


async def _monitor_loop() -> None:
    global _llm_unloaded
    while True:
        try:
            await asyncio.sleep(30)
            settings = get_settings()
            now = time.monotonic()
            if (
                not _llm_unloaded
                and now - _last_activity >= settings["llm_idle_unload_seconds"]
            ):
                _llm_unloaded = await unload_ollama_model()
            if (
                await cosyvoice_running()
                and now - _last_voice_activity >= settings["voice_idle_unload_seconds"]
                and _connected_clients == 0
            ):
                await stop_cosyvoice()
                logger.info("[runtime] stopped idle voice service")
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(f"[runtime] monitor cycle failed: {exc}")


def start_monitor() -> None:
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_monitor_loop())


async def stop_monitor() -> None:
    global _monitor_task
    if _monitor_task:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
        _monitor_task = None
