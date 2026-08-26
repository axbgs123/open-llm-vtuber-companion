"""Small, local-only performance journal for companion features."""

from __future__ import annotations

import json
import os
import resource
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "companion_data"
PATH = DATA_DIR / "diagnostics.json"
MAX_EVENTS = 300
_lock = threading.RLock()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"events": []}
    except Exception:
        return {"events": []}


def _save(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = PATH.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, PATH)


def record(
    kind: str, duration_ms: float, *, ok: bool = True, cached: bool = False
) -> None:
    try:
        with _lock:
            data = _load()
            events = data.setdefault("events", [])
            events.append(
                {
                    "kind": str(kind)[:50],
                    "duration_ms": round(max(0.0, float(duration_ms)), 1),
                    "ok": bool(ok),
                    "cached": bool(cached),
                    "at": time.time(),
                }
            )
            data["events"] = events[-MAX_EVENTS:]
            _save(data)
    except Exception as exc:
        # Observability must never break a successful conversation or TTS task.
        logger.warning(f"[diagnostics] event dropped: {exc}")


@contextmanager
def timer(kind: str) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"ok": True, "cached": False}
    started = time.perf_counter()
    try:
        yield state
    except Exception:
        state["ok"] = False
        raise
    finally:
        record(kind, (time.perf_counter() - started) * 1000, **state)


def summary() -> dict[str, Any]:
    with _lock:
        events = _load().get("events", [])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get("kind", "other"))].append(event)
    metrics = []
    for kind, rows in sorted(grouped.items()):
        durations = sorted(float(row.get("duration_ms", 0)) for row in rows)
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
        metrics.append(
            {
                "kind": kind,
                "count": len(rows),
                "average_ms": round(sum(durations) / len(durations), 1),
                "p95_ms": round(p95, 1),
                "errors": sum(not bool(row.get("ok", True)) for row in rows),
                "cache_hits": sum(bool(row.get("cached")) for row in rows),
            }
        )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    rss_bytes = int(rss if os.uname().sysname == "Darwin" else rss * 1024)
    return {"events": len(events), "metrics": metrics, "process_peak_rss": rss_bytes}


def clear() -> None:
    with _lock:
        _save({"events": []})
