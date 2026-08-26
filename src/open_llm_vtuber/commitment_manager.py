"""Per-character reminders and promises extracted from ordinary conversation."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .utils.path_safety import safe_join


def _path(conf_uid: str) -> Path:
    return Path(safe_join("chat_history", conf_uid, "commitments.json"))


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def load(conf_uid: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(_path(conf_uid).read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def save(conf_uid: str, rows: list[dict[str, Any]]) -> None:
    path = _path(conf_uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def parse_due(text: str, now: dt.datetime | None = None) -> str:
    now = now or _now()
    day = now.date()
    explicit_day = False
    weekday_match = re.search(r"下(?:周|週)(?P<weekday>[一二三四五六日天])", text)
    if weekday_match:
        weekday = "一二三四五六日天".index(weekday_match.group("weekday"))
        if weekday >= 7:
            weekday = 6
        next_monday = day + dt.timedelta(days=7 - day.weekday())
        day = next_monday + dt.timedelta(days=weekday)
        explicit_day = True
    elif "后天" in text or "後天" in text:
        day += dt.timedelta(days=2)
        explicit_day = True
    elif "明天" in text:
        day += dt.timedelta(days=1)
        explicit_day = True
    elif "下周" in text or "下週" in text:
        day += dt.timedelta(days=7)
        explicit_day = True
    elif "周末" in text or "週末" in text:
        day += dt.timedelta(days=(5 - day.weekday()) % 7 or 7)
        explicit_day = True
    is_today = any(word in text for word in ("今天", "今日", "今晚", "今早"))
    if is_today:
        explicit_day = True
    match = re.search(
        r"(?P<h>\d{1,2})(?:[:：点時时])(?:(?P<half>半)|(?P<m>\d{1,2})分?)?",
        text,
    )
    default_hour = (
        20 if "今晚" in text else 9 if "今早" in text else 18 if is_today else 9
    )
    hour = max(0, min(23, int(match.group("h")))) if match else default_hour
    minute = 30 if match and match.group("half") else 0
    if match and match.group("m"):
        minute = max(0, min(59, int(match.group("m"))))
    if any(word in text for word in ("下午", "晚上", "今晚", "傍晚")) and hour < 12:
        hour += 12
    elif "中午" in text and hour < 11:
        hour += 12
    elif "凌晨" in text and hour == 12:
        hour = 0
    due = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=now.tzinfo)
    if due <= now and is_today and match is None:
        due = min(
            now + dt.timedelta(hours=1),
            dt.datetime.combine(day, dt.time(23, 59), tzinfo=now.tzinfo),
        )
    if due <= now and not explicit_day:
        due += dt.timedelta(days=1)
    return due.isoformat(timespec="minutes")


def extract_from_turn(conf_uid: str, text: str) -> list[dict[str, Any]]:
    patterns = [
        r"(?:提醒我|记得提醒我|記得提醒我)(?P<what>[^。！？]{2,80})",
        r"(?P<when>今天|明天|后天|後天|下周|下週|周末|週末)(?:我)?(?:要|得|准备|準備)(?P<what>[^。！？]{2,80})",
    ]
    rows = load(conf_uid)
    created = []
    for pattern in patterns:
        for match in re.finditer(pattern, text or ""):
            content = (match.groupdict().get("what") or match.group(0)).strip(" ，,")
            due = parse_due(match.group(0))
            fingerprint = hashlib.sha256(f"{content}\0{due}".encode()).hexdigest()[:16]
            if any(row.get("id") == fingerprint for row in rows):
                continue
            row = {
                "id": fingerprint,
                "content": content,
                "due_at": due,
                "status": "open",
                "created_at": _now().isoformat(timespec="seconds"),
                "source_excerpt": match.group(0),
            }
            rows.append(row)
            created.append(row)
    if created:
        save(conf_uid, rows)
    return created


def list_items(conf_uid: str, *, include_done: bool = True) -> list[dict[str, Any]]:
    rows = load(conf_uid)
    if not include_done:
        rows = [row for row in rows if row.get("status") == "open"]
    return sorted(rows, key=lambda row: str(row.get("due_at", "")))


def create(conf_uid: str, content: str, due_at: str) -> dict[str, Any]:
    content = str(content).strip()[:120]
    if not content:
        raise ValueError("commitment content is required")
    try:
        due = dt.datetime.fromisoformat(str(due_at))
        if due.tzinfo is None:
            due = due.astimezone()
    except Exception as exc:
        raise ValueError("invalid due time") from exc
    rows = load(conf_uid)
    fingerprint = hashlib.sha256(f"{content}\0{due.isoformat()}".encode()).hexdigest()[
        :16
    ]
    row = {
        "id": fingerprint,
        "content": content,
        "due_at": due.isoformat(timespec="minutes"),
        "status": "open",
        "created_at": _now().isoformat(timespec="seconds"),
        "source_excerpt": "控制台手动添加",
    }
    rows.append(row)
    save(conf_uid, rows)
    return row


def update(conf_uid: str, item_id: str, status: str) -> list[dict[str, Any]]:
    if status not in {"open", "done", "dismissed"}:
        raise ValueError("invalid commitment status")
    rows = load(conf_uid)
    target = next((row for row in rows if row.get("id") == item_id), None)
    if not target:
        raise ValueError("commitment not found")
    target["status"] = status
    target["updated_at"] = _now().isoformat(timespec="seconds")
    save(conf_uid, rows)
    return rows


def due_topic(conf_uid: str, within_hours: int = 24) -> str:
    now = _now()
    horizon = now + dt.timedelta(hours=within_hours)
    rows = load(conf_uid)
    for row in sorted(rows, key=lambda item: str(item.get("due_at", ""))):
        if row.get("status") != "open":
            continue
        try:
            due = dt.datetime.fromisoformat(str(row.get("due_at")))
        except Exception:
            continue
        try:
            last_prompted = dt.datetime.fromisoformat(str(row.get("last_prompted_at")))
        except Exception:
            last_prompted = None
        if last_prompted and now - last_prompted < dt.timedelta(hours=12):
            continue
        if now - dt.timedelta(hours=2) <= due <= horizon:
            row["last_prompted_at"] = now.isoformat(timespec="seconds")
            save(conf_uid, rows)
            return f"自然地关心这件即将到期的事：{row.get('content')}。不要说你在执行提醒任务"
    return ""
