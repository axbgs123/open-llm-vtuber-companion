####
# change from xTTS.py
####

import hashlib
import os
import re
import shutil
from pathlib import Path
import requests
from loguru import logger
from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    def __init__(
        self,
        api_url: str = "http://127.0.0.1:9880/tts",
        text_lang: str = "zh",
        ref_audio_path: str = "",
        prompt_lang: str = "zh",
        prompt_text: str = "",
        text_split_method: str = "cut5",
        batch_size: str = "1",
        media_type: str = "wav",
        streaming_mode: str = "true",
    ):
        self.api_url = api_url
        self.text_lang = text_lang
        self.ref_audio_path = ref_audio_path
        self.prompt_lang = prompt_lang
        self.prompt_text = prompt_text
        self.text_split_method = text_split_method
        self.batch_size = batch_size
        self.media_type = media_type
        self.streaming_mode = streaming_mode

    def _cache_path(self, text: str) -> Path:
        cache_dir = Path.cwd() / "companion_data" / "voice_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        identity = "\0".join(
            (text, self.ref_audio_path, self.prompt_text, self.text_lang, self.prompt_lang)
        )
        return cache_dir / f"{hashlib.sha256(identity.encode()).hexdigest()}.{self.media_type}"

    def generate_audio(self, text, file_name_no_ext=None):
        from ..companion_diagnostics import timer

        file_name = self.generate_cache_file_name(file_name_no_ext, self.media_type)
        cleaned_text = re.sub(r"\[.*?\]", "", text)
        cache_path = self._cache_path(cleaned_text)
        if cache_path.is_file() and cache_path.stat().st_size > 128:
            shutil.copyfile(cache_path, file_name)
            from ..companion_diagnostics import record

            record("voice_synthesis", 0, cached=True)
            return file_name
        from ..runtime_manager import ensure_gpt_sovits_blocking, note_voice_activity

        if not ensure_gpt_sovits_blocking():
            logger.error("GPT-SoVITS could not be started on demand")
            return None
        note_voice_activity()
        # Prepare the data for the POST request
        data = {
            "text": cleaned_text,
            "text_lang": self.text_lang,
            "ref_audio_path": self.ref_audio_path,
            "prompt_lang": self.prompt_lang,
            "prompt_text": self.prompt_text,
            "text_split_method": self.text_split_method,
            "batch_size": self.batch_size,
            "media_type": self.media_type,
            "streaming_mode": self.streaming_mode,
        }

        # Send POST request to the TTS API
        with timer("voice_synthesis") as timing:
            response = requests.get(
                self.api_url, params=data, timeout=120, stream=True
            )
            if response.status_code == 200:
                with open(file_name, "wb") as audio_file:
                    for chunk in response.iter_content(chunk_size=32 * 1024):
                        if chunk:
                            audio_file.write(chunk)
                temp_cache = cache_path.with_suffix(cache_path.suffix + ".tmp")
                shutil.copyfile(file_name, temp_cache)
                os.replace(temp_cache, cache_path)
                return file_name
            timing["ok"] = False
            logger.critical(
                f"Error: Failed to generate audio. Status code: {response.status_code}"
            )
            return None
