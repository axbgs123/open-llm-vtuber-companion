"""Client for the bundled, loopback-only CosyVoice3 service."""

from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
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
    ):
        del mode_checkbox_group, sft_dropdown, seed
        self.client_url = str(client_url or "http://127.0.0.1:50000").rstrip("/")
        self.api_name = "/" + str(api_name or "/tts").lstrip("/")
        self.prompt_text = str(prompt_text or "")
        raw_prompt = str(prompt_wav_upload_url or prompt_wav_record_url or "")
        self.prompt_wav = str(Path(raw_prompt).expanduser().resolve()) if raw_prompt else ""
        self.instruct_text = str(instruct_text or "")

    @staticmethod
    def _emotion(actions) -> str:
        expressions = getattr(actions, "expressions", None) or []
        for expression in expressions:
            if isinstance(expression, str) and expression.strip():
                return expression.strip().lower()
        return "neutral"

    def generate_audio_with_context(
        self, text: str, actions=None, file_name_no_ext=None
    ) -> str:
        from ..runtime_manager import ensure_cosyvoice_blocking, note_voice_activity

        if not ensure_cosyvoice_blocking():
            raise RuntimeError("CosyVoice could not be started on demand")
        if not self.prompt_wav:
            raise ValueError("CosyVoice requires a reference voice file")
        note_voice_activity()
        target = self.generate_cache_file_name(file_name_no_ext, "wav")
        payload = {
            "text": text,
            "prompt_wav": self.prompt_wav,
            "prompt_text": self.prompt_text,
            "emotion": self._emotion(actions),
            "instruct_text": self.instruct_text,
            "speed": 1.0,
        }
        with httpx.Client(timeout=300) as client:
            response = client.post(self.client_url + self.api_name, json=payload)
            response.raise_for_status()
        Path(target).write_bytes(response.content)
        logger.info(
            f"CosyVoice generated {len(response.content)} bytes "
            f"with emotion={payload['emotion']}"
        )
        return target

    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        return self.generate_audio_with_context(text, None, file_name_no_ext)
