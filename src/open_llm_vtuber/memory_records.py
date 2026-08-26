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
                        _new_record(
                            key,
                            fact,
                            fact,
                            "迁移",
                            source,
                            status="pending_confirmation",
                            confidence=0.7,
                            importance=2,
                            needs_confirmation=True,
                        )
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


def _expiry(days: int) -> str:
    return (dt.datetime.now().astimezone() + dt.timedelta(days=days)).isoformat(
        timespec="seconds"
    )


def extract_claims(user_input: str) -> list[dict[str, Any]]:
    """Extract high-confidence user claims that have stable conflict keys."""
    text = re.sub(r"\s+", " ", user_input or "").strip()
    patterns = [
        (
            r"(?:请|以後|以后)?叫我(?P<v>[\w\u3400-\u9fff·]{1,20})",
            "identity.preferred_name",
            "称呼",
            5,
        ),
        (
            r"我叫(?P<v>[\w\u3400-\u9fff·]{1,20})",
            "identity.name",
            "身份",
            5,
        ),
        (
            r"我(?:现在|目前)?住在(?P<v>[^，。！？]{1,40})",
            "identity.location",
            "身份",
            4,
        ),
        (
            r"我(?:的职业是|的職業是|是做)(?P<v>[^，。！？]{1,40})",
            "identity.occupation",
            "身份",
            4,
        ),
        (
            r"我(?:的)?生日(?:是|在)(?P<v>[^，。！？]{1,30})",
            "identity.birthday",
            "重要日期",
            5,
        ),
    ]
    claims: list[dict[str, Any]] = []
    for pattern, key, category, importance in patterns:
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
                        "importance": importance,
                        "confidence": 0.95,
                        "expires_at": "",
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
                        "importance": 3,
                        "confidence": 0.9,
                        "expires_at": "",
                    }
                )

    family_pattern = r"我(?:有一个|有一個|有个|有個)?(?P<r>爸爸|妈妈|媽媽|哥哥|姐姐|弟弟|妹妹|丈夫|妻子|男朋友|女朋友)(?:叫(?P<n>[\w\u3400-\u9fff·]{1,20}))?"
    for match in re.finditer(family_pattern, text):
        relation = match.group("r")
        name = match.group("n") or relation
        claims.append(
            {
                "key": f"family:{relation}",
                "value": name,
                "category": "家庭",
                "fact": match.group(0),
                "importance": 4,
                "confidence": 0.9,
                "expires_at": "",
            }
        )

    project_match = re.search(
        r"我(?:最近|目前|正在)?(?:在)?(?:做|开发|開發|写|寫)(?P<v>[^，。！？]{2,60})",
        text,
    )
    if project_match:
        project = _clean_value(project_match.group("v"))
        claims.append(
            {
                "key": "project:current",
                "value": project,
                "category": "项目",
                "fact": f"用户正在做{project}",
                "importance": 4,
                "confidence": 0.85,
                "expires_at": _expiry(90),
            }
        )

    habit_match = re.search(
        r"我(?P<freq>每天|每周|每週|每星期|每月)(?P<v>[^，。！？]{2,50})",
        text,
    )
    if habit_match:
        value = _clean_value(habit_match.group("freq") + habit_match.group("v"))
        claims.append(
            {
                "key": "habit:" + hashlib.sha256(value.encode()).hexdigest()[:10],
                "value": value,
                "category": "习惯",
                "fact": f"用户{value}",
                "importance": 3,
                "confidence": 0.85,
                "expires_at": "",
            }
        )

    plan_match = re.search(
        r"(?P<t>今天|明天|后天|後天|这周|這週|本周|本週|下周|下週)(?:我)?(?:要|准备|準備|打算)(?P<v>[^，。！？]{2,60})",
        text,
    )
    if plan_match:
        plan = plan_match.group("t") + plan_match.group("v")
        days = 2 if plan_match.group("t") in {"今天", "明天", "后天", "後天"} else 14
        claims.append(
            {
                "key": "plan:" + hashlib.sha256(plan.encode()).hexdigest()[:10],
                "value": plan,
                "category": "近期计划",
                "fact": f"用户计划{plan}",
                "importance": 3,
                "confidence": 0.9,
                "expires_at": _expiry(days),
            }
        )

    unique: dict[str, dict[str, Any]] = {}
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
    importance: int = 3,
    expires_at: str = "",
    needs_confirmation: bool = False,
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
        "importance": max(1, min(5, int(importance))),
        "expires_at": expires_at,
        "needs_confirmation": needs_confirmation,
        "last_confirmed_at": created if not needs_confirmation else "",
        "conflict_with": conflict_with,
        "created_at": created,
        "updated_at": created,
        "sources": [source],
    }


