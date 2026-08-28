"""Client for the bundled, loopback-only CosyVoice3 service."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

import httpx
from loguru import logger

from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    CACHE_VERSION = "cosyvoice3-0.5b-2512-v1"
    EMOTION_ALIASES = {
        "happiness": "happy",
        "joy": "happy",
        "smirk": "happy",
        "excited": "excited",
        "sadness": "sad",
        "sorrow": "sad",
        "anger": "angry",
        "disgust": "disgust",
        "surprise": "surprised",
        "fear": "fear",
        "shy": "shy",
        "calm": "calm",
        "relaxed": "calm",
        "caring": "calm",
    }
    SUPPORTED_EMOTIONS = {
        "neutral",
        "happy",
        "excited",
        "sad",
        "angry",
        "surprised",
        "shy",
        "calm",
        "fear",
        "disgust",
    }

    def __init__(
        self,
        client_url="http://127.0.0.1:50000",
        mode_checkbox_group="零样本声音克隆",
        sft_dropdown="中文女",
        prompt_text="",
        prompt_wav_upload_url="",
        prompt_wav_record_url="",
        instruct_text="",
        seed=0,
        api_name="/tts",
        speed=1.0,
        emotion_strength=0.72,
    ):
        del mode_checkbox_group, sft_dropdown, seed
        self.client_url = str(client_url or "http://127.0.0.1:50000").rstrip("/")
        self.api_name = "/" + str(api_name or "/tts").lstrip("/")
        self.prompt_text = str(prompt_text or "")
        raw_prompt = str(prompt_wav_upload_url or prompt_wav_record_url or "")
        self.prompt_wav = (
            str(Path(raw_prompt).expanduser().resolve()) if raw_prompt else ""
        )
        self.instruct_text = str(instruct_text or "")
        self.speed = max(
            0.85,
            min(1.15, float(speed if speed is not None else 1.0)),
        )
        self.emotion_strength = max(
            0.0,
            min(
                1.0,
                float(emotion_strength if emotion_strength is not None else 0.72),
            ),
        )

    @staticmethod
    def _emotion(actions) -> str:
        expressions = getattr(actions, "expressions", None) or []
        for expression in expressions:
            if isinstance(expression, str) and expression.strip():
                clean = expression.strip().lower()
                canonical = TTSEngine.EMOTION_ALIASES.get(clean, clean)
                if canonical not in TTSEngine.SUPPORTED_EMOTIONS:
                    continue
                if canonical == "neutral":
                    continue
                return canonical
        return "neutral"

    def _cache_path(self, text: str, emotion: str) -> Path:
        cache_dir = Path.cwd() / "companion_data" / "voice_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            reference = Path(self.prompt_wav)
            signature = f"{reference.stat().st_size}:{reference.stat().st_mtime_ns}"
        except Exception:
            signature = "missing"
        identity = "\0".join(
            (
                self.CACHE_VERSION,
                text,
                self.client_url,
                self.api_name,
                self.prompt_wav,
                signature,
                self.prompt_text,
                self.instruct_text,
                emotion,
                str(self.speed),
                str(self.emotion_strength),
            )
        )
        return cache_dir / f"{hashlib.sha256(identity.encode()).hexdigest()}.wav"

    @staticmethod
    def _valid_wav_cache(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 128:
                return False
            with path.open("rb") as cached:
                return cached.read(4) == b"RIFF"
        except OSError:
            return False

    def split_streaming_text(self, text: str) -> list[str]:
        """Split Chinese speech into independently playable natural phrases."""
        cleaned = str(text or "").strip()
        if len(cleaned) <= 26:
            return [cleaned]
        pieces = [
            piece.strip()
            for piece in re.split(r"(?<=[，,；;：:。！？!?])", cleaned)
            if piece.strip()
        ]
        fragments: list[str] = []
        pending = ""
        for piece in pieces:
            if pending and len(pending) + len(piece) > 34:
                fragments.append(pending)
                pending = piece
            else:
                pending += piece
        if pending:
            fragments.append(pending)
        if len(fragments) == 1 and len(fragments[0]) > 44:
            only = fragments[0]
            fragments = [
                only[offset : offset + 30] for offset in range(0, len(only), 30)
            ]
        return fragments[:8] if fragments else [cleaned]

    def generate_audio_with_context(
        self, text: str, actions=None, file_name_no_ext=None
    ) -> str:
        if not self.prompt_wav:
            raise ValueError("CosyVoice requires a reference voice file")
        if not Path(self.prompt_wav).is_file():
            raise FileNotFoundError("CosyVoice reference voice file is missing")
        cleaned_text = re.sub(r"\[.*?\]|【.*?】", "", str(text or "")).strip()
        if not cleaned_text:
            raise ValueError("CosyVoice requires non-empty text")
        emotion = self._emotion(actions)
        target = self.generate_cache_file_name(file_name_no_ext, "wav")
        cache_path = self._cache_path(cleaned_text, emotion)
        if self._valid_wav_cache(cache_path):
            shutil.copyfile(cache_path, target)
            from ..companion_diagnostics import record

            record("voice_synthesis", 0, cached=True)
            return target

        from ..runtime_manager import ensure_cosyvoice_blocking, note_voice_activity

        if not ensure_cosyvoice_blocking():
            raise RuntimeError("CosyVoice could not be started on demand")
        note_voice_activity()
        payload = {
            "text": cleaned_text,
            "prompt_wav": self.prompt_wav,
            "prompt_text": self.prompt_text,
            "emotion": emotion,
            "instruct_text": self.instruct_text,
            "speed": self.speed,
            "emotion_strength": self.emotion_strength,
        }
        from ..companion_diagnostics import timer

        with timer("voice_synthesis") as timing:
            try:
                with httpx.Client(timeout=httpx.Timeout(300, connect=8)) as client:
                    response = client.post(
                        self.client_url + self.api_name,
                        json=payload,
                    )
                    response.raise_for_status()
                if len(response.content) <= 128 or not response.content.startswith(
                    b"RIFF"
                ):
                    raise RuntimeError("CosyVoice returned invalid WAV audio")
            except Exception:
                timing["ok"] = False
                raise
        Path(target).write_bytes(response.content)
        temporary = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(target, temporary)
        os.replace(temporary, cache_path)
        logger.info(
            f"CosyVoice generated {len(response.content)} bytes "
            f"with emotion={emotion}, speed={self.speed}, "
            f"strength={self.emotion_strength}"
        )
        return target

    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        return self.generate_audio_with_context(text, None, file_name_no_ext)
