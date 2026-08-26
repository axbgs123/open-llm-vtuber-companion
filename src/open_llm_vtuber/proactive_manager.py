"""Proactive-chat policy: quiet hours, dedupe, daily limits and adaptive cadence."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "companion_data" / "proactive_runtime.json"


def _load() -> dict[str, Any]:
    try:
        if STATE_PATH.is_file():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.warning(f"[proactive] runtime state load failed: {exc}")
    return {"characters": {}}


def _save(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, STATE_PATH)


def _entry(state: dict[str, Any], conf_uid: str) -> dict[str, Any]:
    return state.setdefault("characters", {}).setdefault(
        conf_uid,
        {
            "date": dt.date.today().isoformat(),
            "daily_count": 0,
            "recent_topics": [],
            "unanswered_count": 0,
            "response_streak": 0,
            "awaiting_response": False,
            "last_trigger": "",
        },
    )


def _roll_date(entry: dict[str, Any]) -> None:
    today = dt.date.today().isoformat()
    if entry.get("date") != today:
        entry.update({"date": today, "daily_count": 0})


def _minutes(value: str, fallback: int) -> int:
    try:
        hour, minute = str(value).split(":", 1)
        return max(0, min(1439, int(hour) * 60 + int(minute)))
    except Exception:
        return fallback


def in_quiet_hours(settings: dict[str, Any], now: dt.datetime | None = None) -> bool:
    quiet = settings.get("quiet_hours", {})
    if not quiet.get("enabled", True):
        return False
    now = now or dt.datetime.now().astimezone()
    current = now.hour * 60 + now.minute
    start = _minutes(quiet.get("start", "23:00"), 23 * 60)
    end = _minutes(quiet.get("end", "08:00"), 8 * 60)
    if start == end:
        return True
    return start <= current < end if start < end else current >= start or current < end


def effective_idle_seconds(conf_uid: str, settings: dict[str, Any]) -> int:
    base = int(settings.get("idle_seconds", 120))
    if not settings.get("adaptive_enabled", True):
        return base
    state = _load()
    entry = _entry(state, conf_uid)
    unanswered = max(0, int(entry.get("unanswered_count", 0)))
    response_streak = max(0, int(entry.get("response_streak", 0)))
    multiplier = min(4.0, 1.0 + unanswered * 0.6)
    if response_streak >= 3:
        multiplier *= 0.8
    return max(30, int(base * multiplier))


def can_trigger(conf_uid: str, settings: dict[str, Any]) -> tuple[bool, str]:
    if not settings.get("enabled", True):
        return False, "disabled"
    if in_quiet_hours(settings):
        return False, "quiet_hours"
    state = _load()
    entry = _entry(state, conf_uid)
    _roll_date(entry)
    if int(entry.get("daily_count", 0)) >= int(settings.get("daily_limit", 6)):
        _save(state)
        return False, "daily_limit"
    _save(state)
    return True, "ok"


def choose_topic(conf_uid: str, settings: dict[str, Any]) -> str:
    try:
        from .commitment_manager import due_topic

        commitment = due_topic(conf_uid)
        if commitment:
            return commitment
    except Exception:
        pass
    topics = [
        str(topic).strip() for topic in settings.get("topics", []) if str(topic).strip()
    ]
    if not topics:
        return "关心对方最近过得怎么样"
    state = _load()
    entry = _entry(state, conf_uid)
    window = max(1, min(10, int(settings.get("recent_topic_window", 4))))
    recent = entry.get("recent_topics", [])[-window:]
    available = [
        topic for topic in topics if not any(topic in str(item) for item in recent)
    ]
    if not available:
        available = topics
    index = int(entry.get("daily_count", 0)) % len(available)
    selected = available[index]
    try:
        from .relationship_state import load as load_relationship

        recent_moods = load_relationship(conf_uid).get("recent_moods", [])
        if recent_moods and recent_moods[-1].get("mood") == "低落":
            return f"温和承接对方上次偏低落的状态，再自然聊到：{selected}"
    except Exception:
        pass
    return selected


def record_trigger(conf_uid: str, topic: str) -> None:
    state = _load()
    entry = _entry(state, conf_uid)
    _roll_date(entry)
    if entry.get("awaiting_response"):
        entry["unanswered_count"] = int(entry.get("unanswered_count", 0)) + 1
        entry["response_streak"] = 0
    entry["awaiting_response"] = True
    entry["daily_count"] = int(entry.get("daily_count", 0)) + 1
    entry.setdefault("recent_topics", []).append(topic)
    entry["recent_topics"] = entry["recent_topics"][-10:]
    entry["last_trigger"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    _save(state)


def record_human_response(conf_uid: str) -> None:
    state = _load()
    entry = _entry(state, conf_uid)
    if entry.get("awaiting_response"):
        entry["awaiting_response"] = False
        entry["unanswered_count"] = max(0, int(entry.get("unanswered_count", 0)) - 1)
        entry["response_streak"] = int(entry.get("response_streak", 0)) + 1
        _save(state)


def get_runtime(conf_uid: str) -> dict[str, Any]:
    state = _load()
    entry = _entry(state, conf_uid)
    _roll_date(entry)
    _save(state)
    return entry
