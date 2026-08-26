"""Versioned migrations for companion-owned runtime data."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "companion_data"
SCHEMA_PATH = DATA_DIR / "schema.json"
CURRENT_SCHEMA = 3


def _schema() -> int:
    try:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        return int(data.get("version", 0))
    except Exception:
        return 0


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def _migrate_state() -> None:
    path = DATA_DIR / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    for section in ("memory", "voices", "active_voice"):
        if not isinstance(state.get(section), dict):
            state[section] = {}
    if not isinstance(state.get("proactive"), dict):
        state["proactive"] = {}
    _atomic_json(path, state)


def _migrate_memory_records() -> int:
    changed = 0
    history_root = ROOT / "chat_history"
    if not history_root.is_dir():
        return changed
    for path in history_root.glob("*/memory_records.json"):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                continue
            touched = False
            for record in records:
                if not isinstance(record, dict):
                    continue
                defaults = {
                    "importance": 3,
                    "expires_at": "",
                    "needs_confirmation": False,
                    "last_confirmed_at": record.get("updated_at", ""),
                }
                for key, value in defaults.items():
                    if key not in record:
                        record[key] = value
                        touched = True
                if (
                    record.get("status") == "active"
                    and float(record.get("confidence", 1.0)) < 0.65
                ):
                    record["status"] = "pending_confirmation"
                    record["needs_confirmation"] = True
                    touched = True
            if touched:
                _atomic_json(path, records)
                changed += 1
        except Exception as exc:
            logger.warning(f"[migration] memory record skipped {path}: {exc}")
    return changed


def run_migrations() -> dict[str, Any]:
    previous = _schema()
    if previous >= CURRENT_SCHEMA:
        return {"from": previous, "to": CURRENT_SCHEMA, "changed": 0}
    if previous > 0:
        try:
            from .backup_manager import create_backup

            create_backup(scope="global", reason=f"before_schema_{CURRENT_SCHEMA}")
        except Exception as exc:
            logger.warning(f"[migration] pre-migration backup failed: {exc}")
    _migrate_state()
    changed = _migrate_memory_records()
    _atomic_json(
        SCHEMA_PATH,
        {
            "version": CURRENT_SCHEMA,
            "migrated_from": previous,
            "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    logger.info(f"[migration] companion schema {previous} -> {CURRENT_SCHEMA}")
    return {"from": previous, "to": CURRENT_SCHEMA, "changed": changed}


def status() -> dict[str, int]:
    return {"current": _schema(), "latest": CURRENT_SCHEMA}
