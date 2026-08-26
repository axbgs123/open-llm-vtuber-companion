"""Encrypted local backups, incremental snapshots, and scoped restore."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Iterable

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger
import yaml

from .utils.path_safety import is_within

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "backups"
INDEX_PATH = BACKUP_DIR / "index.json"
KEY_PATH = ROOT / "companion_data" / ".backup.key"
KEYRING_DIR = ROOT / "companion_data" / "backup_keys"
RETENTION_PATH = BACKUP_DIR / "retention.json"
MAGIC = b"AICB1\n"
BACKUP_SCHEMA = 1
DEFAULT_RETENTION = {"max_backups": 30, "keep_safety": 10}

_daily_task: asyncio.Task | None = None


def _key() -> bytes:
    if KEY_PATH.is_file():
        return KEY_PATH.read_bytes().strip()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key + b"\n")
    os.chmod(KEY_PATH, 0o600)
    return key


def _index() -> dict[str, Any]:
    try:
        if INDEX_PATH.is_file():
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.warning(f"[backup] index unreadable: {exc}")
    return {"backups": [], "last_hashes": {}, "last_global": ""}


def _save_index(index: dict[str, Any]) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    temp = INDEX_PATH.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, INDEX_PATH)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _allowed_file(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    relative = path.relative_to(ROOT)
    parts = relative.parts
    if any(
        part in {".git", ".venv", "models", "logs", "cache", "backups", "backup_keys"}
        for part in parts
    ):
        return False
    if path.name in {".backup.key", "fts_index.db", "semantic_index.db"}:
        return False
    if path.suffix in {".pyc", ".log"}:
        return False
    return True


def _walk(directory: Path) -> Iterable[Path]:
    if directory.is_dir():
        for path in directory.rglob("*"):
            if _allowed_file(path):
                yield path


def _global_files() -> list[Path]:
    candidates = [
        ROOT / "conf.yaml",
        ROOT / "prompts" / "utils" / "proactive_speak_prompt.txt",
    ]
    files = [path for path in candidates if _allowed_file(path)]
    for directory in (
        ROOT / "characters",
        ROOT / "chat_history",
        ROOT / "companion_data",
    ):
        files.extend(_walk(directory))
    return sorted(set(files))


def _character_yaml(conf_uid: str) -> Path | None:
    candidates = [ROOT / "conf.yaml", *(ROOT / "characters").glob("*.yaml")]
    for path in candidates:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if str((data.get("character_config") or {}).get("conf_uid")) == conf_uid:
                return path
        except Exception:
            continue
    return None


def _character_state(conf_uid: str) -> dict[str, Any]:
    state_path = ROOT / "companion_data" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    return {
        "memory": (state.get("memory") or {}).get(conf_uid),
        "voices": (state.get("voices") or {}).get(conf_uid),
        "active_voice": (state.get("active_voice") or {}).get(conf_uid),
    }


def _character_files(conf_uid: str) -> list[Path]:
    files: list[Path] = []
    character_yaml = _character_yaml(conf_uid)
    if character_yaml:
        files.append(character_yaml)
    for directory in (
        ROOT / "chat_history" / conf_uid,
        ROOT / "companion_data" / "voice_references" / conf_uid,
    ):
        files.extend(_walk(directory))
    return sorted(set(files))


def _write_encrypted(filename: str, zip_bytes: bytes) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / filename
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(MAGIC + Fernet(_key()).encrypt(zip_bytes))
    os.replace(temp, path)
    return path


def _read_encrypted(path: Path) -> bytes:
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("not a companion backup")
    keys = [_key()]
    if KEYRING_DIR.is_dir():
        keys.extend(path.read_bytes().strip() for path in KEYRING_DIR.glob("*.key"))
    for key in keys:
        try:
            return Fernet(key).decrypt(raw[len(MAGIC) :])
        except (InvalidToken, ValueError):
            continue
    raise ValueError("backup key does not match any imported recovery key")


def import_recovery_key(raw: bytes) -> dict[str, str]:
    key = raw.strip()
    try:
        Fernet(key)
    except Exception as exc:
        raise ValueError("invalid recovery key") from exc
    fingerprint = hashlib.sha256(key).hexdigest()[:12]
    if key == _key():
        return {"fingerprint": fingerprint, "status": "current"}
    KEYRING_DIR.mkdir(parents=True, exist_ok=True)
    path = KEYRING_DIR / f"{fingerprint}.key"
    path.write_bytes(key + b"\n")
    os.chmod(path, 0o600)
    return {"fingerprint": fingerprint, "status": "imported"}


def _backup_manifest(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(_read_encrypted(path))) as archive:
        return json.loads(archive.read("manifest.json"))


def create_backup(
    *,
    scope: str = "global",
    conf_uid: str = "",
    incremental: bool = False,
    reason: str = "manual",
) -> dict[str, Any]:
    if scope not in {"global", "character"}:
        raise ValueError("invalid backup scope")
    if scope == "character" and not conf_uid:
        raise ValueError("conf_uid is required")
    index = _index()
    files = _global_files() if scope == "global" else _character_files(conf_uid)
    hashes = {str(path.relative_to(ROOT)): _hash(path) for path in files}
    base = ""
    deleted: list[str] = []
    if scope == "global" and incremental and index.get("last_global"):
        previous = index.get("last_hashes", {})
        files = [
            path
            for path in files
            if previous.get(str(path.relative_to(ROOT)))
            != hashes[str(path.relative_to(ROOT))]
        ]
        deleted = sorted(set(previous) - set(hashes))
        base = str(index.get("last_global") or "")
    created = dt.datetime.now().astimezone()
    stamp = created.strftime("%Y%m%d-%H%M%S-%f")
    uid_suffix = f"-{conf_uid}" if conf_uid else ""
    mode = "incremental" if base else "full"
    filename = f"{stamp}-{scope}{uid_suffix}-{mode}.aicbackup"
    manifest = {
        "schema": BACKUP_SCHEMA,
        "created_at": created.isoformat(timespec="seconds"),
        "scope": scope,
        "conf_uid": conf_uid,
        "mode": mode,
        "base": base,
        "reason": reason,
        "files": hashes,
        "included": [str(path.relative_to(ROOT)) for path in files],
        "deleted": deleted,
    }
    memory = io.BytesIO()
    with zipfile.ZipFile(
        memory, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        for path in files:
            archive.write(path, str(path.relative_to(ROOT)))
        if scope == "character":
            archive.writestr(
                "__character_state__.json",
                json.dumps(_character_state(conf_uid), ensure_ascii=False, indent=2),
            )
    backup_path = _write_encrypted(filename, memory.getvalue())
    record = {
        **{
            key: manifest[key]
            for key in ("created_at", "scope", "conf_uid", "mode", "base", "reason")
        },
        "filename": filename,
        "size": backup_path.stat().st_size,
    }
    index.setdefault("backups", []).append(record)
    if scope == "global":
        index["last_global"] = filename
        index["last_hashes"] = hashes
    _save_index(index)
    if reason == "automatic":
        prune_backups()
    logger.info(f"[backup] created {filename} ({record['size']} bytes)")
    return record


def list_backups() -> list[dict[str, Any]]:
    index = _index()
    existing = []
    for record in index.get("backups", []):
        path = BACKUP_DIR / str(record.get("filename", ""))
        if path.is_file():
            current = dict(record)
            current["size"] = path.stat().st_size
            existing.append(current)
    return sorted(existing, key=lambda item: item.get("created_at", ""), reverse=True)


def preview_backup(filename: str) -> dict[str, Any]:
    chain = _chain(Path(filename).name)
    final = chain[-1][0]
    effective: set[str] = set()
    deleted: set[str] = set()
    try:
        for manifest, archive in chain:
            deleted.update(str(item) for item in manifest.get("deleted", []))
            for name in archive.namelist():
                if name in {"manifest.json", "__character_state__.json"} or name.endswith("/"):
                    continue
                target = (ROOT / name).resolve()
                if not is_within(str(ROOT), str(target)):
                    raise ValueError("backup contains unsafe path")
                effective.add(name)
                deleted.discard(name)
        created = sorted(name for name in effective if not (ROOT / name).is_file())
        overwritten = sorted(name for name in effective if (ROOT / name).is_file())
        removed = sorted(name for name in deleted if (ROOT / name).is_file())
        return {
            "filename": Path(filename).name,
            "scope": final.get("scope"),
            "conf_uid": final.get("conf_uid", ""),
            "created_at": final.get("created_at", ""),
            "chain": [
                *[str(manifest.get("base")) for manifest, _ in chain if manifest.get("base")],
                Path(filename).name,
            ],
            "created": created,
            "overwritten": overwritten,
            "deleted": removed,
            "counts": {"created": len(created), "overwritten": len(overwritten), "deleted": len(removed)},
        }
    finally:
        for _, archive in chain:
            archive.close()


def get_retention() -> dict[str, int]:
    settings = dict(DEFAULT_RETENTION)
    try:
        loaded = json.loads(RETENTION_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            settings.update({key: loaded[key] for key in settings if key in loaded})
    except Exception:
        pass
    settings["max_backups"] = max(5, min(200, int(settings["max_backups"])))
    settings["keep_safety"] = max(1, min(50, int(settings["keep_safety"])))
    return settings


def save_retention(incoming: dict[str, Any]) -> dict[str, int]:
    settings = get_retention()
    settings.update({key: incoming[key] for key in settings if key in incoming})
    settings["max_backups"] = max(5, min(200, int(settings["max_backups"])))
    settings["keep_safety"] = max(1, min(50, int(settings["keep_safety"])))
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    temp = RETENTION_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, RETENTION_PATH)
    return settings


def prune_backups() -> dict[str, Any]:
    settings = get_retention()
    records = list_backups()
    safety = [row for row in records if row.get("reason") not in {"manual", "automatic", "test"}]
    keep_names = {row["filename"] for row in records[: settings["max_backups"]]}
    keep_names.update(row["filename"] for row in safety[: settings["keep_safety"]])
    by_name = {row["filename"]: row for row in records}
    pending = list(keep_names)
    while pending:
        name = pending.pop()
        base = str(by_name.get(name, {}).get("base") or "")
        if base and base not in keep_names:
            keep_names.add(base)
            pending.append(base)
    removed = []
    for row in records:
        if row["filename"] not in keep_names:
            (BACKUP_DIR / row["filename"]).unlink(missing_ok=True)
            removed.append(row["filename"])
    index = _index()
    index["backups"] = [row for row in index.get("backups", []) if row.get("filename") in keep_names]
    _save_index(index)
    return {"removed": removed, "kept": len(keep_names)}


def _chain(
    filename: str, seen: set[str] | None = None
) -> list[tuple[dict[str, Any], zipfile.ZipFile]]:
    seen = seen or set()
    if filename in seen:
        raise ValueError("backup chain cycle")
    seen.add(filename)
    path = BACKUP_DIR / Path(filename).name
    if not path.is_file():
        raise FileNotFoundError(filename)
    payload = io.BytesIO(_read_encrypted(path))
    archive = zipfile.ZipFile(payload)
    manifest = json.loads(archive.read("manifest.json"))
    chain = _chain(manifest["base"], seen) if manifest.get("base") else []
    chain.append((manifest, archive))
    return chain


def _merge_character_state(conf_uid: str, backup_state: dict[str, Any]) -> None:
    state_path = ROOT / "companion_data" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {"memory": {}, "voices": {}, "active_voice": {}}
    for section in ("memory", "voices", "active_voice"):
        state.setdefault(section, {})
        value = backup_state.get(section)
        if section == "voices" and isinstance(value, list):
            for profile in value:
                if isinstance(profile, dict) and profile.get("ref_audio_path"):
                    profile["ref_audio_path"] = str(
                        (
                            ROOT
                            / "companion_data"
                            / "voice_references"
                            / conf_uid
                            / Path(str(profile["ref_audio_path"])).name
                        ).resolve()
                    )
        if value is None:
            state[section].pop(conf_uid, None)
        else:
            state[section][conf_uid] = value
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp = state_path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, state_path)


def restore_backup(filename: str) -> dict[str, Any]:
    chain = _chain(Path(filename).name)
    final_manifest = chain[-1][0]
    scope = final_manifest.get("scope")
    conf_uid = str(final_manifest.get("conf_uid") or "")
    create_backup(
        scope="character" if scope == "character" else "global",
        conf_uid=conf_uid,
        reason="pre_restore",
    )
    restored = 0
    deleted: set[str] = set()
    character_state: dict[str, Any] | None = None
    for manifest, archive in chain:
        deleted.update(str(item) for item in manifest.get("deleted", []))
        for name in archive.namelist():
            if name in {"manifest.json", "__character_state__.json"} or name.endswith(
                "/"
            ):
                continue
            target = (ROOT / name).resolve()
            if not is_within(str(ROOT), str(target)):
                raise ValueError("backup contains unsafe path")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
            deleted.discard(name)
            restored += 1
        if "__character_state__.json" in archive.namelist():
            character_state = json.loads(archive.read("__character_state__.json"))
    for relative in deleted:
        target = (ROOT / relative).resolve()
        if is_within(str(ROOT), str(target)) and target.is_file():
            target.unlink()
    if scope == "character" and character_state is not None:
        _merge_character_state(conf_uid, character_state)
    return {"ok": True, "restored": restored, "scope": scope, "conf_uid": conf_uid}


def create_safety_snapshot(conf_uid: str, reason: str) -> None:
    try:
        create_backup(scope="character", conf_uid=conf_uid, reason=reason)
    except Exception as exc:
        logger.warning(f"[backup] safety snapshot failed: {exc}")


def backup_due() -> bool:
    automatic = [item for item in list_backups() if item.get("reason") == "automatic"]
    if not automatic:
        return True
    try:
        last = dt.datetime.fromisoformat(automatic[0]["created_at"])
        return dt.datetime.now().astimezone() - last >= dt.timedelta(hours=24)
    except Exception:
        return True


async def _daily_loop() -> None:
    while True:
        try:
            if backup_due():
                await asyncio.to_thread(
                    create_backup,
                    scope="global",
                    incremental=True,
                    reason="automatic",
                )
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(f"[backup] automatic backup failed: {exc}")
            await asyncio.sleep(3600)


def start_daily_backup() -> None:
    global _daily_task
    if _daily_task is None or _daily_task.done():
        _daily_task = asyncio.create_task(_daily_loop())


async def stop_daily_backup() -> None:
    global _daily_task
    if _daily_task:
        _daily_task.cancel()
        try:
            await _daily_task
        except asyncio.CancelledError:
            pass
        _daily_task = None