def _fact_tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^\w\u3400-\u9fff]", "", text.lower())
    return {normalized[i : i + 2] for i in range(max(0, len(normalized) - 1))}


def _near_duplicate(records: list[dict[str, Any]], fact: str) -> dict[str, Any] | None:
    target = _fact_tokens(fact)
    if not target:
        return None
    for record in records:
        if record.get("status") not in {"active", "pending_confirmation"}:
            continue
        existing = _fact_tokens(str(record.get("fact", "")))
        if existing and len(target & existing) / len(target | existing) >= 0.82:
            return record
    return None


def _is_expired(record: dict[str, Any]) -> bool:
    expires_at = str(record.get("expires_at") or "")
    if not expires_at:
        return False
    try:
        expiry = dt.datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.astimezone()
        return expiry <= dt.datetime.now().astimezone()
    except Exception:
        return False


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
                confidence=float(claim.get("confidence", 0.9)),
                importance=int(claim.get("importance", 3)),
                expires_at=str(claim.get("expires_at", "")),
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
        duplicate = _near_duplicate(records, fact)
        if duplicate:
            duplicate.setdefault("sources", []).append(source)
            duplicate["updated_at"] = _now()
            continue
        key = "note:" + hashlib.sha256(fact.encode()).hexdigest()[:12]
        records.append(
            _new_record(
                key,
                fact,
                fact,
                "其他",
                source,
                status="pending_confirmation",
                confidence=0.7,
                importance=2,
                needs_confirmation=True,
            )
        )
        known_facts.add(fact)

    save_records(conf_uid, records)
    return records


def active_memory_text(conf_uid: str, cap: int = 1500) -> str:
    records = load_records(conf_uid)
    changed = False
    for record in records:
        if record.get("status") == "active" and _is_expired(record):
            record["status"] = "expired"
            record["updated_at"] = _now()
            changed = True
    if changed:
        save_records(conf_uid, records)
    active = [record for record in records if record.get("status") == "active"]
    active.sort(
        key=lambda item: (
            -int(item.get("importance", 3)),
            item.get("category", ""),
            item.get("updated_at", ""),
        )
    )
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
            record["needs_confirmation"] = False
            record["last_confirmed_at"] = _now()
        elif record.get("status") in {"active", "pending_conflict"}:
            record["status"] = "superseded"
        record["updated_at"] = _now()
    save_records(conf_uid, records)
    return records


def confirm_record(
    conf_uid: str, record_id: str, accept: bool = True
) -> list[dict[str, Any]]:
    records = load_records(conf_uid)
    record = next((item for item in records if item.get("id") == record_id), None)
    if not record:
        raise ValueError("memory record not found")
    record["status"] = "active" if accept else "rejected"
    record["needs_confirmation"] = False
    record["last_confirmed_at"] = _now() if accept else ""
    record["updated_at"] = _now()
    save_records(conf_uid, records)
    return records


def update_record(
    conf_uid: str,
    record_id: str,
    *,
    importance: int | None = None,
    expires_at: str | None = None,
) -> list[dict[str, Any]]:
    records = load_records(conf_uid)
    record = next((item for item in records if item.get("id") == record_id), None)
    if not record:
        raise ValueError("memory record not found")
    if importance is not None:
        record["importance"] = max(1, min(5, int(importance)))
    if expires_at is not None:
        if expires_at:
            dt.datetime.fromisoformat(expires_at)
        record["expires_at"] = expires_at
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
        if record.get("status") in {
            "active",
            "pending_conflict",
            "pending_confirmation",
        }:
            record["status"] = "forgotten"
            record["updated_at"] = _now()
    save_records(conf_uid, records)
    return records
