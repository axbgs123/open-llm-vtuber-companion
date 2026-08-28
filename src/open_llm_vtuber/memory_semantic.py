"""Optional Ollama-backed semantic history index with local SQLite storage."""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import os
import re
import sqlite3
from array import array
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from loguru import logger

from . import memory_fts, runtime_manager
from .utils.path_safety import safe_join

CHAT_HISTORY_DIR = "chat_history"
_INDEX_CACHE: dict[
    str,
    tuple[
        tuple[int, int],
        str,
        list[tuple[str, str, str, str, int]],
        np.ndarray,
    ],
] = {}


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


def _invalidate_index(path: str) -> None:
    _INDEX_CACHE.pop(os.path.abspath(path), None)


def _load_index(
    path: str,
) -> tuple[str, list[tuple[str, str, str, str, int]], np.ndarray]:
    """Load and normalize an index once; reuse it until the DB changes."""
    absolute = os.path.abspath(path)
    stat = os.stat(absolute)
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _INDEX_CACHE.get(absolute)
    if cached and cached[0] == signature:
        return cached[1], cached[2], cached[3]

    conn = sqlite3.connect(absolute)
    try:
        rows = conn.execute(
            "SELECT text, role, ts, src, message_index, model, vector FROM vectors"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "", [], np.empty((0, 0), dtype=np.float32)

    dimensions = len(rows[0][6]) // 4
    valid = [row for row in rows if len(row[6]) == dimensions * 4]
    metadata = [
        (str(text), str(role), str(ts or ""), str(src), int(message_index))
        for text, role, ts, src, message_index, _model, _vector in valid
    ]
    matrix = np.vstack(
        [np.frombuffer(row[6], dtype=np.float32) for row in valid]
    ).astype(np.float32, copy=False)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, norms, out=matrix, where=norms != 0)
    model = str(valid[0][5])
    _INDEX_CACHE[absolute] = (signature, model, metadata, matrix)
    return model, metadata, matrix


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
    _invalidate_index(path)
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
    _invalidate_index(path)


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
    model, rows, matrix = await asyncio.to_thread(_load_index, path)
    if not model or not rows:
        return []
    query_vectors = await _embed([query], model)
    if not query_vectors:
        return []
    query_vector = np.asarray(query_vectors[0], dtype=np.float32)
    norm = float(np.linalg.norm(query_vector))
    if not norm or matrix.shape[1] != query_vector.size:
        return []
    scores = matrix @ (query_vector / norm)
    limit = max(1, min(20, int(k), len(rows)))
    if limit < len(rows):
        candidates = np.argpartition(scores, -limit)[-limit:]
        indices = candidates[np.argsort(scores[candidates])[::-1]]
    else:
        indices = np.argsort(scores)[::-1]
    scored = []
    for index in indices:
        text, role, timestamp, source, message_index = rows[int(index)]
        score = float(scores[int(index)])
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
    return scored


async def hybrid_search(conf_uid: str, query: str, k: int = 5) -> list[dict[str, Any]]:
    started = __import__("time").perf_counter()
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
    # A small deterministic reranker: direct lexical evidence and relative-time
    # intent should beat a vaguely similar old message.
    query_terms = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]{2,}|[\u3400-\u9fff]{2,}", query)
    }
    wants_recent = any(
        word in query for word in ("最近", "刚才", "今天", "上次", "近来")
    )
    now = dt.datetime.now().astimezone()
    for item in merged.values():
        text = str(item.get("text", "")).lower()
        overlap = sum(term in text for term in query_terms)
        item["hybrid_score"] += min(0.12, overlap * 0.03)
        if wants_recent:
            try:
                timestamp = dt.datetime.fromisoformat(str(item.get("timestamp", "")))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.astimezone()
                age_days = max(0.0, (now - timestamp).total_seconds() / 86400)
                item["hybrid_score"] += 0.12 / (1.0 + age_days / 7.0)
                item.setdefault("methods", []).append("recency")
            except Exception:
                pass
    results = sorted(
        merged.values(), key=lambda item: item["hybrid_score"], reverse=True
    )
    from .companion_diagnostics import record

    record("memory_hybrid_search", (__import__("time").perf_counter() - started) * 1000)
    return results[: max(1, min(20, int(k)))]
