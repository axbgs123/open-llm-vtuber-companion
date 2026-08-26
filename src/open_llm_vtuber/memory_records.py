"""Structured, attributable memory records with deterministic conflict detection."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

from .utils.path_safety import safe_join

CHAT_HISTORY_DIR = "chat_history"


def _dir(conf_uid: str) -> Path:
    return Path(safe_join(CHAT_HISTORY_DIR, conf_uid))


def _path(conf_uid: str) -> Path:
    return _dir(conf_uid) / "memory_records.json"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _id(key: str, value: str, created: str) -> str:
    return hashlib.sha256(f"{key}\0{value}\0{created}".encode()).hexdigest()[:16]


def load_records(conf_uid: str) -> list[dict[str, Any]]:
    try:
        path = _path(conf_uid)
        if not path.is_file():
            legacy = _dir(conf_uid) / "core_memory.md"
            if not legacy.is_file():
                return []
            source = _source(None, "从旧版 core_memory.md 迁移", "legacy")
            records = []
            for line in legacy.read_text(encoding="utf-8").splitlines():
                fact = line.strip().lstrip("- ").strip()
                if fact:
                    key = "legacy:" + hashlib.sha256(fact.encode()).hexdigest()[:12]
                    records.append(
                        _new_record(key, fact, fact, "迁移", source, confidence=0.7)
                    )
            if records:
                save_records(conf_uid, records)
            return records
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning(f"[memory-records] load failed for {conf_uid}: {exc}")
        return []


def save_records(conf_uid: str, records: list[dict[str, Any]]) -> None:
    directory = _dir(conf_uid)
    directory.mkdir(parents=True, exist_ok=True)
    path = _path(conf_uid)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def _clean_value(value: str) -> str:
    return re.split(r"[。！？，,;；\n]", value.strip())[0].strip(" 的了呢吧呀")[:80]


def extract_claims(user_input: str) -> list[dict[str, str]]:
    """Extract high-confidence user claims that have stable conflict keys."""
    text = re.sub(r"\s+", " ", user_input or "").strip()
    patterns = [
        (
            r"(?:请|以後|以后)?叫我(?P<v>[\w\u3400-\u9fff·]{1,20})",
            "identity.preferred_name",
            "称呼",
        ),
        (r"我叫(?P<v>[\w\u3400-\u9fff·]{1,20})", "identity.name", "身份"),
        (r"我(?:现在|目前)?住在(?P<v>[^，。！？]{1,40})", "identity.location", "身份"),
        (
            r"我(?:的职业是|的職業是|是做)(?P<v>[^，。！？]{1,40})",
            "identity.occupation",
            "身份",
        ),
    ]
    claims: list[dict[str, str]] = []
    for pattern, key, category in patterns:
        match = re.search(pattern, text)
        if match:
            value = _clean_value(match.group("v"))
            if value:
                claims.append(
                    {
                        "key": key,
                        "value": value,
                        "category": category,
                        "fact": match.group(0),
                    }
                )

    preference_patterns = [
        (r"我(?:不喜欢|不喜歡|讨厌|討厭)(?P<v>[^，。！？]{1,40})", "不喜欢"),
        (r"我(?:很)?(?:喜欢|喜歡|爱|愛)(?P<v>[^，。！？]{1,40})", "喜欢"),
    ]
    for pattern, stance in preference_patterns:
        for match in re.finditer(pattern, text):
            target = _clean_value(match.group("v"))
            if target:
                claims.append(
                    {
                        "key": f"preference:{target}",
                        "value": stance,
                        "category": "偏好",
                        "fact": f"用户{stance}{target}",
                    }
                )
    unique: dict[str, dict[str, str]] = {}
    for claim in claims:
        unique[claim["key"]] = claim
    return list(unique.values())


def _source(
    history_uid: str | None, excerpt: str, kind: str = "conversation"
) -> dict[str, str]:
    return {
        "kind": kind,
        "history_uid": history_uid or "",
        "excerpt": excerpt[:300],
        "timestamp": _now(),
    }


def _new_record(
    key: str,
    value: str,
    fact: str,
    category: str,
    source: dict[str, str],
    *,
    status: str = "active",
    conflict_with: str = "",
    confidence: float = 0.9,
) -> dict[str, Any]:
    created = _now()
    return {
        "id": _id(key, value, created),
        "key": key,
        "value": value,
        "fact": fact.strip().lstrip("- "),
        "category": category,
        "status": status,
        "confidence": confidence,
        "conflict_with": conflict_with,
        "created_at": created,
        "updated_at": created,
        "sources": [source],
    }


def update_from_turn(
    conf_uid: str,
    user_input: str,
    memory_text: str,
    history_uid: str | None = None,
) -> list[dict[str, Any]]:
    records = load_records(conf_uid)
    source = _source(history_uid, user_input)

    claims = extract_claims(user_input)
    for claim in claims:
        same = next(
            (
                record
                for record in records
                if record.get("key") == claim["key"]
                and record.get("value") == claim["value"]
                and record.get("status") == "active"
            ),
            None,
        )
        if same:
            same.setdefault("sources", []).append(source)
            same["updated_at"] = _now()
            same["confidence"] = min(1.0, float(same.get("confidence", 0.8)) + 0.05)
            continue
        conflict = next(
            (
                record
                for record in records
                if record.get("key") == claim["key"]
                and record.get("status") == "active"
                and record.get("value") != claim["value"]
            ),
            None,
        )
        records.append(
            _new_record(
                claim["key"],
                claim["value"],
                claim["fact"],
                claim["category"],
                source,
                status="pending_conflict" if conflict else "active",
                conflict_with=str(conflict.get("id", "")) if conflict else "",
            )
        )

    known_facts = {str(record.get("fact", "")).strip() for record in records}
    structured_values = {claim["value"] for claim in claims}
    for line in memory_text.splitlines():
        fact = line.strip().lstrip("- ").strip()
        if (
            not fact
            or fact in known_facts
            or fact.startswith(("用户说：", "使用者說："))
        ):
            continue
        if any(value and value in fact for value in structured_values):
            continue
        key = "note:" + hashlib.sha256(fact.encode()).hexdigest()[:12]
        records.append(_new_record(key, fact, fact, "其他", source, confidence=0.7))
        known_facts.add(fact)

    save_records(conf_uid, records)
    return records


def active_memory_text(conf_uid: str, cap: int = 1500) -> str:
    active = [
        record for record in load_records(conf_uid) if record.get("status") == "active"
    ]
    active.sort(key=lambda item: (item.get("category", ""), item.get("updated_at", "")))
    lines: list[str] = []
    length = 0
    for record in active:
        line = "- " + str(record.get("fact", "")).strip().lstrip("- ")
        if len(line) + length > cap:
            break
        lines.append(line)
        length += len(line) + 1
    return "\n".join(lines)


def resolve_conflict(conf_uid: str, winner_id: str) -> list[dict[str, Any]]:
    records = load_records(conf_uid)
    winner = next((record for record in records if record.get("id") == winner_id), None)
    if not winner:
        raise ValueError("memory record not found")
    key = winner.get("key")
    for record in records:
        if record.get("key") != key:
            continue
        if record.get("id") == winner_id:
            record["status"] = "active"
            record["conflict_with"] = ""
        elif record.get("status") in {"active", "pending_conflict"}:
            record["status"] = "superseded"
        record["updated_at"] = _now()
    save_records(conf_uid, records)
    return records


def replace_manual_memory(conf_uid: str, content: str) -> list[dict[str, Any]]:
    records = [
        record for record in load_records(conf_uid) if record.get("status") != "active"
    ]
    source = _source(None, content, "manual")
    for line in content.splitlines():
        fact = line.strip().lstrip("- ").strip()
        if fact:
            key = "manual:" + hashlib.sha256(fact.encode()).hexdigest()[:12]
            records.append(_new_record(key, fact, fact, "手动", source, confidence=1.0))
    save_records(conf_uid, records)
    return records


def forget_active_records(conf_uid: str) -> list[dict[str, Any]]:
    records = load_records(conf_uid)
    for record in records:
        if record.get("status") in {"active", "pending_conflict"}:
            record["status"] = "forgotten"
            record["updated_at"] = _now()
    save_records(conf_uid, records)
    return records
