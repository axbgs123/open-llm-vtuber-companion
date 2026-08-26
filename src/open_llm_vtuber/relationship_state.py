"""Lightweight emotional continuity without pretending to diagnose the user."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

from .utils.path_safety import safe_join

POSITIVE = ("开心", "高兴", "顺利", "喜欢", "谢谢", "太好了", "興奮", "期待")
NEGATIVE = (
    "不开心",
    "不喜欢",
    "难过",
    "烦",
    "累",
    "焦虑",
    "生气",
    "失望",
    "压力",
    "討厭",
    "讨厌",
    "難過",
)


def _path(conf_uid: str) -> Path:
    return Path(safe_join("chat_history", conf_uid, "relationship_state.json"))


def load(conf_uid: str) -> dict[str, Any]:
    try:
        data = json.loads(_path(conf_uid).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"turns": 0, "warmth": 50, "engagement": 50, "recent_moods": []}


def _save(conf_uid: str, state: dict[str, Any]) -> None:
    path = _path(conf_uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def update_from_turn(
    conf_uid: str, user_text: str, ai_text: str = ""
) -> dict[str, Any]:
    state = load(conf_uid)
    negative = sum(word in user_text for word in NEGATIVE)
    positive_text = user_text
    for phrase in NEGATIVE:
        positive_text = positive_text.replace(phrase, "")
    positive = sum(word in positive_text for word in POSITIVE)
    mood = "低落" if negative > positive else "轻快" if positive > negative else "平稳"
    state["turns"] = int(state.get("turns", 0)) + 1
    state["warmth"] = max(
        0, min(100, int(state.get("warmth", 50)) + (1 if ai_text else 0))
    )
    length_score = min(4, len(re.sub(r"\s+", "", user_text)) // 30)
    state["engagement"] = max(
        0, min(100, int(state.get("engagement", 50)) - 1 + length_score)
    )
    state.setdefault("recent_moods", []).append(
        {
            "mood": mood,
            "excerpt": user_text[:80],
            "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
    state["recent_moods"] = state["recent_moods"][-8:]
    _save(conf_uid, state)
    return state


def prompt_context(conf_uid: str) -> str:
    state = load(conf_uid)
    recent = state.get("recent_moods", [])
    if not recent:
        return ""
    mood = recent[-1].get("mood", "平稳")
    return (
        "\n\n【关系与情绪连续性】你们已经持续对话 "
        f"{state.get('turns', 0)} 轮；对方最近的表达状态偏{mood}。"
        "自然承接，但不要声称你能诊断情绪，也不要直接报出这些数值。"
    )
