"""Read-only macOS presence signals used to avoid poorly timed proactive speech."""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

from loguru import logger

DEFAULT_BLOCKED_APPS = [
    "zoom.us",
    "Microsoft Teams",
    "腾讯会议",
    "Tencent Meeting",
    "WeMeet",
    "FaceTime",
    "Keynote",
    "Microsoft PowerPoint",
]

_cache: tuple[float, dict[str, Any]] | None = None


def _run(args: list[str], timeout: float = 2.0) -> str:
    try:
        result = subprocess.run(
            args,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _frontmost_app() -> str:
    return _run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to get name of first application process whose frontmost is true',
        ]
    )


def _idle_seconds() -> float:
    output = _run(["ioreg", "-c", "IOHIDSystem"])
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', output)
    return int(match.group(1)) / 1_000_000_000 if match else 0.0


def _session_state() -> tuple[bool, bool]:
    try:
        import Quartz

        session = Quartz.CGSessionCopyCurrentDictionary() or {}
        locked = bool(session.get("CGSSessionScreenIsLocked", False))
        on_console = bool(session.get("kCGSessionOnConsoleKey", True))
        return locked, on_console
    except Exception:
        return False, True


def _full_screen(frontmost: str) -> bool:
    try:
        import AppKit
        import Quartz

        screens = AppKit.NSScreen.screens()
        if not screens:
            return False
        max_width = max(float(screen.frame().size.width) for screen in screens)
        max_height = max(float(screen.frame().size.height) for screen in screens)
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        for window in windows[:30]:
            if str(window.get("kCGWindowOwnerName", "")) != frontmost:
                continue
            if int(window.get("kCGWindowLayer", 0)) != 0:
                continue
            bounds = window.get("kCGWindowBounds", {})
            width = float(bounds.get("Width", 0))
            height = float(bounds.get("Height", 0))
            return width >= max_width * 0.92 and height >= max_height * 0.9
    except Exception as exc:
        logger.debug(f"[environment] fullscreen detection unavailable: {exc}")
    return False


def _focus_mode() -> bool:
    values = [
        _run(
            [
                "defaults",
                "-currentHost",
                "read",
                "com.apple.notificationcenterui",
                "doNotDisturb",
            ]
        ),
        _run(
            [
                "defaults",
                "read",
                "com.apple.controlcenter",
                "NSStatusItem Visible FocusModes",
            ]
        ),
    ]
    return any(value.lower() in {"1", "true", "yes"} for value in values)


def signals(force: bool = False) -> dict[str, Any]:
    global _cache
    now = time.monotonic()
    if not force and _cache and now - _cache[0] < 5:
        return dict(_cache[1])
    frontmost = _frontmost_app()
    locked, on_console = _session_state()
    running = _run(["ps", "-axo", "comm="])
    meeting_apps = [
        app for app in DEFAULT_BLOCKED_APPS if app.lower() in running.lower()
    ]
    result = {
        "frontmost_app": frontmost,
        "idle_seconds": round(_idle_seconds(), 1),
        "screen_locked": locked,
        "on_console": on_console,
        "full_screen": _full_screen(frontmost),
        "focus_mode": _focus_mode(),
        "meeting_apps": meeting_apps,
        "microphone_likely_in_use": bool(meeting_apps),
    }
    _cache = (now, result)
    return dict(result)


def evaluate(settings: dict[str, Any]) -> dict[str, Any]:
    environment = settings.get("environment", {})
    current = signals()
    reasons: list[str] = []
    blocked_apps = [
        str(item).lower()
        for item in environment.get("blocked_apps", DEFAULT_BLOCKED_APPS)
    ]
    frontmost = str(current["frontmost_app"]).lower()
    if environment.get("block_meeting_apps", True) and (
        current["meeting_apps"] or any(app in frontmost for app in blocked_apps)
    ):
        reasons.append("meeting_app")
    if environment.get("block_full_screen", True) and current["full_screen"]:
        reasons.append("full_screen")
    if environment.get("block_focus_mode", True) and current["focus_mode"]:
        reasons.append("focus_mode")
    if environment.get("block_when_locked", True) and (
        current["screen_locked"] or not current["on_console"]
    ):
        reasons.append("screen_locked")
    away_seconds = max(60, int(environment.get("away_after_seconds", 900)))
    if (
        environment.get("block_when_away", True)
        and current["idle_seconds"] >= away_seconds
    ):
        reasons.append("user_away")
    if (
        environment.get("block_microphone", True)
        and current["microphone_likely_in_use"]
    ):
        reasons.append("microphone")
    return {"allowed": not reasons, "reasons": sorted(set(reasons)), "signals": current}
