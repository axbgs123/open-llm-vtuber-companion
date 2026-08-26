"""Optional Ollama-backed semantic history index with local SQLite storage."""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import os
import sqlite3
from array import array
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from . import memory_fts, runtime_manager
from .utils.path_safety import safe_join

CHAT_HISTORY_DIR = "chat_history"


def _db_path(conf_uid: str) -> str:
    directory = safe_join(CHAT_HISTORY_DIR, conf_uid)
    return os.path.join(directory, "semantic_index.db")


def index_exists(conf_uid: str) -> bool:
    try:
        path = _db_path(conf_uid)
        if not os.path.isfile(path):
            return False
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT count(*) FROM vectors").fetchone()
            return bool(row and row[0])
        finally:
            conn.close()
    except Exception:
        return False


async def model_installed(model: str | None = None) -> bool:
    model = model or str(runtime_manager.get_settings()["semantic_model"])
    if not await runtime_manager.ollama_running():
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get("http://127.0.0.1:11434/api/tags")
        names = {item.get("name", "") for item in response.json().get("models", [])}
        return model in names or f"{model}:latest" in names
    except Exception:
        return False


async def install_model(model: str | None = None) -> bool:
    model = model or str(runtime_manager.get_settings()["semantic_model"])
    if not await runtime_manager.ensure_ollama():
        return False
    if await model_installed(model):
        return True
    process = await asyncio.create_subprocess_exec(
        "ollama",
        "pull",
        model,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    await process.communicate()
    return process.returncode == 0 and await model_installed(model)


async def _embed(texts: list[str], model: str) -> list[list[float]]:
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            "http://127.0.0.1:11434/api/embed",
            json={"model": model, "input": texts, "keep_alive": "5m"},
        )
        response.raise_for_status()
    return response.json().get("embeddings", [])


def _pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)


async def rebuild_index(conf_uid: str, model: str | None = None) -> int:
    model = model or str(runtime_manager.get_settings()["semantic_model"])
    if not await model_installed(model):
        raise RuntimeError(f"semantic model not installed: {model}")
    rows: list[tuple[str, str, str, str, int]] = []
    for source in memory_fts._list_transcripts(conf_uid):
        for message_index, (text, role, timestamp) in enumerate(
            memory_fts._parse_transcript(source)
        ):
            rows.append((text, role, timestamp, source, message_index))
    vectors: list[list[float]] = []
    for offset in range(0, len(rows), 16):
        vectors.extend(
            await _embed([row[0] for row in rows[offset : offset + 16]], model)
        )
    if len(vectors) != len(rows):
        raise RuntimeError("embedding count mismatch")
    path = _db_path(conf_uid)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP TABLE IF EXISTS vectors")
        conn.execute(
            "CREATE TABLE vectors("
            "id INTEGER PRIMARY KEY, text TEXT, role TEXT, ts TEXT, src TEXT, "
            "message_index INTEGER, model TEXT, vector BLOB)"
        )
        conn.executemany(
            "INSERT INTO vectors(text, role, ts, src, message_index, model, vector) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(*row, model, _pack(vector)) for row, vector in zip(rows, vectors)],
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"[semantic] indexed {len(rows)} messages for {conf_uid} with {model}")
    return len(rows)


async def append_turn(
    conf_uid: str,
    history_uid: str | None,
    user_text: str,
    ai_text: str,
) -> None:
    """Incrementally append a completed turn when a semantic index already exists."""
    path = _db_path(conf_uid)
    if not history_uid or not os.path.isfile(path):
        return
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT model FROM vectors LIMIT 1").fetchone()
    finally:
        conn.close()
    if not row:
        return
    model = str(row[0])
    texts = [user_text.strip(), ai_text.strip()]
    vectors = await _embed(texts, model)
    if len(vectors) != 2:
        return
    source = os.path.join("chat_history", conf_uid, f"{history_uid}.json")
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    conn = sqlite3.connect(path)
    try:
        for index, (text, role, vector) in enumerate(
            zip(texts, ("human", "ai"), vectors)
        ):
            exists = conn.execute(
                "SELECT 1 FROM vectors WHERE src=? AND role=? AND text=? LIMIT 1",
                (source, role, text),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO vectors(text, role, ts, src, message_index, model, vector) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (text, role, timestamp, source, -2 + index, model, _pack(vector)),
                )
        conn.commit()
    finally:
        conn.close()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else -1.0


async def search(conf_uid: str, query: str, k: int = 5) -> list[dict[str, Any]]:
    path = _db_path(conf_uid)
    if not os.path.isfile(path):
        return []
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT model FROM vectors LIMIT 1").fetchone()
        if not row:
            return []
        model = str(row[0])
        query_vectors = await _embed([query], model)
        if not query_vectors:
            return []
        query_vector = query_vectors[0]
        rows = conn.execute(
            "SELECT text, role, ts, src, message_index, vector FROM vectors"
        ).fetchall()
    finally:
        conn.close()
    scored = []
    for text, role, timestamp, source, message_index, blob in rows:
        score = _cosine(query_vector, _unpack(blob))
        scored.append(
            {
                "text": str(text),
                "role": str(role),
                "timestamp": str(timestamp or ""),
                "source": str(source),
                "history_uid": os.path.splitext(os.path.basename(str(source)))[0],
                "message_index": int(message_index),
                "score": score,
                "method": "semantic",
                "snippet": memory_fts._format_snippet(str(text), str(role)),
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[: max(1, min(20, int(k)))]


async def hybrid_search(conf_uid: str, query: str, k: int = 5) -> list[dict[str, Any]]:
    keyword = memory_fts.search_detailed(conf_uid, query, max(k * 2, 8))
    try:
        semantic = await search(conf_uid, query, max(k * 2, 8))
    except Exception as exc:
        logger.warning(f"[semantic] search failed, using keyword only: {exc}")
        semantic = []
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in keyword:
        key = (item["history_uid"], item["text"])
        merged[key] = {
            **item,
            "hybrid_score": 0.4 * float(item["score"]),
            "methods": ["keyword"],
        }
    for item in semantic:
        key = (item["history_uid"], item["text"])
        semantic_score = max(0.0, min(1.0, (float(item["score"]) + 1.0) / 2.0))
        if key in merged:
            merged[key]["hybrid_score"] += 0.6 * semantic_score
            merged[key]["methods"].append("semantic")
        else:
            merged[key] = {
                **item,
                "hybrid_score": 0.6 * semantic_score,
                "methods": ["semantic"],
            }
    results = sorted(
        merged.values(), key=lambda item: item["hybrid_score"], reverse=True
    )
    return results[: max(1, min(20, int(k)))]
