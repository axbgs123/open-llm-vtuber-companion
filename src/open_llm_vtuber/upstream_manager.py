"""Safe upstream update checks for the private companion branch."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def status(fetch: bool = False) -> dict[str, Any]:
    remotes = _git("remote", check=False).splitlines()
    if "upstream" not in remotes:
        return {"ok": False, "error": "upstream remote is missing"}
    if fetch:
        result = subprocess.run(
            ["git", "fetch", "upstream", "main"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        if result.returncode:
            return {"ok": False, "error": result.stderr.strip() or "fetch failed"}
    try:
        behind = int(_git("rev-list", "--count", "HEAD..upstream/main") or 0)
        ahead = int(_git("rev-list", "--count", "upstream/main..HEAD") or 0)
        dirty = bool(_git("status", "--porcelain", check=False))
        return {
            "ok": True,
            "behind": behind,
            "ahead": ahead,
            "dirty": dirty,
            "head": _git("rev-parse", "--short", "HEAD"),
            "upstream_head": _git("rev-parse", "--short", "upstream/main"),
            "sync_script": str(ROOT / "sync-upstream.command"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
